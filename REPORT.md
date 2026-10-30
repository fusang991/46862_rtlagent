# RTL Agent Design Report — Draft

Team number: 46862. Final author details: to be supplied by the team. This
document is a draft and is not a claim that competition compliance has been approved.

## 1. Objective

Generate Verilog/SystemVerilog for an unseen specification using local models,
with an independently runnable same-model official baseline.

## 2. Architecture

ChipMATE-V-4B proposes RTL and ChipMATE-P-4B proposes Python behavior. The
original multi-agent workflow cross-verifies generated candidates using Vivado.
Two local inference services share one GPU; an authenticated asynchronous HTTP
service exposes the solution and baseline modes. No golden implementation or
official testbench is supplied to generation.

## 3. Models and inference

See `model/MODEL.md` and `manifest.json`. Both checkpoints were published by
ChipMATE and have not been additionally trained by this team. The V checkpoint
is the main model; P generates Python behavioral models. The gated weight
access and the competition classification of P need organizer confirmation.

## 4. Agent and 5. Skills

The selected configuration is the original n=3, at most three-turn V/P flow
with 1,000 generated verification stimuli. Optional research policies and
same-task answer material are not enabled in this submitted configuration.
The included skill documentation is provided for inspection; its presence alone
does not establish that an optional skill is active or improves accuracy.

## 6. Historical development results

On the 156 publicly available converted VerilogEval tasks, a historical W7900D
and Vivado comparison used one sample per task per mode: the original V/P flow
had 116 L3, 32 L1 and 8 L0; the official single-V baseline had 75 L3, 40 L1
and 41 L0. Reported ability/gain formula values were 23.54/30 and 35.98/40.
These are development-set results, not a private competition score, a five-
sample stability result, or the full score. One agent execution error on task
031 and known evaluator/input limitations were retained in the source record.

## 7. Reproduction

Prepare the pinned model files locally, configure the official Vivado license,
then run `serve/start.sh` and use `run.sh <task_dir> <out_dir>` or the unchanged
official baseline. See README.md. The competition-provided Docker base is
available inside the platform; teams are not required to build the image there.

## 8. Failure analysis

Public development results contain functional, interface and execution failures.
The two-model agreement is internal evidence, not an independently judged pass.
The public main-model files are gated and the P-model policy classification is
unresolved. Current package validation and competition-endpoint status must be
added after the corresponding checks; do not treat prior scores as new results.

## 9. Design contributions and 10. Conclusions

The submitted default preserves the existing V/P verification loop, isolated
model services, official baseline and deadline-controlled single-task wrapper.
Further comparison with held-out tasks, service endurance and official
reproduction are still required.

## 11. References

- ChipMATE upstream, commit `d94087b5159aea482648b26398a2ffb164246671`.
- Competition reference repository; exact version is in `provenance.json`.
- Model cards and pinned weight revisions are listed in `model/MODEL.md`.

## 12. Large-model collaboration record

The team used a coding assistant during development and validation. The exact
model/tool versions, team-vs-assistant contribution attribution, accepted and
rejected suggestions, and validation evidence must be completed by the team.

## 13. Appendix

Complete task-level results, logs, training-source details and final competition
validation are to be supplied in the final report; they are not bundled here.
