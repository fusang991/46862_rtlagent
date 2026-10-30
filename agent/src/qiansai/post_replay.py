"""Paired final-selection ablation over a completed official agent batch.

The recorded original generation and feedback stay fixed. Captured Vivado
outputs are reused by exact input identity; missing simulations run in Vivado.
No model is called. Changed final RTL is independently graded by official tools.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from .candidate_policy import cached_simulators
from .captured_vivado import CapturedVivado
from .official_entry import collect_trace, supervise
from .postselect import select_recorded
from .reference_trace import trace_reference_tools
from .verification_policy import verification_policy
from .vivado import VivadoSimulator, bind_vivado_simulator


def run(source: Path, out: Path, tasks: Path, official: Path, policy: str, cases=None, *,
        acceptance_policy="unconditional", additional_capture_roots=(), prior_selection_cost_s=0):
    if acceptance_policy not in {"unconditional", "strong_evidence_v1", "strong_evidence_v2", "strong_evidence_v3"}:
        raise ValueError(f"Unknown acceptance policy: {acceptance_policy}")
    if prior_selection_cost_s < 0:
        raise ValueError("Prior selection cost must be nonnegative")
    source, out = source.resolve(), out.resolve()
    expected = json.loads((source / "manifest.json").read_text())["selected"]
    if cases:
        if set(cases) - set(expected):
            raise ValueError("Requested case is absent from the original batch")
        expected = [name for name in expected if name in cases]
    grades = source / "selftest/results"
    if not all((grades / f"agent.{name}.s0.json").exists() for name in expected):
        raise ValueError("Original agent batch is not complete")
    out.mkdir(parents=True, exist_ok=False)
    (out / "results").mkdir()
    manifest = {"source": str(source), "scope": "Paired final-selection ablation, zero new model calls",
                "post_verification_policy": policy, "tasks": expected,
                "acceptance_policy": acceptance_policy,
                "additional_capture_roots": [str(p) for p in additional_capture_roots],
                "prior_selection_cost_s": prior_selection_cost_s,
                "generation_trajectory": "recorded original prompts, candidates and feedback, unchanged",
                "timing_scope": "selection-stage timing; conservative original-time budget; fresh end-to-end timing still needed"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    records, started = [], time.monotonic()
    for task_id in expected:
        directory = out / task_id
        directory.mkdir()
        sample = source / "selftest/agent" / task_id / "s0"
        work = sample / "workflow"
        control_path = work / "generation.json"
        row = {"task_id": task_id, "model_calls": 0, "status": "unchanged_incomplete_control"}
        solution = directory / "solution.v"
        solution.write_bytes((sample / "solution.v").read_bytes())
        try:
            if control_path.exists():
                control = json.loads(control_path.read_text())
                data = json.loads((work / "input.json").read_text())
                config = json.loads((work / "config.json").read_text())["workflow"]
                entry = json.loads((sample / "entry.json").read_text())
                budget = max(0, 898 - entry["seconds"])
                pools = {role: [json.loads(p.read_text())["outputs"] for p in sorted((work / "calls" / role).glob("batch_*/response.json"))]
                         for role in ("v", "p")}
                begin = time.monotonic()
                checkpoints = [{"seconds": 0, "candidate": control}]
                def checkpoint(candidate):
                    snapshot = {"seconds": prior_selection_cost_s + time.monotonic() - begin, "candidate": candidate}
                    checkpoints.append(snapshot)
                    (directory / f"checkpoint_{len(checkpoints)-1:03d}.json").write_text(json.dumps(snapshot, indent=2) + "\n")
                fresh = VivadoSimulator(directory / "tools", timeout_s=120)
                captured = CapturedVivado(work / "tools", fallback=fresh)
                capture_chain = [captured]
                for capture_root in additional_capture_roots:
                    captured = CapturedVivado(Path(capture_root) / task_id / task_id / "tools", fallback=captured)
                    capture_chain.append(captured)
                with (bind_vivado_simulator(captured), trace_reference_tools(directory / "tools"),
                      verification_policy(policy, question=data["prompt"]), cached_simulators()):
                    gate = None
                    if acceptance_policy != "unconditional":
                        from .selection_acceptance import EvidenceGate
                        gate = EvidenceGate(control, data.get("interface_stub") or data["interface_from_prompt"],
                                            num_verify_tests=config["num_verify_tests"], seed=config["seed"],
                                            checkpoint=checkpoint, policy=acceptance_policy)
                    selected, matrices = select_recorded(data.get("interface_stub") or data["interface_from_prompt"],
                        pools["v"], pools["p"], n=config["n"], num_verify_tests=config["num_verify_tests"],
                        seed=config["seed"], checkpoint=gate.observe if gate else checkpoint)
                    proposal = selected
                    if gate:
                        selected = gate.chosen
                feasible = [x for x in checkpoints if x["seconds"] <= budget]
                chosen = feasible[-1]["candidate"]
                solution.write_text(chosen["verilog"])
                selection = {"selected": chosen, "unbounded_selection": selected,
                             "unbounded_proposal": proposal,
                             "matrices": matrices, "capture": captured.stats(),
                             "capture_chain": [item.stats() for item in capture_chain],
                             "acceptance": gate.summary() if gate else None,
                             "wall_post_seconds": time.monotonic() - begin,
                             "prior_selection_cost_s": prior_selection_cost_s,
                             "post_seconds": prior_selection_cost_s + time.monotonic() - begin,
                             "remaining_original_budget_s": budget,
                             "fell_back_for_budget": feasible[-1] is not checkpoints[-1]}
                (directory / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")
                row.update(status="postselected", post_seconds=selection["post_seconds"],
                           capture_hits=captured.hits, capture_misses=captured.misses,
                           fell_back_for_budget=selection["fell_back_for_budget"])
                with (directory / "trace.jsonl").open("x") as stream:
                    for record in collect_trace(directory):
                        if record.get("artifact"):
                            record["artifact"] = str(Path(record["artifact"]).relative_to(directory.name))
                        stream.write(json.dumps(record) + "\n")
            # Independent reference/testbench access begins only after selection.
            same = solution.read_bytes() == (sample / "solution.v").read_bytes()
            row.update(same_rtl=same, sha256=hashlib.sha256(solution.read_bytes()).hexdigest())
            original = json.loads((grades / f"agent.{task_id}.s0.json").read_text())
            row["original_official"] = original
            if same:
                verdict = original
                row["official_grade_source"] = "identical original RTL bytes"
            else:
                evaluation = directory / "evaluation"
                evaluation.mkdir()
                argv = ["/usr/bin/python3", str(official / "selftest/judge.py"), "--task", str(tasks / task_id),
                        "--solution", str(solution), "--outdir", str(evaluation / "logs"),
                        "--json", str(evaluation / "result.json"), "--timeout", "300"]
                operation = supervise(argv, evaluation / "command.log", 330)
                (evaluation / "command.json").write_text(json.dumps(operation, indent=2) + "\n")
                if operation["returncode"] or operation["timed_out"]:
                    raise RuntimeError("Official confirmation failed to execute")
                verdict = json.loads((evaluation / "result.json").read_text())
                row["official_grade_source"] = "new official Vivado confirmation"
            row["official"] = verdict
            (out / "results" / f"agent.{task_id}.s0.json").write_text(json.dumps(verdict, indent=2) + "\n")
        except Exception as error:
            row.update(status="execution_error", error=str(error))
            (directory / "error.log").write_text(traceback.format_exc())
        records.append(row)
        (directory / "result.json").write_text(json.dumps(row, indent=2) + "\n")
        print(json.dumps({k: v for k, v in row.items() if k not in {"original_official", "official"}}
                         | ({"L": row["official"]["level"]} if "official" in row else {})), flush=True)
    baseline_files = [grades / f"baseline.{name}.s0.json" for name in expected
                      if (grades / f"baseline.{name}.s0.json").exists()]
    score_path = None
    if len(baseline_files) == len(expected) and all("official" in x for x in records):
        for path in baseline_files:
            shutil.copy2(path, out / "results" / path.name)
        score_path = out / "score.json"
        operation = subprocess.run([sys.executable, str(official / "selftest/score.py"), "--results", str(out / "results"),
                                    "--json", str(score_path)], capture_output=True, text=True, check=True)
        (out / "score.stdout").write_text(operation.stdout)
        (out / "score.stderr").write_text(operation.stderr)
    summary = {"scope": manifest["scope"], "seconds": time.monotonic() - started, "tasks": len(expected),
               "model_calls": 0, "changed": sum(x.get("same_rtl") is False for x in records),
               "errors": sum(x["status"] == "execution_error" for x in records),
               "score": str(score_path) if score_path else None, "baseline_reused_from_same_study": True,
               "records": records}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "out", "tasks", "official"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--post-verification-policy", choices=["upstream", "rtl_tools_v1", "rtl_timing_v1"], default="upstream")
    parser.add_argument("--case", action="append", help="Explicit completed cases; otherwise replay the whole batch")
    parser.add_argument("--acceptance-policy", choices=["unconditional", "strong_evidence_v1", "strong_evidence_v2", "strong_evidence_v3"], default="unconditional")
    parser.add_argument("--additional-capture-root", action="append", type=Path, default=[])
    parser.add_argument("--prior-selection-cost-s", type=float, default=0,
                        help="Conservatively charge prior replay cost when its captures are reused")
    args = parser.parse_args()
    result = run(args.source, args.out, args.tasks, args.official, args.post_verification_policy, args.case,
                 acceptance_policy=args.acceptance_policy, additional_capture_roots=args.additional_capture_root,
                 prior_selection_cost_s=args.prior_selection_cost_s)
    print(json.dumps({k: v for k, v in result.items() if k != "records"}), flush=True)


if __name__ == "__main__":
    main()
