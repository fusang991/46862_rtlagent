#!/usr/bin/env bash
# Serving-only paths for the official image's packaged ROCm SDK.
# Keep these out of the workflow environment; vllm_rocm.sh sources this file.
qiansai_gpu_python_root="${QIANSAI_GPU_PYTHON_ROOT:-/opt/python}"
qiansai_gpu_libraries=""
for qiansai_gpu_sdk in _rocm_sdk_devel _rocm_sdk_core; do
    for qiansai_gpu_library in \
        "$qiansai_gpu_python_root"/lib/python*/site-packages/"$qiansai_gpu_sdk"/lib \
        "$qiansai_gpu_python_root"/lib/python*/site-packages/"$qiansai_gpu_sdk"/lib/rocm_sysdeps/lib; do
        if [[ -d "$qiansai_gpu_library" ]]; then
            qiansai_gpu_libraries="${qiansai_gpu_libraries:+$qiansai_gpu_libraries:}$qiansai_gpu_library"
        fi
    done
done
if [[ -n "$qiansai_gpu_libraries" ]]; then
    export LD_LIBRARY_PATH="$qiansai_gpu_libraries${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export PATH="$qiansai_gpu_python_root/bin:$PATH"
