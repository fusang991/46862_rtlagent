"""Development entry for one official prompt and the Vivado cross-check adapter.

Only prompt.txt is read from the task directory. Independent official judging
is a separate operation; its reference and testbench are never generation input.
Model services must already be ready, so startup is outside the task budget.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import time

import yaml
from chipmate.backends import make_backend
from chipmate.inference import run_problem

from .case_runner import TracedBackend
from .services import model_ready
from .vivado import VivadoSimulator, bind_vivado_simulator


def prompt_interface(question: str) -> str:
    """Read declared ports from a prompt table or ANSI module header.

    The competition contract uses TopModule when the table does not name it.
    A buggy implementation supplied in a question is allowed input, but only
    its header is copied into the stimulus interface, never its body.
    """
    module = re.search(r"\bmodule\s+named\s+[`\"]?([A-Za-z_]\w*)", question)
    table = []
    seen = set()
    for line in question.splitlines():
        if not re.match(r"\s*[-*]\s+(input|output|inout)\b", line):
            continue
        port = re.fullmatch(
            r"\s*[-*]\s+(input|output)\s+([A-Za-z_]\w*)"
            r"\s*(?:\(\s*(\d+)\s+bits?\s*\))?\s*[,;]?\s*", line,
        )
        if port is None:
            raise ValueError(f"Unsupported interface declaration: {line.strip()}")
        direction, name, width = port.groups()
        bits = int(width or 1)
        if bits < 1 or name in seen:
            raise ValueError("Invalid width or duplicate port in prompt")
        seen.add(name)
        table.append(f"{direction} " + (f"[{bits-1}:0] " if bits > 1 else "") + name)
    if table:
        if not any(p.startswith("output ") for p in table):
            raise ValueError("Declared interface has no output ports")
        name = module[1] if module else "TopModule"
        return f"module {name} (" + ", ".join(table) + ");\nendmodule\n"
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", question, flags=re.DOTALL)
    header = re.search(r"\bmodule\s+(TopModule)\s*\((.*?)\)\s*;", clean, re.DOTALL)
    if header:
        # Keep the subset supported by the pinned upstream port parser explicit.
        ports = header[2].split(",")
        declaration = re.compile(
            r"\s*(input|output)\s+(?:(?:reg|wire|logic)\s+)?"
            r"(?:\[\s*\d+\s*:\s*\d+\s*\]\s*)?[A-Za-z_]\w*\s*"
        )
        if not all(declaration.fullmatch(p) for p in ports):
            raise ValueError("Unsupported port declaration in prompt module header")
        if not any(re.match(r"\s*output\b", p) for p in ports):
            raise ValueError("Declared interface has no output ports")
        return header[0].strip() + "\nendmodule\n"
    raise ValueError("No supported interface port table or module header found in prompt")


def run_task(task: Path, out: Path, serving: dict, workflow: dict,
             *, deadline_s: float = 900, simulation_timeout: float = 120) -> dict:
    if deadline_s <= 0:
        raise ValueError("Task deadline must be positive")
    question = (task / "prompt.txt").read_text().strip()
    stub = prompt_interface(question)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise FileExistsError(f"Refusing to overwrite task output: {out}")
    for role in ("v", "p"):
        if not model_ready(serving[role]):
            raise RuntimeError(f"{role} service must be started before running a task")
    (out / "input.json").write_text(json.dumps({
        "task_id": task.name, "prompt": question, "interface_from_prompt": stub,
    }, indent=2) + "\n")
    (out / "config.json").write_text(json.dumps({
        "serving": serving, "workflow": workflow, "deadline_s": deadline_s,
        "simulation_timeout_s": simulation_timeout,
        "started_at": datetime.now().astimezone().isoformat(),
    }, indent=2) + "\n")
    started = time.monotonic()
    simulator = VivadoSimulator(out / "tools", timeout_s=simulation_timeout,
                                deadline=started + deadline_s)
    backends = {role: TracedBackend(make_backend(
        provider="openai-compat", model=serving[role]["model"],
        base_url=serving[role]["base_url"], api_key="local",
    ), out / "calls" / role) for role in ("v", "p")}
    with bind_vivado_simulator(simulator):
        result = run_problem(
            task_id=task.name, question=question, ref_sv=stub,
            v_backend=backends["v"], p_backend=backends["p"],
            **{key: workflow[key] for key in
               ("n", "max_turns", "num_verify_tests", "temperature", "seed")},
        ).to_dict()
    result["seconds"] = time.monotonic() - started
    result["verification_scope"] = "Generated RTL/Python agreement; independently grade solution.v with official judge.py"
    (out / "generation.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / "solution.v").write_text(result["verilog"])
    (out / "reference_model.py").write_text(result["python"])
    trace = []
    for path in out.glob("calls/*/batch_*/requests/request_*.json"):
        row = json.loads(path.read_text())
        trace.append({"tool": "llm", "role": path.relative_to(out).parts[1],
                      "artifact": str(path.relative_to(out)), **row})
    for path in (out / "tools").glob("simulation_*/*.json"):
        row = json.loads(path.read_text())
        trace.append({"tool": row["argv"][0], "artifact": str(path.relative_to(out)), **row})
    with (out / "trace.jsonl").open("x") as stream:
        for row in sorted(trace, key=lambda item: item.get("started_at", "")):
            stream.write(json.dumps(row) + "\n")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("task", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--serving", required=True, type=Path)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--deadline-s", type=float, default=900)
    args = p.parse_args()
    result = run_task(args.task, args.out, yaml.safe_load(args.serving.read_text()),
                      yaml.safe_load(args.config.read_text()), deadline_s=args.deadline_s)
    print(json.dumps({"task_id": result["task_id"], "seconds": result["seconds"],
                      "self_agreement": result["best_match_rate"],
                      "solution": str(args.out / "solution.v")}), flush=True)


if __name__ == "__main__":
    main()
