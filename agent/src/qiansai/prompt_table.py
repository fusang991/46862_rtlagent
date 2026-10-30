"""Check model candidates against a combinational truth table in the input prompt.

This tool never reads an evaluator reference. Sequential waveforms and tables
belonging to nested modules are left to the model. Partial examples constrain
candidate checking but are never treated as a complete function specification.
"""

from dataclasses import dataclass
import re

from chipmate.cross_verify import parse_ports


@dataclass
class PromptTable:
    inputs: list
    outputs: list
    rows: list
    radix: int = 10
    coverage_complete: bool = True
    source: str = "public prompt waveform"

    def to_dict(self):
        return {"inputs": self.inputs, "outputs": self.outputs, "rows": self.rows,
                "source": self.source, "complete_combinational_coverage": self.coverage_complete,
                "waveform_radix": self.radix}


def extract_complete_table(question: str, stub: str) -> PromptTable | None:
    if sum(width for _, width in parse_ports(stub)[0]) > 8:
        return None
    return extract_table_examples(question, stub, require_complete=True)


def extract_table_examples(question: str, stub: str, *, require_complete=False) -> PromptTable | None:
    if not re.search(r"\bcombinational\s+circuit\b", question, re.I):
        return None
    if re.search(r"\b(?:sequential|submodules?|module\s+[A-Z])\b", question, re.I):
        return None
    if re.search(r"\bsub[-\s]+modules?\b", question, re.I):
        return None
    if not require_complete:
        named = re.findall(r"\b[Mm]odule\s+(?:named\s+)?[`\"]?([A-Z]\w*)", question)
        if any(name != "TopModule" for name in named):
            return None
    inputs, outputs = parse_ports(stub)
    if not inputs or not outputs or sum(w for _, w in inputs) > 512:
        return None
    if any(re.search(r"clk|clock|reset|rst", n, re.I) for n, _ in inputs):
        return None
    ports = dict(inputs + outputs)
    lines = question.splitlines()
    tables = []
    for index, line in enumerate(lines):
        header = line.split()
        if not header or header[0].lower() != 'time' or len(set(header)) != len(header):
            continue
        if set(header[1:]) != set(ports):
            continue
        raw = []
        for body in lines[index+1:]:
            cells = body.split()
            if not cells:
                if raw:break
                continue
            if len(cells) != len(header) or not re.fullmatch(r"\d+(?:\.\d+)?(?:ps|ns|us|ms)", cells[0]):
                break
            if any(re.search(r"[xz?]", cell, re.I) for cell in cells[1:]):
                continue
            if not all(re.fullmatch(r"[0-9a-fA-F]+", cell) for cell in cells[1:]):
                return None
            raw.append(cells[1:])
        if not raw:continue
        radix = 16 if any(re.search(r"[a-f]", cell, re.I) for row in raw for cell in row) else 10
        # Multi-digit numeral-only waveforms have an unspecified radix. Decline
        # rather than treating a possible hexadecimal 10 as decimal ten.
        if radix == 10 and any(len(cell.lstrip("0")) > 1 for row in raw for cell in row):
            continue
        bases = [radix] * (len(header)-1)
        known = {}
        for cells in raw:
            row = {n: int(cell, base) for n, cell, base in zip(header[1:], cells, bases)}
            if any(row[n] >= 1 << w for n, w in ports.items()):return None
            key = tuple(row[n] for n, _ in inputs)
            result = {n: row[n] for n, _ in outputs}
            if key in known and known[key] != result:return None
            known[key] = result
        complete = len(known) == 1 << sum(w for _, w in inputs)
        if require_complete and not complete:continue
        tables.append(PromptTable(inputs, outputs,
                      [{"inputs": dict(zip((n for n, _ in inputs), key)), "outputs": value}
                       for key, value in sorted(known.items())], radix, complete))
    return tables[0] if len(tables) == 1 else None


def extract_state_table_examples(question: str, stub: str) -> PromptTable | None:
    """Read explicit encodings and Moore transition rows, without deriving RTL.

    Only an external state input and a single binary control are supported.
    Unlisted encodings remain unconstrained, including illegal one-hot states.
    """
    if not re.search(r"\bcombinational\s+logic\s+portion\b", question, re.I):
        return None
    inputs, outputs = parse_ports(stub)
    if set(dict(inputs)) != {'state', 'in'} or set(dict(outputs)) != {'next_state', 'out'}:
        return None
    widths = dict(inputs + outputs)
    if widths['in'] != 1 or widths['out'] != 1 or widths['state'] != widths['next_state']:
        return None
    encodings = re.findall(r"\b([A-Z]\w*)\s*=\s*(\d+)'[bB]([01_]+)\b", question)
    codes = {}
    for name, width, binary in encodings:
        value = int(binary.replace('_', ''), 2)
        if int(width) != widths['state'] or value >= 1 << widths['state']:
            return None
        if name in codes and codes[name] != value:
            return None
        codes[name] = value
    if not codes or len(set(codes.values())) != len(codes):
        return None
    lines = question.splitlines()
    tables = []
    for index, line in enumerate(lines):
        if not re.fullmatch(r"\s*State\s*\|\s*Next state in=0,\s*Next state in=1\s*\|\s*Output\s*", line, re.I):
            continue
        rows, seen = [], set()
        for body in lines[index+1:]:
            match = re.fullmatch(r"\s*([A-Z]\w*)\s*\|\s*([A-Z]\w*),\s*([A-Z]\w*)\s*\|\s*([01])\s*", body)
            if not match:
                if body.strip() or rows:
                    break
                continue
            state, zero, one, output = match.groups()
            if state in seen or any(name not in codes for name in (state, zero, one)):
                return None
            seen.add(state)
            for control, successor in enumerate((zero, one)):
                rows.append({'inputs': {'state': codes[state], 'in': control},
                             'outputs': {'next_state': codes[successor], 'out': int(output)}})
        if seen == set(codes):
            tables.append(PromptTable(inputs, outputs, rows, 2,
                          len(codes) == 1 << widths['state'], 'public prompt state transition table'))
    return tables[0] if len(tables) == 1 else None


def extract_public_examples(question: str, stub: str) -> PromptTable | None:
    return extract_table_examples(question, stub) or extract_state_table_examples(question, stub)


class TableCandidateChecker:
    """Score model-produced RTL using public examples; never generate a design."""
    def __init__(self, table: PromptTable, simulator, root):
        import json
        self.table, self.simulator, self.root = table, simulator, root
        self.cache = {}
        root.mkdir(parents=True, exist_ok=False)
        (root / "public_table.json").write_text(json.dumps(table.to_dict(), indent=2) + "\n")

    def __call__(self, code):
        import hashlib
        import json
        identity = hashlib.sha256(code.encode()).hexdigest()
        if identity in self.cache:
            return self.cache[identity]
        actual, error = self.simulator(code, self.table.inputs, self.table.outputs,
                                      [row["inputs"] for row in self.table.rows], False, [])
        expected = [row["outputs"] for row in self.table.rows]
        checks = len(expected) * len(self.table.outputs)
        correct = 0
        if not error and actual is not None and len(actual) == len(expected):
            correct = sum(a.get(name) == e[name] for a, e in zip(actual, expected)
                          for name, _ in self.table.outputs)
        score = correct / checks
        self.cache[identity] = score
        (self.root / (identity + ".json")).write_text(json.dumps({
            "candidate_sha256": identity, "source": "model candidate",
            "prompt_example_match_rate": score, "correct_cells": correct,
            "total_cells": checks, "error": error, "actual": actual,
        }, indent=2) + "\n")
        return score

    def feedback(self, code):
        import hashlib
        import json
        identity = hashlib.sha256(code.encode()).hexdigest()
        record = json.loads((self.root / (identity + ".json")).read_text())
        actual = record.get("actual") or []
        counterexamples = []
        for index, row in enumerate(self.table.rows):
            observed = actual[index] if index < len(actual) else None
            if observed != row["outputs"]:
                counterexamples.append({"inputs": row["inputs"], "required_by_statement": row["outputs"],
                                        "observed_from_candidate": observed})
        return ("The candidate violates input/output examples explicitly supplied in the statement. "
                "Repair the design using the statement and these concrete counterexamples. "
                + ("These examples are incomplete: do not assume that other input signals are fixed to the example values. "
                   if not self.table.coverage_complete else "") +
                "Return the complete Verilog design in the original answer format.\n" +
                json.dumps({"compile_or_simulation_error": record["error"],
                            "public_counterexamples": counterexamples[:5]}))
