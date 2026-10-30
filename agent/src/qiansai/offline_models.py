"""Restore pinned model artifacts from a verified, possibly split tar.gz archive."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class PartsReader(io.RawIOBase):
    def __init__(self, paths: list[Path]):
        super().__init__()
        self.paths = iter(paths)
        self.current = None

    def readable(self):
        return True

    def readinto(self, buffer):
        while True:
            if self.current is None:
                path = next(self.paths, None)
                if path is None:
                    return 0
                self.current = path.open("rb")
            size = self.current.readinto(buffer)
            if size:
                return size
            self.current.close()
            self.current = None

    def close(self):
        if self.current is not None:
            self.current.close()
        super().close()


def verify_parts(directory: Path, manifest: dict) -> list[Path]:
    total, digest, paths = 0, hashlib.sha256(), []
    seen = set()
    for part in manifest["parts"]:
        name = part["name"]
        if not re.fullmatch(r"part_\d{5}(?:_\d{2})?\.bin", name) or name in seen:
            raise ValueError("Invalid or duplicate archive part name")
        seen.add(name)
        path = directory / name
        if path.is_symlink() or path.stat().st_size != part["bytes"]:
            raise ValueError(f"Invalid archive part size/type: {name}")
        current = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
                current.update(block)
                total += len(block)
        if current.hexdigest() != part["sha256"]:
            raise ValueError(f"Archive part hash mismatch: {name}")
        paths.append(path)
    identity = manifest["identity"]
    if total != identity["source_bytes"] or digest.hexdigest() != identity["source_sha256"]:
        raise ValueError("Full archive hash/size mismatch")
    return paths


def restore_models(parts: Path, manifest: dict, expected: dict, destination: Path) -> dict:
    """Verify compressed bytes before writing a new model directory; never overwrite."""
    paths = verify_parts(parts, manifest)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite model directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    minimum = sum(m["weight_bytes"] for m in expected["models"].values()) + 128 * 1024 * 1024
    if shutil.disk_usage(destination.parent).free < minimum:
        raise OSError("Insufficient persistent space for weights and model metadata")
    directories = {m["directory"] for m in expected["models"].values()}
    for name in directories:
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError("Invalid model directory name")
    destination.mkdir()
    extracted = []
    with io.BufferedReader(PartsReader(paths), buffer_size=8 * 1024 * 1024) as source:
        with tarfile.open(fileobj=source, mode="r|gz") as archive:
            for member in archive:
                if member.name == "qiansai/model/manifest.json":
                    relative = PurePosixPath("model_manifest.archived.json")
                elif member.name == "qiansai/model/weights/download_manifest.json":
                    relative = PurePosixPath("download_manifest.archived.json")
                elif member.name.startswith("qiansai/model/weights/"):
                    relative = PurePosixPath(member.name).relative_to("qiansai/model/weights")
                    if not relative.parts or relative.parts[0] not in directories:
                        continue
                    if ".cache" in relative.parts or member.isdir():
                        continue
                else:
                    continue
                if relative.is_absolute() or ".." in relative.parts or not member.isfile():
                    raise ValueError(f"Invalid model archive member: {member.name}")
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as input_stream, target.open("xb") as output:
                    shutil.copyfileobj(input_stream, output, length=8 * 1024 * 1024)
                if target.stat().st_size != member.size:
                    raise ValueError(f"Incomplete restored file: {relative}")
                extracted.append(str(relative))
    archived = json.loads((destination / "download_manifest.archived.json").read_text())
    declared = json.loads((destination / "model_manifest.archived.json").read_text())
    records = {}
    for role, model in expected["models"].items():
        identity = archived[role]
        if any(identity[key] != model[key] or declared["models"][role][key] != model[key]
               for key in ("repo_id", "revision")):
            raise ValueError(f"Archived identity mismatch: {role}")
        record = {}
        for name, expected_hash in identity["files"].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or ".cache" in relative.parts:
                raise ValueError("Invalid filename in model identity record")
            path = destination / model["directory"] / name
            actual = digest_file(path)
            if actual != expected_hash:
                raise ValueError(f"Model file hash mismatch: {role}/{name}")
            record[name] = {"sha256": actual, "bytes": path.stat().st_size}
        weight = destination / model["directory"] / "model.safetensors"
        if (record.get("model.safetensors", {}).get("sha256") != model["weight_sha256"]
                or weight.stat().st_size != model["weight_bytes"]):
            raise ValueError(f"Pinned weight mismatch: {role}")
        records[role] = {"repo_id": model["repo_id"], "revision": model["revision"], "files": record}
    # Publish the standard identity record only after every stored file passes.
    (destination / "download_manifest.json").write_bytes((destination / "download_manifest.archived.json").read_bytes())
    result = {"verified_at": datetime.now(timezone.utc).isoformat(),
              "archive_sha256": manifest["identity"]["source_sha256"],
              "archive_bytes": manifest["identity"]["source_bytes"],
              "models": records, "extracted": extracted,
              "stored_file_bytes": sum(x["bytes"] for m in records.values() for x in m["files"].values())}
    (destination / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", type=Path, required=True)
    parser.add_argument("--parts-manifest", type=Path, required=True)
    parser.add_argument("--models-manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    result = restore_models(args.parts, json.loads(args.parts_manifest.read_text()),
                            json.loads(args.models_manifest.read_text()), args.destination)
    print(json.dumps({"destination": str(args.destination), "archive_sha256": result["archive_sha256"],
                      "models_verified": list(result["models"]), "bytes": result["stored_file_bytes"]}), flush=True)


if __name__ == "__main__":
    main()
