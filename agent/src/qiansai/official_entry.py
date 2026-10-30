"""Bound a single development workflow to the official wall-clock contract.

Model services are started separately. The worker receives only the task prompt;
the official judge and its reference files run outside this entry.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def descendant_pids(pid: int) -> set[int]:
    """Include children spawned from non-leader threads and separate sessions."""
    found, pending = set(), [pid]
    while pending:
        parent = pending.pop()
        for task in Path(f"/proc/{parent}/task").glob("*/children"):
            try:
                children = {int(value) for value in task.read_text().split()}
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            new = children - found - {pid}
            found.update(new)
            pending.extend(new)
    return found


def terminate_tree(process: subprocess.Popen) -> list[int]:
    """Stop our worker and its tool sessions without touching inference servers."""
    pids = descendant_pids(process.pid) | {process.pid}
    groups = set()
    for pid in pids:
        try:
            group = os.getpgid(pid)
            if group != os.getpgrp():
                groups.add(group)
        except ProcessLookupError:
            pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for group in groups:
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                pass
        if sig == signal.SIGTERM:
            time.sleep(0.1)
    process.wait()
    return sorted(pids)


def supervise(argv: list[str], log: Path, timeout_s: float, *, env=None, cwd=None) -> dict:
    if timeout_s <= 0:
        raise ValueError("Workflow timeout must be positive")
    started = time.monotonic()
    timestamp = datetime.now(timezone.utc).isoformat()
    timed_out, stopped = False, []
    with log.open("x") as stream:
        process = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=env, cwd=cwd)
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            stopped = terminate_tree(process)
        except BaseException:
            terminate_tree(process)
            raise
    return {"argv": argv, "cwd": str(Path(cwd).resolve()) if cwd is not None else str(Path.cwd()),
            "started_at": timestamp,
            "seconds": time.monotonic() - started, "returncode": process.returncode,
            "timed_out": timed_out, "stopped_pids": stopped}


def collect_trace(work: Path) -> list[dict]:
    """Keep partial SDK/tool evidence even when the worker misses its deadline."""
    trace = []
    for pattern, kind in (("calls/*/batch_*/requests/request_*.json", "llm"),
                          ("tools/simulation_*/*.json", "simulator"),
                          ("tools/reference_*/command.json", "python")):
        for path in work.glob(pattern):
            artifact = str(path.relative_to(work.parent))
            try:
                row = json.loads(path.read_text())
                datetime.fromisoformat(row["started_at"])
                row["seconds"]
                tool = "llm" if kind == "llm" else row["argv"][0]
                row = {**row, "tool": tool, "artifact": artifact}
                if kind == "llm":
                    row["role"] = path.relative_to(work).parts[1]
            except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
                # A hard deadline can interrupt a JSON write; retain that fact.
                row = {"tool": "trace_read_error", "artifact": artifact,
                       "started_at": datetime.now(timezone.utc).isoformat(),
                       "seconds": 0, "error": str(error)}
            trace.append(row)
    for row in trace:
        row["ts"] = datetime.fromisoformat(row["started_at"]).timestamp()
        row["sec"] = row["seconds"]
        if row.get("status") in {"starting", "running"}:
            row["incomplete"] = True
            row["sec"] = max(0, time.time() - row["ts"])
            row["timing_scope"] = "observed until worker stop; tool completion unconfirmed"
        if row["tool"] == "llm":
            usage = row.get("response", {}).get("usage") or {}
            row["tokens_in"] = usage.get("prompt_tokens")
            row["tokens_out"] = usage.get("completion_tokens")
    return sorted(trace, key=lambda row: row["started_at"])


def run_entry(task: Path, out: Path, serving: Path, config: Path, deadline_s: float) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    work = out / "workflow"
    if work.exists() or (out / "solution.v").exists() or (out / "trace.jsonl").exists():
        raise FileExistsError(f"Refusing to overwrite workflow artifacts: {out}")
    argv = [sys.executable, "-m", "qiansai.official_task", str(task), str(work),
            "--serving", str(serving), "--config", str(config),
            "--deadline-s", str(deadline_s)]
    # Leave time to reap tools and publish the result inside the caller's budget.
    worker_budget = deadline_s - min(2, deadline_s * 0.1)
    result = supervise(argv, out / "workflow.log", worker_budget)
    solution = work / "solution.v"
    code = solution.read_text() if solution.exists() else ""
    # The official contract represents an unsolved task with an empty artifact.
    (out / "solution.v").write_text(code)
    trace = collect_trace(work)
    trace.append({"tool": "workflow", "ts": datetime.fromisoformat(result["started_at"]).timestamp(),
                  "sec": result["seconds"], **result})
    with (out / "trace.jsonl").open("x") as stream:
        for row in trace:
            stream.write(json.dumps(row) + "\n")
    result["produced_rtl"] = bool(code.strip())
    (out / "entry.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--serving", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--deadline-s", type=float, default=900)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    result = run_entry(args.task, args.out, args.serving, args.config, args.deadline_s)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
