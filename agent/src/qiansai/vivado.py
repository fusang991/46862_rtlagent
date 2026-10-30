"""Vivado simulation for ChipMATE's generated stimulus harness.

This is an opt-in tool adapter. The pinned upstream sources, prompts, candidate
selection and Python reference execution remain unchanged. The binding must be
used in a dedicated workflow process (the official service launches one per job).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

_BINDING_LOCK = threading.Lock()


def parse_stimulus_output(text: str, outputs: list[tuple[str, int]], count: int):
    """Require every vector and output; absent/x/z outputs cannot mean agreement."""
    rows: dict[int, dict[str, int]] = {}
    names = {name for name, _ in outputs}
    if "TIMEOUT" in text:
        return None, "sv_sim_hang"
    for line in text.splitlines():
        match = re.fullmatch(r"TEST_(\d+)\s+(.*)", line.strip())
        if not match:
            continue
        index = int(match[1])
        if index >= count or index in rows:
            return None, "sv_invalid_vector_index"
        pairs = re.findall(r"(\w+)=([^\s]+)", match[2])
        if len(pairs) != len(names) or {name for name, _ in pairs} != names:
            return None, "sv_incomplete_outputs"
        if any(not re.fullmatch(r"-?\d+", value) for _, value in pairs):
            return None, "sv_unknown_outputs"
        rows[index] = {name: int(value) for name, value in pairs}
    if set(rows) != set(range(count)):
        return None, "sv_incomplete_vectors"
    return [rows[index] for index in range(count)], None


class VivadoSimulator:
    def __init__(self, logs: Path, *, scratch: Path | None = None,
                 timeout_s: float = 120, deadline: float | None = None):
        if timeout_s <= 0:
            raise ValueError("Simulation timeout must be positive")
        self.logs = logs.resolve()
        self.logs.mkdir(parents=True, exist_ok=True)
        self.scratch = Path(scratch or os.environ.get("EDA_TMP", tempfile.gettempdir()))
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.deadline = deadline
        for tool in ("xvlog", "xelab", "xsim"):
            if shutil.which(tool) is None:
                raise RuntimeError(f"{tool} unavailable; load the official Vivado environment")

    def _run(self, argv: list[str], work: Path, directory: Path, stage: str, until: float):
        remaining = until - time.monotonic()
        if remaining <= 0:
            return None, "", f"sv_{stage}_timeout"
        started = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        record = {"argv": argv, "cwd": str(work), "returncode": None,
                  "started_at": timestamp, "seconds": 0, "error": None,
                  "status": "starting"}
        def persist():
            temporary = directory / f"{stage}.json.tmp"
            temporary.write_text(json.dumps(record, indent=2) + "\n")
            temporary.replace(directory / f"{stage}.json")
        persist()
        try:
            process = subprocess.Popen(argv, cwd=work, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, errors="replace",
                                       start_new_session=True)
        except OSError as error:
            record.update(status="spawn_error", error=str(error), seconds=time.monotonic() - started)
            persist()
            raise
        record.update(status="running", pid=process.pid)
        persist()
        error = None
        try:
            output, _ = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = process.communicate()
            error = f"sv_{stage}_timeout"
        # Also reap helper processes which outlive a failed parent.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if error is None and process.returncode:
            error = f"sv_{stage}_fail: {output.strip()[:1500]}"
        (directory / f"{stage}.log").write_text(output)
        record.update(returncode=process.returncode, seconds=time.monotonic() - started,
                      error=error, status="failed" if error else "completed")
        persist()
        return process.returncode, output, error

    def __call__(self, dut_sv, inputs, outputs, stimuli, has_clk, resets, timeout=30):
        upstream = importlib.import_module("chipmate.cross_verify")
        directory = Path(tempfile.mkdtemp(prefix="simulation_", dir=self.logs))
        until = time.monotonic() + self.timeout_s
        if self.deadline is not None:
            until = min(until, self.deadline)
        with tempfile.TemporaryDirectory(prefix="qiansai-vivado-", dir=self.scratch) as tmp:
            work = Path(tmp)
            tb = upstream.build_stim_testbench(inputs, outputs, stimuli, has_clk, resets)
            for name, content in (("dut.sv", dut_sv), ("tb.sv", tb)):
                (work / name).write_text(content)
                (directory / name).write_text(content)
            commands = [
                ("compile", ["xvlog", "-sv", "--nolog", "dut.sv", "tb.sv"]),
                ("elaborate", ["xelab", "stim_tb", "-s", "stim_snap", "--nolog",
                               "--timescale", "1ns/1ps", "--relax"]),
                ("sim", ["xsim", "stim_snap", "-R", "--nolog", "-sv_seed", "0"]),
            ]
            for stage, argv in commands:
                _, output, error = self._run(argv, work, directory, stage, until)
                if error:
                    return None, error
            return parse_stimulus_output(output, outputs, len(stimuli))


@contextmanager
def bind_vivado_simulator(simulator: VivadoSimulator):
    """Temporarily replace the simulator in an exclusive workflow process."""
    upstream = importlib.import_module("chipmate.cross_verify")
    with _BINDING_LOCK:
        original = upstream.simulate_verilog
        upstream.simulate_verilog = simulator
        try:
            yield
        finally:
            upstream.simulate_verilog = original
