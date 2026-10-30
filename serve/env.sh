# Deployment settings; do not store credentials in this file.
if [ -r "${QIANSAI_EDA_ENV:-/workspace/env.sh}" ]; then
    . "${QIANSAI_EDA_ENV:-/workspace/env.sh}"
fi
if [ -z "${QIANSAI_WORKFLOW_PYTHON:-}" ]; then
    if [ -x /opt/qiansai-workflow/bin/python ]; then
        QIANSAI_WORKFLOW_PYTHON=/opt/qiansai-workflow/bin/python
    elif [ -x "$QIANSAI_PACKAGE_ROOT/.venv/bin/python" ]; then
        QIANSAI_WORKFLOW_PYTHON="$QIANSAI_PACKAGE_ROOT/.venv/bin/python"
    else
        QIANSAI_WORKFLOW_PYTHON="$(command -v python3)"
    fi
fi
export QIANSAI_WORKFLOW_PYTHON
export MODEL_ROOT="${MODEL_ROOT:-/models}"
export PYTHONPATH="$QIANSAI_PACKAGE_ROOT/agent/src:$QIANSAI_PACKAGE_ROOT/agent/third_party/chipmate"
export PATH="$(dirname "$QIANSAI_WORKFLOW_PYTHON"):$PATH"
export QIANSAI_VLLM_EXECUTABLE="$QIANSAI_PACKAGE_ROOT/agent/serve/vllm_rocm.sh"
export QIANSAI_GPU_ENV="${QIANSAI_GPU_ENV:-$QIANSAI_PACKAGE_ROOT/serve/gpu_env.sh}"
export LLM_BASE_URL=http://127.0.0.1:8001/v1
export MODEL_NAME=core12345/ChipMATE-V-4B
export LLM_BACKEND=openai TRACK=rtl
export QIANSAI_OFFICIAL_BASELINE="$QIANSAI_PACKAGE_ROOT/baseline.py"
export XILINXD_LICENSE_FILE="${XILINXD_LICENSE_FILE:-2100@vivado-lic.fpgachina.svc.cluster.local}"
export FLEXLM_TIMEOUT="${FLEXLM_TIMEOUT:-300000000}"
export EDA_TMP="${EDA_TMP:-/var/tmp/eda}" AGENT_TMP="${AGENT_TMP:-/var/tmp}"
export SELFTEST_TMP="${SELFTEST_TMP:-/var/tmp}"
export NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1
export PYTHONDONTWRITEBYTECODE=1
