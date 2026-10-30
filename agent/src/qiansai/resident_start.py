"""Detach model services once and return only after both model identities match."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import yaml

from .services import model_ready


def start(serving: Path, model_root: Path, logs: Path, timeout: float = 1200):
    config = yaml.safe_load(serving.read_text())
    if all(model_ready(config[role]) for role in ('v', 'p')):
        return {'mode': 'reused'}
    logs.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, '-m', 'qiansai.services', '--serving', str(serving),
               '--model-root', str(model_root), '--logs', str(logs/'models'),
               '--startup-timeout', str(timeout)]
    with (logs/'supervisor.log').open('x') as stream:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                    stderr=subprocess.STDOUT, env=os.environ.copy(), start_new_session=True)
    record = {'mode': 'started', 'pid': process.pid, 'argv': command}
    (logs/'supervisor.json').write_text(json.dumps(record, indent=2)+'\n')
    started = time.monotonic()
    try:
        while not all(model_ready(config[role]) for role in ('v', 'p')):
            if process.poll() is not None:raise RuntimeError('Model supervisor exited; inspect '+str(logs))
            if time.monotonic()-started >= timeout:raise RuntimeError('Model startup timed out; inspect '+str(logs))
            time.sleep(1)
    except BaseException:
        process.terminate()  # Supervisor closes only its owned model processes.
        raise
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serving', type=Path, required=True)
    parser.add_argument('--model-root', type=Path, required=True)
    parser.add_argument('--logs-root', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=1200)
    args = parser.parse_args()
    directory = args.logs_root/(datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    print(json.dumps(start(args.serving.resolve(), args.model_root.resolve(), directory, args.timeout)), flush=True)


if __name__ == '__main__':main()
