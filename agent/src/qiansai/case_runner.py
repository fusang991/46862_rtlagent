"""One-command testcase selection, original ChipMATE generation and evaluation."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
import uuid
from zoneinfo import ZoneInfo

import yaml
from chipmate.backends import Backend, make_backend
from chipmate.inference import run_problem

from .datasets import prepare_tasks, sha256_file
from .evaluation import evaluate
from .services import ModelServices
from .request_trace import ChatTrace, usage_summary

ROOT = Path(__file__).resolve().parents[2]


def now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def write_json(path: Path, data) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def command_output(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class TracedBackend(Backend):
    """Record generation batches while leaving upstream prompts and calls intact."""

    def __init__(self, backend: Backend, directory: Path):
        self.backend = backend
        self.directory = directory
        self.directory.mkdir(parents=True)
        self.batch = 0

    def generate(self, prompt, n=1, temperature=0.6, max_tokens=4096):
        self.batch += 1
        call = self.directory / f"batch_{self.batch:02d}"
        call.mkdir()
        write_json(call / "request.json", {
            "started_at": now(), "prompt": prompt, "n": n,
            "temperature": temperature, "max_tokens": max_tokens,
        })
        started = time.monotonic()
        trace = None
        try:
            client = getattr(self.backend, "_client", None)
            if client is not None and hasattr(client, "chat"):
                with ChatTrace(client.chat.completions, call / "requests") as trace:
                    outputs = self.backend.generate(prompt, n, temperature, max_tokens)
            else:
                outputs = self.backend.generate(prompt, n, temperature, max_tokens)
        except Exception as error:
            write_json(call / "error.json", {
                "error": str(error), "seconds": time.monotonic() - started,
            })
            raise
        seconds = time.monotonic() - started
        write_json(call / "response.json", {
            "outputs": outputs, "seconds": seconds,
            "tokens": usage_summary(trace.records if trace else [], seconds),
        })
        return outputs


def select_tasks(rows: list[dict], cases: list[str] | None, limit: int | None) -> list[dict]:
    if limit is not None and limit < 1:
        raise ValueError("--limit must be positive")
    if cases:
        if len(set(cases)) != len(cases):
            raise ValueError("Duplicate --case")
        by_id = {row["task_id"]: row for row in rows}
        unknown = set(cases) - by_id.keys()
        if unknown:
            raise ValueError(f"Unknown testcase: {', '.join(sorted(unknown))}; use --list-cases")
        rows = [by_id[case] for case in cases]
    return rows if limit is None else rows[:limit]


def arguments():
    parser = argparse.ArgumentParser(
        description="Run ChipMATE V-4B + P-4B and show independent testcase results",
        epilog="Raw upstream JSONL interface: run.sh --input INPUT --out OUTPUT [--limit N]",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--case", action="append", help="Exact testcase ID; may be repeated")
    selection.add_argument("--suite", choices=["verilogeval-v2"])
    selection.add_argument("--list-cases", action="store_true")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/verilog_eval/dataset_spec-to-rtl")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--run-dir", type=Path, help="New output directory; existing paths are refused")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/chipmate_4b.yaml")
    parser.add_argument("--serving", type=Path, default=ROOT / "configs/serving.yaml")
    parser.add_argument("--model-root", type=Path, default=Path(os.environ.get("MODEL_ROOT", ROOT / "model/weights")))
    parser.add_argument("--no-start", action="store_true", help="Require existing model services")
    parser.add_argument("--startup-timeout", type=float, default=900)
    parser.add_argument("--eval-timeout", type=float, default=30)
    return parser, parser.parse_args()


def run(args) -> int:
    rows = prepare_tasks(args.dataset)
    if args.list_cases:
        print("\n".join(row["task_id"] for row in rows))
        return 0
    rows = select_tasks(rows, args.case, args.limit)
    if not rows:
        raise ValueError("No testcases selected")
    if args.startup_timeout <= 0 or args.eval_timeout <= 0:
        raise ValueError("Timeouts must be positive")
    workflow = yaml.safe_load(args.config.read_text())
    serving = yaml.safe_load(args.serving.read_text())
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    directory = args.run_dir or ROOT / "runs" / f"{stamp}_chipmate_{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    directory = directory.resolve()
    logs = directory / "logs"
    logs.mkdir()
    with (directory / "inputs.jsonl").open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    (directory / "config.yaml").write_text(yaml.safe_dump({"workflow": workflow, "serving": serving}))
    downloaded_manifest = args.model_root / "download_manifest.json"
    manifest = {
        "started_at": now(), "argv": sys.argv[1:], "task_ids": [row["task_id"] for row in rows],
        "git_commit": command_output(["git", "rev-parse", "HEAD"]),
        "git_status": command_output(["git", "status", "--short"]),
        "submodules": command_output(["git", "submodule", "status"]),
        "models_configured": json.loads((ROOT / "model/manifest.json").read_text()),
        "downloaded_model_files": (
            json.loads(downloaded_manifest.read_text()) if downloaded_manifest.is_file() else None
        ),
        "input_sha256": sha256_file(directory / "inputs.jsonl"),
        "source_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in sorted((ROOT / "src/qiansai").glob("*.py"))
        },
        "gpu_before": command_output([
            "nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv",
        ]),
        "trace_scope": "unchanged upstream generate() and SDK create() requests/responses/usage; SDK HTTP retries are internal",
        "sampling_unit": "one full upstream workflow per testcase; not a pass@k estimate",
    }
    write_json(directory / "manifest.json", manifest)
    results = []
    started = time.monotonic()
    run_error = None
    print(f"Run directory: {directory}", flush=True)
    try:
        with ModelServices(
            serving, args.model_root, logs, autostart=not args.no_start,
            startup_timeout=args.startup_timeout,
        ) as services, (directory / "outputs.jsonl").open("x", encoding="utf-8") as outputs:
            write_json(directory / "services.json", services.info)
            for row in rows:
                services.check()
                task_id = row["task_id"]
                task_dir = directory / "tasks" / task_id
                task_dir.mkdir(parents=True)
                task_started = time.monotonic()
                print(f"Generating {task_id} with V-4B + P-4B...", flush=True)
                try:
                    backends = {
                        role: TracedBackend(make_backend(
                            provider="openai-compat", model=serving[role]["model"],
                            base_url=serving[role]["base_url"], api_key="local",
                        ), task_dir / "calls" / role)
                        for role in ("v", "p")
                    }
                    generated = run_problem(
                        **row, v_backend=backends["v"], p_backend=backends["p"],
                        **{key: workflow[key] for key in (
                            "n", "max_turns", "num_verify_tests", "temperature", "seed",
                        )},
                    ).to_dict()
                    generation_seconds = time.monotonic() - task_started
                    write_json(task_dir / "generation.json", generated)
                    rtl = task_dir / "TopModule.sv"
                    rtl.write_text(generated["verilog"], encoding="utf-8")
                    (task_dir / "reference_model.py").write_text(generated["python"], encoding="utf-8")
                    evaluation = evaluate(
                        rtl, args.dataset / f"{task_id}_ref.sv",
                        args.dataset / f"{task_id}_test.sv", task_dir / "evaluation",
                        timeout=args.eval_timeout,
                    )
                    write_json(task_dir / "evaluation.json", evaluation)
                    result = {
                        "task_id": task_id, "status": evaluation["status"],
                        "cross_verification_match_rate": generated["best_match_rate"],
                        "compile": evaluation["compile"]["status"],
                        "simulation": evaluation["simulation"]["status"],
                        "generation_seconds": generation_seconds,
                        "seconds": time.monotonic() - task_started,
                        "rtl": str(rtl.relative_to(directory)),
                        "evaluation": str((task_dir / "evaluation.json").relative_to(directory)),
                    }
                    outputs.write(json.dumps({**generated, "independent_evaluation": evaluation}) + "\n")
                except Exception as error:
                    (task_dir / "error.log").write_text(traceback.format_exc())
                    result = {
                        "task_id": task_id, "status": "ERROR", "error": str(error),
                        "compile": "NOT_RUN", "simulation": "NOT_RUN",
                        "seconds": time.monotonic() - task_started,
                    }
                    outputs.write(json.dumps(result) + "\n")
                outputs.flush()
                results.append(result)
                print(
                    f"{task_id}: {result['status']} | compile={result['compile']} "
                    f"| simulation={result['simulation']} | {result['seconds']:.1f}s",
                    flush=True,
                )
                if result.get("rtl"):
                    print(f"RTL: {directory / result['rtl']}", flush=True)
                if result.get("error"):
                    print(f"Error: {result['error']}", file=sys.stderr, flush=True)
    except (Exception, KeyboardInterrupt) as error:
        run_error = str(error) or "Interrupted"
        (logs / "error.log").write_text(traceback.format_exc())
        print(f"Run error: {run_error}", file=sys.stderr, flush=True)
    counts = {status: sum(row["status"] == status for row in results) for status in ("PASS", "FAIL", "ERROR")}
    not_run = len(rows) - len(results)
    status = "ERROR" if run_error or counts["ERROR"] or not_run else ("FAIL" if counts["FAIL"] else "PASS")
    write_json(directory / "metrics.json", {
        "status": status, "finished_at": now(), "seconds": time.monotonic() - started,
        "selected": len(rows), "completed": len(results), "counts": counts,
        "not_run": not_run, "error": run_error, "tasks": results,
    })
    from .run_report import report
    report(directory)
    print(
        f"Result: {status} | PASS {counts['PASS']}/{len(rows)} | FAIL {counts['FAIL']} "
        f"| ERROR {counts['ERROR']} | NOT_RUN {not_run}\nReport: {directory / 'metrics.json'}",
        flush=True,
    )
    return 2 if status == "ERROR" else (1 if status == "FAIL" else 0)


def main():
    parser, args = arguments()
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        raise SystemExit(run(args))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f"Run error: {error}\n")


if __name__ == "__main__":
    main()
