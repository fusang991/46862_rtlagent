"""Build an isolated official directory/API package without weights or task answers."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys

import yaml

from .official_suite import BASELINE_SHA256

ROOT = Path(__file__).resolve().parents[2]


def build(official: Path, out: Path, serving: Path, workflow: Path,
          model_root: Path, python: Path) -> dict:
    baseline = official/'example/baseline.py'
    if hashlib.sha256(baseline.read_bytes()).hexdigest() != BASELINE_SHA256:
        raise ValueError('Official baseline identity differs from the verified version')
    config = yaml.safe_load(serving.read_text())
    out.mkdir(parents=True, exist_ok=False)
    source = out/'source'
    source.mkdir()
    for directory in ['src/qiansai', 'skill']:
        shutil.copytree(ROOT/directory, source/directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    upstream = source/'third_party/chipmate';upstream.mkdir(parents=True)
    shutil.copytree(ROOT/'third_party/chipmate/chipmate', upstream/'chipmate',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for filename in ['pyproject.toml', 'README.md', 'LICENSE']:
        path = ROOT/'third_party/chipmate'/filename
        if path.is_file():shutil.copy2(path, upstream/filename)
    for filename in ['pyproject.toml', 'uv.lock', 'README.md', 'MODEL.md', 'model/manifest.json', 'serve/vllm_rocm.sh']:
        path = source/filename;path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/filename, path)
    (out/'configs').mkdir();(out/'serve').mkdir()
    shutil.copy2(serving, out/'configs/serving.yaml')
    shutil.copy2(workflow, out/'configs/workflow.yaml')
    for filename in ['baseline.py', 'run_baseline.sh']:
        shutil.copy2(official/'example'/filename, out/filename)
    shutil.copy2(official/'LICENSE', out/'OFFICIAL_LICENSE')
    shutil.copy2(ROOT/'MODEL.md', out/'MODEL.md')
    settings = f'''# Deployment paths and model settings; no credentials belong in this file.
if [ -r "${{QIANSAI_EDA_ENV:-/workspace/env.sh}}" ]; then
    . "${{QIANSAI_EDA_ENV:-/workspace/env.sh}}"
fi
export QIANSAI_WORKFLOW_PYTHON="${{QIANSAI_WORKFLOW_PYTHON:-{str(python)}}}"
export MODEL_ROOT="${{MODEL_ROOT:-{str(model_root)}}}"
export PYTHONPATH="$QIANSAI_PACKAGE_ROOT/source/src:$QIANSAI_PACKAGE_ROOT/source/third_party/chipmate"
export PATH="$(dirname "$QIANSAI_WORKFLOW_PYTHON"):$PATH"
export QIANSAI_VLLM_EXECUTABLE="$QIANSAI_PACKAGE_ROOT/source/serve/vllm_rocm.sh"
export LLM_BASE_URL={shlex.quote(config['v']['base_url'])}
export MODEL_NAME={shlex.quote(config['v']['model'])}
export LLM_BACKEND=openai TRACK=rtl
export QIANSAI_OFFICIAL_BASELINE="$QIANSAI_PACKAGE_ROOT/baseline.py"
export NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1
export PYTHONDONTWRITEBYTECODE=1
'''
    # Paths inserted in a parameter-expansion default must not contain shell code.
    if any(c in str(path) for path in (python, model_root) for c in '\n\r"`$\\'):
        raise ValueError('Deployment path contains unsupported shell characters')
    (out/'serve/env.sh').write_text(settings)
    header='''#!/usr/bin/env bash
set -eo pipefail
QIANSAI_PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export QIANSAI_PACKAGE_ROOT
. "$QIANSAI_PACKAGE_ROOT/serve/env.sh"
'''
    (out/'run.sh').write_text(header+'''exec "$QIANSAI_WORKFLOW_PYTHON" -m qiansai.official_entry "$1" "$2" \\
    --serving "$QIANSAI_PACKAGE_ROOT/configs/serving.yaml" \\
    --config "$QIANSAI_PACKAGE_ROOT/configs/workflow.yaml" \\
    --deadline-s "${AGENT_DEADLINE_S:-900}"
''')
    (out/'run_selftest.sh').write_text(header+f'''exec "$QIANSAI_WORKFLOW_PYTHON" -m qiansai.deployed_selftest \\
    --package "$QIANSAI_PACKAGE_ROOT" --official {shlex.quote(str(official))} \\
    --out-base "${{QIANSAI_SELFTEST_ARTIFACTS:-$QIANSAI_PACKAGE_ROOT/../validation_runs}}" "$@"
''')
    serve_header='''#!/usr/bin/env bash
set -eo pipefail
QIANSAI_PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export QIANSAI_PACKAGE_ROOT
. "$QIANSAI_PACKAGE_ROOT/serve/env.sh"
'''
    (out/'serve/start.sh').write_text(serve_header+'''exec "$QIANSAI_WORKFLOW_PYTHON" -m qiansai.resident_start \\
    --serving "$QIANSAI_PACKAGE_ROOT/configs/serving.yaml" --model-root "$MODEL_ROOT" \\
    --logs-root "${SERVE_LOG_DIR:-$QIANSAI_PACKAGE_ROOT/service_logs}"
''')
    (out/'serve/start_http.sh').write_text(serve_header+''': "${FPGACHINA_TOKEN:?Set the organiser token before starting the submission endpoint}"
"$QIANSAI_PACKAGE_ROOT/serve/start.sh"
exec "$QIANSAI_WORKFLOW_PYTHON" -m qiansai.submission \\
    --package "$QIANSAI_PACKAGE_ROOT" --serving "$QIANSAI_PACKAGE_ROOT/configs/serving.yaml" \\
    --artifacts "${QIANSAI_JOB_ARTIFACTS:-$QIANSAI_PACKAGE_ROOT/jobs}" --port "${PORT:-7860}"
''')
    for path in [out/'run.sh', out/'run_selftest.sh', out/'run_baseline.sh', *list((out/'serve').glob('*.sh'))]:path.chmod(0o755)
    manifest = {'generation': {}, 'created_at': datetime.now(timezone.utc).isoformat(),
                'models': json.loads((ROOT/'model/manifest.json').read_text())['models'],
                'official_commit': 'f7a8bd465c2a820e006c7a00ecc7df468974635b',
                'official_baseline_sha256': BASELINE_SHA256,
                'scope': 'Offline directory/API package; independent test evidence required',
                'files': {}}
    (out/'README.md').write_text('''# Local competition package

`serve/start.sh` returns after both models are ready, reusing matching services.
`serve/start_http.sh` starts the asynchronous endpoint on loopback only; set the
organiser token in the process environment. No public tunnel is created here.

`./run_selftest.sh` runs the official example tasks with a fresh unchanged baseline
and prints each independent grade. Select cases with `--case TASK_ID`, or use
`--tasks DIRECTORY --case TASK_ID`; set `--samples 5` for a five-sample development
check. Each command keeps a new results directory and preserves raw failures.

The official baseline and wrapper are unchanged. For direct directory-mode
baseline calls, load the same environment first:

```bash
export QIANSAI_PACKAGE_ROOT="$(pwd)"
. ./serve/env.sh
./run_baseline.sh TASK_DIRECTORY NEW_OUTPUT_DIRECTORY
```

Every HTTP job keeps its request, outputs, actual process logs and trace in a
unique jobs directory. The root project history is not part of generation input.
Rebuild this package to change configuration. Weights remain external in MODEL_ROOT.
Docker rebuilding and physical 32 GB/R9700 validation require separate evidence.
''')
    manifest['files'] = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--official', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--serving', type=Path, required=True)
    parser.add_argument('--workflow', type=Path, required=True)
    parser.add_argument('--model-root', type=Path, required=True)
    parser.add_argument('--workflow-python', type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    result = build(args.official.resolve(), args.out.resolve(), args.serving.resolve(),
                   args.workflow.resolve(), args.model_root.resolve(), args.workflow_python.absolute())
    print(json.dumps({'package': str(args.out), 'verified_files': len(result['files']),
                      'baseline_sha256': result['official_baseline_sha256']}), flush=True)


if __name__ == '__main__':main()
