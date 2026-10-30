# Model and inference declaration

This declaration records the development deployment. The selected package uses
conservative public checks, specialization for explicitly all-negative-edge
statements, and direct V generation only for explicit mux repair prompts.
Independent results and every exploratory variant are preserved separately.

## Weights and roles

| Role | Published checkpoint | Pinned revision | Use |
| --- | --- | --- | --- |
| V | [core12345/ChipMATE-V-4B](https://huggingface.co/core12345/ChipMATE-V-4B) | `7fbb17249a49d109787bbc024a7a21b83302a1c2` | Generate complete Verilog/SystemVerilog designs |
| P | [core12345/ChipMATE-P-4B](https://huggingface.co/core12345/ChipMATE-P-4B) | `cf59695163ad3e4b63bcc6c4eaa91b05fedc4a2e` | Independently generate Python behavioral models for cross-verification |

Both are the published ChipMATE 4B-family checkpoints. This project has performed
no additional training or fine-tuning. Both roles run on the same GPU. P is part
of the agent; the official baseline calls V directly and does not call P.

Weights use BF16, with no weight quantization. Each safetensors file is
9,078,620,536 bytes. SHA-256:

- V: `1e829e646df26f4882bceaf6b6f9629c1a1a317d63f66b40e51ff8d92d458826`
- P: `8ac6cacb9bcd834e28d008e510e6af28923105e471c22f05cee2130a8f64fb4d`

[model/manifest.json](model/manifest.json) is the machine-readable identity record.
All downloaded model files, both weight hashes, and the source transfer archive
were checked before the first AMD inference. Model files total about 18.20 GB.
Only local weight directories are used during evaluation; Hugging Face offline
mode is enabled. Weights and environments are outside ordinary Git history.

## Inference settings

The measured AMD deployment uses the image's separate GPU Python environment:
Python 3.14.7, Torch 2.12.0+rocm10.0.0, and
vLLM 0.27.1.dev5+gf46a9dfe2.d20260827.rocm100. The workflow uses its own Python
3.12.3 environment and accesses the models over loopback HTTP.

- Context limit: 24,576 tokens for each model; no project-added RoPE extrapolation.
- Maximum simultaneous sequences: 3 per service.
- Agent: 3 candidates per role, at most 3 original generation rounds, temperature
  1.0, output limit 4,096 tokens per request. Optional recovery budgets and prompts
  are recorded in each experiment's workflow configuration and request trace.
- Baseline: the official unchanged baseline script, with its default generation
  configuration; output limit 16,384 tokens and request timeout 840 seconds.
- Per-task outer deadline: 900 seconds. Existing candidate checkpoints survive an
  internal timeout. Model startup is performed before task submission.
- Optional interface inference calls V once with n=1, temperature 0 and a
  1,024-token limit; it emits only a port header. Bug-fix header review also
  validates that named ports are neither added nor removed.
- In the selected optional `mux_bugfix_direct_v1` route, explicit mux repair
  statements call V through the unchanged SHA-verified official baseline script:
  one call, temperature 0, top_p 1, 16,384 output tokens, 840-second HTTP timeout.
  P is not called in that route. The same outer task budget and generation
  thinking settings apply. Other tasks retain the dual-model workflow.
  A broader bug-fix route is retained only as an experiment after a non-mux
  control failed. The router uses statement wording, never task IDs or grades.
- The two services are local-only on ports 8001 and 8002; serving uses eager mode
  and the language model path. Full process commands are retained in run artifacts.

The original generation workflow remains pinned to ChipMATE commit
`d94087b5159aea482648b26398a2ffb164246671`. Optional improvements are separate
configurations. Public waveform checks only inspect **model-generated** candidates
and may provide counterexamples for another model call; they do not emit a design.
Reference implementations and official testbenches are opened only by independent
evaluation after candidate selection.

## GPU budget and observed limits

The available development card is a W7900D/gfx1100 with 51,522,830,336 bytes of
physical VRAM. It is not a physical 32 GB validation card. The study samples total
device allocation every second and compares it with **32,000,000,000 bytes**.
Both models and runtime overhead are included; the sampled peak is not an
instantaneous upper-bound proof.

The initial three-example smoke used 0.30 of this device per service and peaked
at 31,717,519,360 bytes. The completed original study and subsequent warm experiments used 0.28 per service;
its observed peak is 29,861,683,200 bytes. Final full-run telemetry remains
the authoritative result.

[configs/serving.rocm.vivado.yaml](configs/serving.rocm.vivado.yaml) records the
measured 48 GiB development configuration. Its fractional allocation must not be
blindly reused on a smaller card. The optional
[configs/serving.rocm.32gb.yaml](configs/serving.rocm.32gb.yaml) requests an absolute
14,000,000,000-byte allocation per service and derives the vLLM fraction from
actual single-card capacity, reserving the remainder for runtime overhead.
A real cold startup with this absolute allocation passed on 2026-10-18, after
confirming the previous services exited and GPU usage fell to 28,028,928 bytes.
Both new services started in 80.24 seconds with a derived fraction of
0.2717242028184529 each on the available card. Subsequent inference memory is
sampled every 0.2 seconds in a separate validation window. Physical 32 GB and
R9700/gfx1201 compatibility have not been measured.

## Official baseline and results

The baseline is the exact file from official commit
`f7a8bd465c2a820e006c7a00ecc7df468974635b`, SHA-256
`17aaa609d4dbd36a94d1390e39f769394448f8588f3ccdd2bd37db9e34ed05e2`.
Each official selftest package copies it without edits. Baseline and agent share
V's model, BF16 format, endpoint and context limit.

The original full AMD/Vivado control completed all 156 tasks, one sample each:
agent 116 L3 / 32 L1 / 8 L0, baseline 75 L3 / 40 L1 / 41 L0. Official set scores
are 0.7846 and 0.5321, capability 23.54/30 and gain 35.98/40. Cost and engineering
points are unmeasured. These public development results are separate from both
older NVIDIA/Icarus results and ongoing fresh strategy experiments.

Measured scores, stage timings, model calls, failures and GPU telemetry are linked
from [the autonomous study journal](docs/journal/20261017_234742_autonomous_iteration.md).
The public development set, historical CPU controls, original GPU runs, candidate
replays, and fresh model ablations are reported separately. No private competition
score or unmeasured engineering/cost score is claimed.
