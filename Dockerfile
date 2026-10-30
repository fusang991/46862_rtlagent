# Build context: an isolated qiansai.package output, never the research checkout.
# Supply the organiser's actual W7900/gfx1100 image tag or digest explicitly.
ARG BASE_IMAGE=fpgachina-rtlhls@sha256:a7cf627e11708e8a6dcd9de689ff934f7b450b79369ebc7bb0217e8b54d81e56
FROM ${BASE_IMAGE}

ARG WORKFLOW_PYTHON=/usr/bin/python3
ARG UV_VERSION=0.9.18
WORKDIR /opt/agent

COPY agent/ /opt/agent/agent/
RUN ${WORKFLOW_PYTHON} -m venv /opt/qiansai-workflow \
    && /opt/qiansai-workflow/bin/pip install --no-cache-dir uv==${UV_VERSION} \
    && cd /opt/agent/agent \
    && UV_PROJECT_ENVIRONMENT=/opt/qiansai-workflow \
       /opt/qiansai-workflow/bin/uv sync --frozen --no-dev --no-editable

COPY . /opt/agent/
RUN chmod +x run.sh run_baseline.sh serve/*.sh \
    && mkdir -p /var/tmp/eda \
    && /opt/qiansai-workflow/bin/python -c "import qiansai.official_entry, qiansai.submission"

# Weights, including their verified download_manifest.json, must already be
# present in the read-only /models mount before evaluation. No startup download.
ENV QIANSAI_WORKFLOW_PYTHON=/opt/qiansai-workflow/bin/python \
    MODEL_ROOT=/models \
    QIANSAI_GPU_ENV=/opt/agent/serve/gpu_env.sh \
    EDA_TMP=/var/tmp/eda AGENT_TMP=/var/tmp SELFTEST_TMP=/var/tmp \
    LLM_BASE_URL=http://127.0.0.1:8001/v1 MODEL_NAME=core12345/ChipMATE-V-4B TRACK=rtl \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1

# The base image owns ROCm, vLLM and Vivado. Do not pip-install GPU dependencies
# into the independent workflow environment above. Existing tool/license
# environment variables come from the base image and deployment environment.
EXPOSE 7860
CMD ["/bin/bash"]
