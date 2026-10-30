"""Build shareable per-case scores and explicitly scoped token-rate summaries."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .request_trace import usage_summary


def report(directory: Path) -> dict:
    metrics = json.loads((directory / "metrics.json").read_text())
    rows, records = [], []
    model_seconds = 0.0
    for task in metrics["tasks"]:
        task_id = task["task_id"]
        task_dir = directory / "tasks" / task_id
        calls = [json.loads(path.read_text()) for path in sorted(task_dir.glob("calls/*/batch_*/requests/request_*.json"))]
        records.extend(calls)
        batches = [json.loads(path.read_text()) for path in task_dir.glob("calls/*/batch_*/response.json")]
        batch_seconds = sum(batch["seconds"] for batch in batches)
        model_seconds += batch_seconds
        generation_seconds = task.get("generation_seconds")
        tokens = usage_summary(calls, generation_seconds or 0) if calls else task.get("tokens") or {}
        evaluation_path = task_dir / "evaluation.json"
        evaluation = json.loads(evaluation_path.read_text()) if evaluation_path.exists() else {}
        row = {
            "task_id": task_id, "status": task["status"], "compile": task.get("compile"),
            "simulation": task.get("simulation"),
            "reason": evaluation.get("simulation", {}).get("reason", task.get("reason", task.get("error"))),
            "generation_seconds": generation_seconds, "total_case_seconds": task.get("seconds"),
            "prompt_tokens": tokens.get("prompt_tokens"), "completion_tokens": tokens.get("completion_tokens"),
            "output_tokens_per_generation_second": tokens.get("output_tokens_per_second"),
            "cross_verification_match_rate": task.get("cross_verification_match_rate"),
            "evaluation_log_directory": str((task_dir / "evaluation").relative_to(directory)) if evaluation else None,
        }
        rows.append(row)
    # Keep unexecuted cases in the table, even when a service failed before task 1.
    known = {row["task_id"] for row in rows}
    for line in (directory / "inputs.jsonl").read_text().splitlines():
        task_id = json.loads(line)["task_id"]
        if task_id not in known:
            rows.append({"task_id": task_id, "status": "NOT_RUN", "reason": "missing_result"})
            known.add(task_id)
    count = metrics["selected"]
    passed = metrics["counts"]["PASS"]
    generation_seconds = metrics.get("generation_request_seconds", sum(task.get("generation_seconds") or 0 for task in metrics["tasks"]))
    summary = {
        "selected": count, "counts": metrics["counts"], "not_run": metrics.get("not_run", metrics["counts"].get("NOT_RUN", 0)),
        "pass_fraction": passed / count if count else None,
        "pass_percent": 100 * passed / count if count else None,
        "score_scope": "raw independent public-set pass fraction; not official competition score or paper pass@k",
        "end_to_end_run_seconds": metrics.get("seconds"),
        "generation_seconds": generation_seconds,
        "generation_tokens": usage_summary(records, generation_seconds) if records else metrics.get("tokens"),
        "model_batch_tokens": usage_summary(records, model_seconds) if records else None,
        "model_batch_scope": "sum of parallel generate batch wall times; includes prefill, waiting and SDK retry time; excludes between-batch workflow and evaluator work",
        "tasks": rows,
    }
    with (directory / "performance.json").open("x") as output:
        json.dump(summary, output, indent=2, ensure_ascii=False)
        output.write("\n")
    columns = ["task_id", "status", "compile", "simulation", "reason", "generation_seconds", "total_case_seconds", "prompt_tokens", "completion_tokens", "output_tokens_per_generation_second", "cross_verification_match_rate", "evaluation_log_directory"]
    with (directory / "cases.csv").open("x", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    report(args.directory)


if __name__ == "__main__":
    main()
