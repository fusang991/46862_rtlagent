#!/usr/bin/env bash
set -eo pipefail
QIANSAI_PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export QIANSAI_PACKAGE_ROOT
. "$QIANSAI_PACKAGE_ROOT/serve/env.sh"
exec "$QIANSAI_WORKFLOW_PYTHON" -m qiansai.resident_start \
    --serving "$QIANSAI_PACKAGE_ROOT/configs/serving.yaml" --model-root "$MODEL_ROOT" \
    --logs-root "${SERVE_LOG_DIR:-$QIANSAI_PACKAGE_ROOT/service_logs}"
