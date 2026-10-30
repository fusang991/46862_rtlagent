"""Check model RTL against explicit scalar sequential waveforms in the prompt.

Only verification testbenches are emitted. This module never emits a design or
reads an evaluator reference. Finite observations do not prove full correctness.
"""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import importlib
import json
import re
import threading

from chipmate.cross_verify import parse_ports

_LOCK = threading.RLock()


@dataclass
class PublicWaveform:
    inputs: list
    outputs: list
    clock: str
    rows: list
    coverage_complete: bool = False

    def to_dict(self):
        return {**self.__dict__, 'source': 'public prompt scalar sequential waveform',
                'timing_convention': 'clock transition first, other input changes after 1ps, observe after another 1ps'}


def extract_scalar_waveform(question, stub):
    if not re.search(r'\bsequential\s+circuit\b', question, re.I):
        return None
    if re.search(r'\bsub[-\s]?modules?\b', question, re.I):
        return None
    inputs, outputs = parse_ports(stub)
    if not inputs or not outputs or any(width != 1 for _, width in inputs + outputs):
        return None
    clocks = [name for name, _ in inputs if name.lower() in {'clk', 'clock'}]
    if len(clocks) != 1:
        return None
    ports = {name for name, _ in inputs + outputs}
    tables = []
    lines = question.splitlines()
    for index, line in enumerate(lines):
        header = line.split()
        if not header or header[0].lower() != 'time' or set(header[1:]) != ports or len(set(header)) != len(header):
            continue
        rows = []
        for body in lines[index+1:]:
            cells = body.split()
            if not cells and not rows:
                continue
            if len(cells) != len(header):
                break
            stamp = re.fullmatch(r'(\d+(?:\.\d+)?)(ps|ns|us|ms)', cells[0])
            if not stamp or any(value.lower() not in {'0','1','x'} for value in cells[1:]):
                break
            exact = Decimal(stamp[1]) * {'ps':1,'ns':1000,'us':1000000,'ms':1000000000}[stamp[2]]
            if exact != int(exact):
                return None
            time_ps = int(exact)
            if rows and time_ps - rows[-1]['time_ps'] < 3:
                return None
            values = {name: None if value.lower() == 'x' else int(value)
                      for name, value in zip(header[1:], cells[1:])}
            rows.append({'time_ps':time_ps, 'inputs':{n:values[n] for n,_ in inputs},
                         'outputs':{n:values[n] for n,_ in outputs}})
        known = sum(value is not None for row in rows for value in row['outputs'].values())
        if rows and len(rows) <= 512 and known:
            tables.append(PublicWaveform(inputs, outputs, clocks[0], rows))
    return tables[0] if len(tables) == 1 else None


def waveform_testbench(wave):
    lines = ['`timescale 1ps/1ps', 'module stim_tb();']
    lines += [f'reg {name};' for name,_ in wave.inputs]
    lines += [f'wire {name};' for name,_ in wave.outputs]
    lines += ['TopModule dut ('+', '.join(f'.{name}({name})' for name,_ in wave.inputs+wave.outputs)+');', 'initial begin']
    current = 0
    def bit(value):return "1'bx" if value is None else "1'b"+str(value)
    for index,row in enumerate(wave.rows):
        lines.append(f"#{row['time_ps']-current};")
        lines.append(f"{wave.clock} = {bit(row['inputs'][wave.clock])}; #1;")
        lines += [f"{name} = {bit(row['inputs'][name])};" for name,_ in wave.inputs if name != wave.clock]
        lines.append('#1;')
        fmt=' '.join(f'{name}=%0d' for name,_ in wave.outputs)
        args=', '.join(f"({name} & 1'b{int(row['outputs'][name] is not None)})" for name,_ in wave.outputs)
        lines.append(f'$display("TEST_{index} {fmt}", {args});')
        current=row['time_ps']+2
    lines += ['$finish;', 'end', f'initial begin #{current+10000}; $display("TIMEOUT"); $finish; end', 'endmodule']
    return '\n'.join(lines)+'\n'


class WaveCandidateChecker:
    """Use only public known cells; initially unknown outputs are unconstrained."""
    def __init__(self, wave, simulator, root):
        from .candidate_policy import SimulationCache
        if isinstance(simulator, SimulationCache):
            raise ValueError('Public waveform checks require an uncached raw simulator')
        self.table, self.simulator, self.root, self.cache = wave, simulator, root, {}
        root.mkdir(parents=True, exist_ok=False)
        (root/'public_waveform.json').write_text(json.dumps(wave.to_dict(),indent=2)+'\n')

    def __call__(self, code):
        identity=hashlib.sha256(code.encode()).hexdigest()
        if identity in self.cache:
            return self.cache[identity]
        upstream=importlib.import_module('chipmate.cross_verify')
        # Pair-verification workers have joined before select_recorded calls a
        # checker. The raw simulator bypasses its unrelated simulation cache.
        with _LOCK:
            previous=upstream.build_stim_testbench
            upstream.build_stim_testbench=lambda *args,**kwargs:waveform_testbench(self.table)
            try:
                actual,error=self.simulator(code,self.table.inputs,self.table.outputs,
                                            [{} for _ in self.table.rows],False,[])
            finally:
                upstream.build_stim_testbench=previous
        total=sum(value is not None for row in self.table.rows for value in row['outputs'].values())
        correct=0
        if not error and actual is not None and len(actual)==len(self.table.rows):
            correct=sum(value is not None and observed.get(name)==value
                        for row,observed in zip(self.table.rows,actual) for name,value in row['outputs'].items())
        score=correct/total
        self.cache[identity]=score
        (self.root/(identity+'.json')).write_text(json.dumps({'candidate_sha256':identity,'source':'model candidate',
            'public_waveform_match_rate':score,'correct_known_cells':correct,'total_known_cells':total,
            'error':error,'actual_masked_outputs':actual},indent=2)+'\n')
        return score

    def feedback(self, code):
        identity=hashlib.sha256(code.encode()).hexdigest();record=json.loads((self.root/(identity+'.json')).read_text())
        actual=record.get('actual_masked_outputs') or [];examples=[]
        for index,row in enumerate(self.table.rows):
            observed=actual[index] if index<len(actual) else {}
            required={name:value for name,value in row['outputs'].items() if value is not None}
            if required and any(observed.get(name)!=value for name,value in required.items()):
                examples.append({'time_ps':row['time_ps'],'inputs':row['inputs'],'required_known_outputs':required,
                                 'observed':observed,'preceding_public_rows':self.table.rows[max(0,index-2):index]})
        return ('The candidate violates sequential waveform observations explicitly supplied in the statement. '
                'Review stored state, sampling edges, and transparent clock-level intervals. '
                'This check changes the clock first, other inputs 1ps later, and observes after another 1ps. '
                'Unknown cells in the public waveform are unconstrained. Repair the model-produced design and '
                'return the complete Verilog in the original answer format.\n'+json.dumps({
                    'compile_or_simulation_error':record['error'],'public_counterexamples':examples[:3]}))
