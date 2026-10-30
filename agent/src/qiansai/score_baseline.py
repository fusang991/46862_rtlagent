"""Score saved single-model completions with the same independent evaluator."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import time

from .case_runner import ROOT, command_output, now, write_json
from .datasets import prepare_tasks, read_tasks, sha256_file
from .evaluation import evaluate
from .request_trace import usage_summary


def score(generations: Path, inputs: Path, dataset: Path, directory: Path, timeout: float = 30) -> dict:
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    expected = read_tasks(inputs)
    ids = [row["task_id"] for row in expected]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Expected input must have unique task IDs and at least one task")
    official = {row["task_id"] for row in prepare_tasks(dataset)}
    if not set(ids) <= official:
        raise ValueError("Expected input contains tasks outside the official dataset")
    generated = [json.loads(line) for line in generations.read_text().splitlines() if line.strip()]
    by_id = {row["task_id"]: row for row in generated}
    if len(by_id) != len(generated) or not set(by_id) <= set(ids):
        raise ValueError("Generation output has duplicate or unexpected task IDs")
    directory.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(generations, directory / "generations.jsonl")
    shutil.copyfile(inputs, directory / "inputs.jsonl")
    write_json(directory / "manifest.json", {
        "started_at": now(), "sampling_unit": "one direct generation per testcase; not paper pass@k",
        "generation_sha256": sha256_file(generations), "input_sha256": sha256_file(inputs),
        "git_commit": command_output(["git", "rev-parse", "HEAD"]),
        "git_status": command_output(["git", "status", "--short"]),
        "submodules": command_output(["git", "submodule", "status"]),
        "iverilog_version": command_output(["iverilog", "-V"]),
        "eval_timeout_seconds": timeout,
        "source_sha256": {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted((ROOT / "src/qiansai").glob("*.py"))},
    })
    started = time.monotonic()
    results = []
    with (directory / "results.jsonl").open("x") as output:
        for task_id in ids:
            task_dir = directory / "tasks" / task_id
            task_dir.mkdir(parents=True)
            row = by_id.get(task_id)
            result = {"task_id": task_id, "status": "NOT_RUN", "compile": "NOT_RUN", "simulation": "NOT_RUN"}
            if row is None:
                result["reason"] = "missing_generation"
            elif row.get("error"):
                result.update(status="ERROR", reason="generation_error", error=row["error"])
                (task_dir / "error.log").write_text(row["error"] + "\n")
            else:
                rtl = task_dir / "TopModule.sv"
                rtl.write_text(row.get("verilog") or "", encoding="utf-8")
                try:
                    evaluation = evaluate(
                        rtl, dataset / f"{task_id}_ref.sv", dataset / f"{task_id}_test.sv",
                        task_dir / "evaluation", timeout=timeout,
                    )
                    write_json(task_dir / "evaluation.json", evaluation)
                    result.update(
                        status=evaluation["status"], compile=evaluation["compile"]["status"],
                        simulation=evaluation["simulation"]["status"],
                        reason=evaluation["simulation"].get("reason"),
                        generation_seconds=row.get("wall_seconds"), tokens=row.get("tokens"),
                    )
                except Exception as error:
                    result.update(status="ERROR", reason="evaluation_error", error=str(error))
                    (task_dir / "error.log").write_text(str(error) + "\n")
            write_json(task_dir / "result.json", result)
            output.write(json.dumps(result) + "\n")
            output.flush()
            results.append(result)
            print(f"{task_id}: {result['status']} | compile={result['compile']} | simulation={result['simulation']}", flush=True)
    counts = {status: sum(row["status"] == status for row in results) for status in ("PASS", "FAIL", "ERROR", "NOT_RUN")}
    generation_seconds = sum(row.get("wall_seconds", 0) for row in generated)
    metrics = {
        "finished_at": now(), "selected": len(ids), "counts": counts,
        "pass_fraction": counts["PASS"] / len(ids), "pass_percent": 100 * counts["PASS"] / len(ids),
        "status": "ERROR" if counts["ERROR"] or counts["NOT_RUN"] else ("FAIL" if counts["FAIL"] else "PASS"),
        "score_scope": "raw independent public-set pass fraction; not official competition score",
        "generation_request_seconds": generation_seconds, "evaluation_seconds": time.monotonic() - started,
        "tokens": usage_summary(generated, generation_seconds),
        "reason_counts": dict(Counter(row.get("reason", "unknown") for row in results if row["status"] != "PASS")),
        "tasks": results,
    }
    write_json(directory / "metrics.json", metrics)
    from .run_report import report
    report(directory)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generations", required=True, type=Path)
    parser.add_argument("--inputs", required=True, type=Path, help="Exact selected generation inputs, including any missing tasks")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/verilog_eval/dataset_spec-to-rtl")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--eval-timeout", type=float, default=30)
    args = parser.parse_args()
    result = score(args.generations, args.inputs, args.dataset, args.run_dir, args.eval_timeout)
    raise SystemExit(2 if result["status"] == "ERROR" else int(result["status"] != "PASS"))


if __name__ == "__main__":
    main()
