#!/usr/bin/env bash
set -eo pipefail
QIANSAI_PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export QIANSAI_PACKAGE_ROOT
. "$QIANSAI_PACKAGE_ROOT/serve/env.sh"
: "${FPGACHINA_TOKEN:?Set the organiser token before starting the submission endpoint}"
"$QIANSAI_PACKAGE_ROOT/serve/start.sh"
exec "$QIANSAI_WORKFLOW_PYTHON" -m qiansai.submission \
    --package "$QIANSAI_PACKAGE_ROOT" --serving "$QIANSAI_PACKAGE_ROOT/configs/serving.yaml" \
    --artifacts "${QIANSAI_JOB_ARTIFACTS:-$QIANSAI_PACKAGE_ROOT/jobs}" --port "${PORT:-7860}"
