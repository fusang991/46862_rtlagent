"""Opt-in compatibility corrections around the pinned upstream verifier."""

from contextlib import contextmanager
import importlib
import re
import threading

_LOCK = threading.RLock()


@contextmanager
def verification_policy(name: str = "upstream", *, question: str = ""):
    """Bind in a dedicated workflow process; never change the default control."""
    if name == "rtl_timing_negative_only_v1":
        selected = "rtl_timing_v1" if statement_clock_edge(question) == "negedge" else "upstream"
        with verification_policy(selected, question=question):
            yield
        return
    if name == "upstream":
        yield
        return
    if name not in {"reset_names_v1", "rtl_tools_v1", "rtl_timing_v1"}:
        raise ValueError(f"Unknown verification policy: {name}")
    upstream = importlib.import_module("chipmate.cross_verify")
    with _LOCK:
        old_reset = upstream.is_active_low
        old_normalize = upstream.normalize_module_name
        old_builder = upstream.build_stim_testbench
        old_simulate = upstream.simulate_verilog

        def active_low(signal):
            lowered = signal.lower()
            return (old_reset(signal) or lowered.endswith(("resetn", "rstn"))
                    or lowered.startswith(("nreset", "nrst")))

        def normalize(code, target="TopModule"):
            clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", code, flags=re.DOTALL)
            if re.search(r"\bmodule\s+" + re.escape(target) + r"\b", clean):
                return code
            return old_normalize(code, target)

        upstream.is_active_low = active_low
        if name in {"rtl_tools_v1", "rtl_timing_v1"}:
            upstream.normalize_module_name = normalize
        if name == "rtl_timing_v1":
            from .candidate_policy import SimulationCache
            if isinstance(old_simulate, SimulationCache):
                upstream.is_active_low = old_reset
                upstream.normalize_module_name = old_normalize
                raise ValueError("Timing policy must wrap the simulation cache, not change it mid-cache")
            edge = statement_clock_edge(question)
            already_aligned = getattr(old_builder, "_qiansai_clock_edge", object()) == edge
            def builder(inputs, outputs, stimuli, has_clk, resets):
                text = old_builder(inputs, outputs, stimuli, has_clk, resets)
                return align_clock_stimulus(text, edge) if has_clk and edge and not already_aligned else text
            builder._qiansai_clock_edge = edge
            def simulate(dut_sv, inputs, outputs, *args, **kwargs):
                clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", dut_sv, flags=re.DOTALL)
                events = re.findall(r"\balways(?:_ff)?\s*@\s*\((.*?)\)", clean, re.DOTALL)
                if any(re.search(r"\bposedge\s+clk\b", event) and
                       re.search(r"\bnegedge\s+clk\b", event) for event in events):
                    return None, "sv_nonsynthesizable_dual_edge_always: use legal separate edge-triggered registers"
                interface_error = explicit_interface_error(clean, inputs, outputs)
                if interface_error:
                    return None, interface_error
                return old_simulate(dut_sv, inputs, outputs, *args, **kwargs)
            upstream.build_stim_testbench = builder
            upstream.simulate_verilog = simulate
        try:
            yield
        finally:
            upstream.is_active_low = old_reset
            upstream.normalize_module_name = old_normalize
            upstream.build_stim_testbench = old_builder
            upstream.simulate_verilog = old_simulate


def statement_clock_edge(question: str) -> str | None:
    """Choose a single global clock edge; leave mixed-edge statements unchanged."""
    negative = re.search(r"negative[- ]edge|falling[- ]edge|\bnegedge\b", question, re.I)
    if not negative:
        return "posedge"
    all_negative = re.search(r"all\s+(?:DFFs|flip[- ]flops|sequential\s+logic).*?(?:negative[- ]edge|falling[- ]edge)",
                             question, re.I | re.DOTALL)
    return "negedge" if all_negative else None


def align_clock_stimulus(text: str, edge: str) -> str:
    """Change data after the inactive edge and observe after the active edge."""
    if edge not in {"posedge", "negedge"}:raise ValueError("Unknown clock edge")
    if edge == "negedge":text = text.replace("@(posedge clk)", "@(negedge clk)")
    other = "negedge" if edge == "posedge" else "posedge"
    lines = text.splitlines()
    positions = [index-2 for index, line in enumerate(lines) if '$display("TEST_' in line]
    for position in reversed(positions):
        if position < 0 or f"@({edge} clk)" not in lines[position+1]:
            raise ValueError("Unexpected upstream stimulus template")
        lines.insert(position, f"    @({other} clk); #1;")
    return "\n".join(lines)


def explicit_interface_error(code, inputs, outputs):
    """Check only unambiguous literal ANSI declarations; leave complex syntax alone."""
    header = re.search(r"\bmodule\s+TopModule\s*\((.*?)\)\s*;", code, re.DOTALL)
    if not header:
        return None
    expected = {n: (direction, w) for direction, ports in (("input", inputs), ("output", outputs))
                for n, w in ports}
    declarations = re.finditer(
        r"\b(input|output)\s+(?:(?:reg|wire|logic)\s+)?(?:signed\s+)?"
        r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([A-Za-z_]\w*)", header[1])
    for match in declarations:
        direction, msb, lsb, name = match.groups()
        width = abs(int(msb)-int(lsb))+1 if msb is not None else 1
        if name in expected and expected[name] != (direction, width):
            return (f"sv_interface_mismatch: port {name} is {direction} width {width}; "
                    f"statement requires {expected[name][0]} width {expected[name][1]}")
    return None
