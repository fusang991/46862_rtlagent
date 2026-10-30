#!/usr/bin/env bash
set -eo pipefail
QIANSAI_PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export QIANSAI_PACKAGE_ROOT
. "$QIANSAI_PACKAGE_ROOT/serve/env.sh"
exec "$QIANSAI_WORKFLOW_PYTHON" -m qiansai.official_entry "$1" "$2" \
    --serving "$QIANSAI_PACKAGE_ROOT/configs/serving.yaml" \
    --config "$QIANSAI_PACKAGE_ROOT/configs/workflow.yaml" \
    --deadline-s "${AGENT_DEADLINE_S:-900}"
