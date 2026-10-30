"""Sample visible GPU memory across the complete lifetime of a command."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
from threading import Event, Thread
import time


def gpu_snapshot() -> list[dict]:
    cards = []
    for node in sorted(Path("/dev/dri").glob("card[0-9]*")):
        device = Path("/sys/class/drm") / node.name / "device"
        used, total = device / "mem_info_vram_used", device / "mem_info_vram_total"
        if used.exists() and total.exists():
            cards.append({
                "device": node.name, "source": "amdgpu_sysfs",
                "used_bytes": int(used.read_text()), "total_bytes": int(total.read_text()),
            })
    if cards:
        return cards
    if shutil.which("nvidia-smi"):
        result = subprocess.run([
            "nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ], capture_output=True, text=True, check=True, timeout=5)
        for line in result.stdout.splitlines():
            index, name, used, total, utilization = [part.strip() for part in line.split(",")]
            cards.append({
                "device": index, "name": name, "source": "nvidia-smi",
                "used_bytes": int(float(used) * 2**20), "total_bytes": int(float(total) * 2**20),
                "utilization_percent": float(utilization),
            })
    return cards


def observe(command: list[str], directory: Path, interval: float) -> int:
    if interval <= 0:
        raise ValueError("Sample interval must be positive")
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    stop = Event()
    samples, errors = [], []

    def sample(stream):
        stamp = datetime.now(timezone.utc).isoformat()
        try:
            row = {"time": stamp, "seconds": time.monotonic() - started, "gpus": gpu_snapshot()}
            if not row["gpus"]:
                row["error"] = "No supported GPU memory counters found"
                errors.append(row)
            samples.append(row)
        except Exception as error:
            row = {"time": stamp, "seconds": time.monotonic() - started, "error": str(error)}
            errors.append(row)
        stream.write(json.dumps(row) + "\n")
        stream.flush()

    with (directory / "gpu_samples.jsonl").open("x") as stream:
        sample(stream)
        def loop():
            while not stop.wait(interval):
                sample(stream)
        thread = Thread(target=loop, daemon=True)
        thread.start()
        try:
            with (directory / "command.log").open("x") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                print(f"Command log: {directory / 'command.log'}", flush=True)
                try:
                    returncode = process.wait()
                except BaseException:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise
        finally:
            stop.set()
            thread.join()
            sample(stream)
            valid = [row for row in samples if row.get("gpus")]
            peak = max((sum(card["used_bytes"] for card in row["gpus"]) for row in valid), default=None)
            gaps = [b["seconds"] - a["seconds"] for a, b in zip(samples, samples[1:])]
            summary = {
                "command": command, "returncode": locals().get("returncode"),
                "wall_seconds": time.monotonic() - started,
                "requested_sample_interval_seconds": interval,
                "maximum_observed_sample_gap_seconds": max(gaps, default=None),
                "valid_samples": len(valid), "sampling_errors": len(errors),
                "sampled_peak_total_used_bytes": peak,
                "sampled_peak_total_used_gib": peak / 2**30 if peak is not None else None,
                "scope": "device memory across visible GPUs, includes other GPU users; starts before command and ends after exit; sampled peak, not instantaneous peak",
            }
            (directory / "telemetry.json").write_text(json.dumps(summary, indent=2) + "\n")
    return returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command is required after --")
    raise SystemExit(observe(command, args.out, args.interval))


if __name__ == "__main__":
    main()
