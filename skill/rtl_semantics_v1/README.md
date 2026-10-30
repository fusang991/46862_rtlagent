# RTL semantics review checklist

This experimental skill adds general review instructions to both generation roles.
It contains no task IDs, reference implementations, expected answers, or testbench outputs.
Select it with `skill_policy: rtl_semantics_v1`; omit the setting to preserve the original prompts.
The original workflow's candidate count, temperature, feedback loop, and token budget remain unchanged.
Actual augmented prompts are recorded by the existing traced backend.

It addresses numeric literal conversion, finite widths, reset priority, shift slices, and cycle timing.
It is intended for statement-driven RTL generation and its Python behavioral model. It cannot resolve
contradictory specifications, guarantee agreement with an unavailable testbench, or prove a design correct.
Its effect on model quality must be measured separately from tool compatibility changes.

Source: observed generated-code failures in the first AMD/Vivado smoke, recorded in
[the autonomous study](../../docs/journal/20261017_234742_autonomous_iteration.md).
No improved benchmark result has been measured yet.
