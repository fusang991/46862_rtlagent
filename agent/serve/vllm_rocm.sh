#!/usr/bin/env bash
# Keep the image's GPU paths out of the workflow Python environment.
set -eo pipefail
qiansai_gpu_env="${QIANSAI_GPU_ENV:-/workspace/gpu-env.sh}"
if [[ ! -r "$qiansai_gpu_env" ]]; then
    printf 'Missing GPU environment: %s\n' "$qiansai_gpu_env" >&2
    exit 2
fi
source "$qiansai_gpu_env"
exec "${QIANSAI_ROCM_VLLM:-/opt/python/bin/vllm}" "$@"
