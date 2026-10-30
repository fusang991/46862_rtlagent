"""Evaluate generated RTL with the pinned VerilogEval reference and testbench."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .datasets import sha256_file


def run_logged(command: list[str], directory: Path, log: Path, timeout: float) -> dict:
    """Keep raw output and kill the process group if a tool exceeds its budget."""
    started = time.monotonic()
    with log.open("x", encoding="utf-8") as stream:
        process = subprocess.Popen(
            command, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    return {
        "command": command, "returncode": process.returncode, "timed_out": timed_out,
        "seconds": time.monotonic() - started, "log": log.name,
    }


def simulation_result(output: str, returncode: int, timed_out: bool) -> dict:
    """Require the official summary, positive coverage, and a clean termination."""
    result = {"status": "FAIL", "mismatches": None, "samples": None}
    summaries = re.findall(r"^Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples\s*$", output, re.M)
    if timed_out:
        result["reason"] = "simulation_wall_timeout"
    elif returncode != 0:
        result["reason"] = "simulation_error"
    elif re.search(r"^\s*TIMEOUT\s*$", output, re.M):
        result["reason"] = "testbench_timeout"
    elif len(summaries) != 1:
        result["reason"] = "missing_or_ambiguous_testbench_summary"
    else:
        mismatches, samples = map(int, summaries[0])
        result.update(mismatches=mismatches, samples=samples)
        if samples == 0:
            result["reason"] = "no_samples"
        elif mismatches:
            result["reason"] = "output_mismatch"
        else:
            result.update(status="PASS", reason="official_testbench_passed")
    return result


def evaluate(
    candidate: Path, reference: Path, testbench: Path, directory: Path,
    *, timeout: float = 30,
) -> dict:
    """Run an independent functional test; cross-verification is never an input."""
    if timeout <= 0:
        raise ValueError("Evaluation timeout must be positive")
    for tool in ("iverilog", "vvp"):
        if not shutil.which(tool):
            raise RuntimeError(f"Missing evaluator tool: {tool}")
    for source in (candidate, reference, testbench):
        if not source.is_file():
            raise ValueError(f"Missing evaluator input: {source}")
    directory.mkdir(parents=True, exist_ok=False)
    directory = directory.resolve()
    sources = {}
    for name, source in (("candidate.sv", candidate), ("reference.sv", reference), ("testbench.sv", testbench)):
        shutil.copyfile(source, directory / name)
        sources[name] = sha256_file(directory / name)
    compile_run = run_logged(
        ["iverilog", "-g2012", "-s", "tb", "-o", "simulation.vvp", *sources],
        directory, directory / "compile.log", timeout,
    )
    compile_ok = compile_run["returncode"] == 0 and not compile_run["timed_out"]
    result = {
        "evaluator": "verilogeval-v2-icarus", "inputs_sha256": sources,
        "compile": {"status": "PASS" if compile_ok else "FAIL", **compile_run},
        "simulation": {"status": "NOT_RUN", "reason": "compile_failed"},
        "status": "FAIL",
    }
    if compile_ok:
        simulation = run_logged(
            ["vvp", "simulation.vvp"], directory, directory / "simulation.log", timeout,
        )
        verdict = simulation_result(
            (directory / "simulation.log").read_text(encoding="utf-8", errors="replace"),
            simulation["returncode"], simulation["timed_out"],
        )
        result["simulation"] = {**simulation, **verdict}
        result["status"] = verdict["status"]
    return result
