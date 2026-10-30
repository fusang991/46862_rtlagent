"""Reuse an observed Vivado simulation only for identical DUT and testbench bytes."""

import hashlib
import importlib
import json
from pathlib import Path
import threading

from .vivado import parse_stimulus_output


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class CapturedVivado:
    def __init__(self, captures: Path, *, fallback=None):
        self.fallback = fallback
        self.index, self.uses = {}, []
        self.lock = threading.Lock()
        self.hits = self.misses = 0
        entries = []
        for directory in captures.glob("simulation_*"):
            if not (directory / "dut.sv").is_file() or not (directory / "tb.sv").is_file():
                continue
            first = directory / "compile.json"
            stamp = json.loads(first.read_text()).get("started_at", "") if first.exists() else ""
            entries.append((stamp, directory))
        for _, directory in sorted(entries):
            key = (_sha((directory / "dut.sv").read_bytes()), _sha((directory / "tb.sv").read_bytes()))
            self.index.setdefault(key, directory)

    def __call__(self, dut_sv, inputs, outputs, stimuli, has_clk, resets, timeout=30):
        upstream = importlib.import_module("chipmate.cross_verify")
        tb = upstream.build_stim_testbench(inputs, outputs, stimuli, has_clk, resets)
        key = (_sha(dut_sv.encode()), _sha(tb.encode()))
        directory = self.index.get(key)
        with self.lock:
            if directory is None:
                self.misses += 1
            else:
                self.hits += 1
            self.uses.append({"dut_sha256": key[0], "testbench_sha256": key[1],
                              "source": str(directory) if directory else None})
        if directory is None:
            if self.fallback is None:
                return None, "sv_not_in_captured_vivado_results"
            return self.fallback(dut_sv, inputs, outputs, stimuli, has_clk, resets, timeout)
        for stage in ("compile", "elaborate", "sim"):
            path = directory / f"{stage}.json"
            if not path.exists():
                return None, f"sv_captured_{stage}_incomplete"
            result = json.loads(path.read_text())
            if result.get("returncode") != 0 or result.get("error"):
                return None, f"sv_captured_{stage}_failed"
        return parse_stimulus_output((directory / "sim.log").read_text(), outputs, len(stimuli))

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "uses": self.uses}
