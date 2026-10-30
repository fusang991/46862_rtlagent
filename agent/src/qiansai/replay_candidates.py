"""CPU replay of recorded candidates; this is not fresh model inference.

Changed selections receive a local Icarus functional check. Only identical RTL
may reuse an existing official judgement; changed RTL still needs Vivado grading.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib
import json
from pathlib import Path
import time
import traceback

from .candidate_policy import run_all_pairs
from .evaluation import evaluate
from .reference_trace import trace_reference_tools
from .verification_policy import verification_policy


class RecordedBackend:
    def __init__(self, batches: list[Path]):
        self.batches = batches
        self.calls = []

    def generate(self, prompt, n, temperature):
        directory = self.batches[len(self.calls)]
        recorded = json.loads((directory / "request.json").read_text())
        outputs = json.loads((directory / "response.json").read_text())["outputs"]
        if len(outputs) != n:
            raise ValueError("Recorded candidate count differs from replay configuration")
        self.calls.append({"recorded_batch": str(directory), "n": n, "temperature": temperature,
                           "same_feedback_prompt": recorded["prompt"] == prompt,
                           "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()})
        return outputs


@contextmanager
def strict_cpu_simulator():
    upstream = importlib.import_module("chipmate.cross_verify")
    original = upstream.simulate_verilog
    def checked(*args, **kwargs):
        rows, error = original(*args, **kwargs)
        if error:
            return rows, error
        outputs, stimuli = args[2], args[3]
        if len(rows) != len(stimuli) or any(type(row.get(name)) is not int
                                           for row in rows for name, _ in outputs):
            return None, "sv_incomplete_or_unknown_outputs"
        return rows, None
    upstream.simulate_verilog = checked
    try:
        yield
    finally:
        upstream.simulate_verilog = original


def replay(source: Path, out: Path, dataset: Path, policy: str) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    manifest = {"scope": "CPU candidate replay, zero new model calls; changed selections need official confirmation",
                "source": str(source.resolve()), "verification_policy": policy,
                "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (Path(__file__), Path(__file__).with_name("candidate_policy.py"))}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    started, records = time.monotonic(), []
    for work in sorted((source / "selftest/agent").glob("*/s0/workflow")):
        task_id = work.parent.parent.name
        destination = out / task_id
        destination.mkdir()
        row = {"task_id": task_id, "model_calls": 0}
        try:
            data = json.loads((work / "input.json").read_text())
            config = json.loads((work / "config.json").read_text())["workflow"]
            batches = {role: [p.parent for p in sorted((work / "calls" / role).glob("batch_*/response.json"))]
                       for role in ("v", "p")}
            turns = min(len(batches["v"]), len(batches["p"]))
            if not turns:
                raise ValueError("No complete recorded generation batches")
            backends = {role: RecordedBackend(batches[role]) for role in ("v", "p")}
            with (strict_cpu_simulator(), trace_reference_tools(destination / "tools"),
                  verification_policy(policy)):
                result = run_all_pairs(task_id=task_id, question=data["prompt"],
                    ref_sv=data.get("interface_stub") or data["interface_from_prompt"], v_backend=backends["v"], p_backend=backends["p"],
                    n=config["n"], max_turns=turns, num_verify_tests=config["num_verify_tests"],
                    temperature=config["temperature"], seed=config["seed"]).to_dict()
            # Commit the selection before consulting any independent evaluation.
            candidate = destination / "solution.v"
            candidate.write_text(result["verilog"])
            (destination / "selection.json").write_text(json.dumps(result, indent=2) + "\n")
            selected_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
            original_hash = hashlib.sha256((work.parent / "solution.v").read_bytes()).hexdigest()
            row.update(status="selected", same_rtl=selected_hash == original_hash,
                       selected_sha256=selected_hash, original_sha256=original_hash,
                       self_agreement=result["best_match_rate"], turns_used=result["turns_used"],
                       replay_calls={role: backend.calls for role, backend in backends.items()})
            grade = source / "selftest/results" / f"agent.{task_id}.s0.json"
            if grade.exists():
                row["original_official"] = json.loads(grade.read_text())
                if row["same_rtl"]:
                    row["official_grade_reused_for_identical_bytes"] = row["original_official"]
            if not row["same_rtl"]:
                reference, tb = dataset / f"{task_id}_ref.sv", dataset / f"{task_id}_test.sv"
                if reference.exists() and tb.exists():
                    row["cpu_functional_control"] = evaluate(candidate, reference, tb, destination / "cpu_evaluation")
        except Exception as error:
            row.update(status="error", error=str(error))
            (destination / "error.log").write_text(traceback.format_exc())
        (destination / "result.json").write_text(json.dumps(row, indent=2) + "\n")
        records.append(row)
        print(json.dumps({key: row[key] for key in ("task_id", "status", "same_rtl", "self_agreement", "error") if key in row}), flush=True)
    summary = {"scope": manifest["scope"], "seconds": time.monotonic() - started,
               "tasks": len(records), "errors": sum(x["status"] == "error" for x in records),
               "changed_selections": sum(x.get("same_rtl") is False for x in records),
               "cpu_functional": {value: sum(x.get("cpu_functional_control", {}).get("status") == value for x in records)
                                  for value in ("PASS", "FAIL")}, "records": records}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--verification-policy", default="upstream", choices=["upstream", "rtl_tools_v1"])
    args = parser.parse_args()
    result = replay(args.source, args.out, args.dataset, args.verification_policy)
    print(json.dumps({k: v for k, v in result.items() if k != "records"}), flush=True)


if __name__ == "__main__":
    main()
