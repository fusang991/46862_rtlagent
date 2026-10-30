"""Observe the pinned Python reference harness without changing its execution."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time

_LOCK = threading.RLock()


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


class _SubprocessTrace:
    def __init__(self, original, logs: Path):
        self.original = original
        self.logs = logs

    def __getattr__(self, name):
        return getattr(self.original, name)

    def run(self, argv, *args, **kwargs):
        directory = Path(tempfile.mkdtemp(prefix="reference_", dir=self.logs))
        command = [str(value) for value in argv]
        inputs = []
        # Snapshot file inputs only; ordinary switches/scalars are not files.
        for value in command[1:]:
            path = Path(value)
            if path.is_file():
                destination = directory / path.name
                shutil.copyfile(path, destination)
                inputs.append({"original": str(path), "snapshot": path.name,
                               "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
        started = time.monotonic()
        record = {"argv": command, "cwd": str(kwargs.get("cwd") or os.getcwd()),
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "timeout_s": kwargs.get("timeout"), "inputs": inputs,
                  "returncode": None, "error": None, "seconds": 0,
                  "status": "running"}
        def persist():
            temporary = directory / "command.json.tmp"
            temporary.write_text(json.dumps(record, indent=2) + "\n")
            temporary.replace(directory / "command.json")
        persist()
        stdout, stderr = "", ""
        try:
            result = self.original.run(argv, *args, **kwargs)
            record["returncode"] = result.returncode
            stdout, stderr = _text(result.stdout), _text(result.stderr)
            if result.returncode:
                record["error"] = "process_exited_nonzero"
            return result
        except BaseException as error:
            record["error"] = f"{type(error).__name__}: {error}"
            stdout, stderr = _text(getattr(error, "stdout", None)), _text(getattr(error, "stderr", None))
            raise
        finally:
            record["seconds"] = time.monotonic() - started
            record["status"] = "failed" if record["error"] else "completed"
            (directory / "stdout.log").write_text(stdout)
            (directory / "stderr.log").write_text(stderr)
            persist()


@contextmanager
def trace_reference_tools(logs: Path):
    """Replace only this upstream module's subprocess reference, not stdlib state."""
    logs.mkdir(parents=True, exist_ok=True)
    upstream = importlib.import_module("chipmate.cross_verify")
    with _LOCK:
        original = upstream.subprocess
        upstream.subprocess = _SubprocessTrace(original, logs)
        try:
            yield
        finally:
            upstream.subprocess = original
