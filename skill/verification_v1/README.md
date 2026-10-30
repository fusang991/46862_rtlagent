# Vivado verification compatibility checks

These are optional checks around the pinned ChipMATE verifier. They consume the
statement-derived interface and generated candidate programs. They never read the
official reference implementation or testbench, and never produce a design.

| Configuration | Behavior |
| --- | --- |
| `verification_policy: upstream` | Preserve the original verifier metadata and stimulus |
| `reset_names_v1` | Recognize common active-low reset spellings such as resetn/aresetn/rstn |
| `rtl_tools_v1` | Reset-name checks plus preserving an existing TopModule when a helper module comes first |
| `rtl_timing_v1` | The tools checks, single-edge stimulus alignment, literal ANSI port checks, and rejection of a single always block using both edges of clk |

For a single-clock design, timing checks change data after the inactive edge and
observe after the active edge. A clearly global negative-edge statement selects
the falling edge. Mixed-edge statements retain the original stimulus rather than
pretending that one observation point models every phase. This adapter currently
uses the upstream clock port convention `clk`; other clock names and multiple
clock domains require separate treatment.

A port named `clock` can also be an explicit level-sensitive latch control.
Read the supplied waveform before treating it as an implicit periodic clock;
blindly renaming it to `clk` can remove the behavior that needs testing.

Port checks inspect only unambiguous literal ANSI declarations. They do not claim
to parse the full SystemVerilog language or infer parameterized widths. Unsupported
syntax remains for the actual compiler to judge. Rejecting a known unsupported
dual-edge always form is a precheck, not a proof of synthesizability.

Timing policy must be installed **outside** the simulation cache context. A cache
created for one testbench timing must not supply results for another timing. The
standalone candidate replay identifies captured simulations by exact DUT and
emitted testbench hashes, running Vivado again when either changes.

A failed Python candidate does not prove that the RTL is wrong. Optional candidate
policies preserve observed RTL validity when internal agreement ties at zero;
those candidates remain marked unmatched. Conversely, perfect V/P agreement is
not an independent benchmark pass. The official Vivado judge remains the final
independent check, after selection has been frozen.

Unknown output bits are another limit: the statement may explicitly leave some
bits unconstrained. A strict verifier can reject those bits while the official
testbench correctly ignores them. The optional `strong_evidence_v2` acceptance
rule therefore distinguishes compilation failure from incomplete/unknown
simulation output. The earlier v1 rule is preserved for comparison; greater
conservatism can also discard a beneficial selection, so compare measured grades.

Implementation: [verification_policy.py](../../src/qiansai/verification_policy.py),
[vivado.py](../../src/qiansai/vivado.py),
[candidate_policy.py](../../src/qiansai/candidate_policy.py).
Evidence and limitations: [autonomous study](../../docs/journal/20261017_234742_autonomous_iteration.md).
