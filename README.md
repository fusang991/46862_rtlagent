# RTL competition submission candidate

This directory is an isolated export of the original ChipMATE V/P workflow.
The competition team is `46862`; the private repository is `46862_rtlagent`.

- `agent/` contains the frozen workflow and the pinned ChipMATE library.
- `skill/` documents available skills; the selected original config does not
  enable optional research policies. An identical runtime copy is under
  `agent/skill/` because the frozen code resolves resources there.
- `serve/start.sh` starts/reuses the two local inference services and returns
  when ready. Set `MODEL_ROOT` to the directory containing `chipmate-v-4b` and
  `chipmate-p-4b`; `/models` is the container default.
- `serve/run_api.sh start|status|stop` supervises the authenticated HTTP
  endpoint. Set `FPGACHINA_TOKEN` in the process environment, never in Git.
- `run.sh <task_dir> <out_dir>` and the unchanged `run_baseline.sh` implement
  the competition's single-task directory interfaces. Outside the container,
  set `QIANSAI_PACKAGE_ROOT` to this directory and source `serve/env.sh` before
  invoking the official baseline wrapper; the container declares these model
  service variables directly in its Dockerfile.
- `model/MODEL.md` and `REPORT.md` clearly mark unresolved eligibility and
  verification items. Weight files and runtime results are not included.

The original V/P workflow, model revisions and generation budgets are kept.
The main model's public files are gated and the P model generates Python code;
the organizer must resolve the corresponding rule questions before submission.
The official base image is only accessible within the platform. This repository
does not claim a team-side Docker build or a finished official evaluation.
