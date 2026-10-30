# Model declaration

This is a draft submission of the original ChipMATE V/P workflow. The team has
not further fine-tuned these checkpoints. The organizer's acceptance of gated
main-model files and the classification of the Python-generating P model remain
unconfirmed and must be resolved before claiming submission eligibility.

| Role | Model and source | Revision | Purpose |
| --- | --- | --- | --- |
| Main | https://huggingface.co/core12345/ChipMATE-V-4B | `7fbb17249a49d109787bbc024a7a21b83302a1c2` | Generates RTL |
| Additional | https://huggingface.co/core12345/ChipMATE-P-4B | `cf59695163ad3e4b63bcc6c4eaa91b05fedc4a2e` | Generates Python behavioral reference models for cross-verification |

Both checkpoints are published as ChipMATE 4B-family models, BF16 without
quantization, with 9,078,620,536-byte weights each. Their published backbone is
Qwen/Qwen3.5-4B. Each safetensors header lists 4,539,265,536 stored BF16 tensor
elements; this is a tensor-element count, not an independently audited count of
trainable parameters. Both models run locally on the same W7900D GPU. Their file pages currently
require acceptance of access conditions; an unauthenticated config request
returned HTTP 401 on 2026-10-29. The contest rules require the main-model source
to be publicly accessible. No credential is contained in this repository.

The two services run locally on one GPU, each with 24,576-token context, up to
three concurrent sequences and an absolute 14,000,000,000-byte allocation. The
total budget is 32,000,000,000 bytes, including both services and runtime.
On the previously tested W7900D development device, sampled peak total use in
the 14 GB-per-service startup and inference window was 29.128 GB; this is a
historical sampled total, not a new result or a per-model measurement.

The original agent does not explicitly pass chat-template thinking options. The
pinned V/P templates default to `enable_thinking=true`; the manifest declares
that setting for the unchanged official baseline. A local template rendering
check confirmed that omitted and explicit true have identical output for a
simple text prompt. Real request/serving verification is recorded separately.

Weights must already exist under `MODEL_ROOT` before offline evaluation. The
model source and checksum inventory used by the runtime is `agent/model/manifest.json`.
