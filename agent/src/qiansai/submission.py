"""Local asynchronous competition endpoint; workflow-only Python dependencies."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import threading
import time
from urllib.parse import urlsplit
import uuid

import yaml

from .official_entry import supervise
from .services import model_ready
from .telemetry import gpu_snapshot


class Submission:
    def __init__(self, package: Path, serving: dict, artifacts: Path, token: str, *, ready=None):
        if not token:
            raise ValueError("FPGACHINA_TOKEN must be set")
        self.package, self.serving = package.resolve(), serving
        self.artifacts = artifacts.resolve()
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.ready = ready or (lambda: all(model_ready(serving[role]) for role in ('v', 'p')))
        self.jobs, self.nonces = {}, {}
        self.lock, self.queue = threading.Lock(), Queue()
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def public(self, job):
        result = {key: job[key] for key in ('job_id', 'task_id', 'status')}
        if job['status'] == 'done':
            result.update({key: job[key] for key in ('solution', 'trace', 'elapsed_s')})
        return result

    def submit(self, request):
        if not isinstance(request, dict):raise ValueError('Expected a JSON object')
        if not isinstance(request.get('task_id'), str) or not request['task_id']:
            raise ValueError('task_id must be a nonempty string')
        if not isinstance(request.get('prompt'), str):raise ValueError('prompt must be a string')
        mode, nonce, interface = request.get('mode', 'agent'), request.get('nonce', ''), request.get('interface', '')
        if mode not in ('agent', 'baseline'):raise ValueError('mode must be agent or baseline')
        if not isinstance(nonce, str) or not isinstance(interface, str):raise ValueError('nonce and interface must be strings')
        deadline = request.get('deadline_s', 900)
        if isinstance(deadline, bool) or not isinstance(deadline, int) or not 1 <= deadline <= 900:
            raise ValueError('deadline_s must be an integer from 1 to 900')
        normalized = {**request, 'mode': mode, 'nonce': nonce, 'interface': interface, 'deadline_s': deadline}
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if nonce and nonce in self.nonces:
                job = self.jobs[self.nonces[nonce]]
                if job['request_sha256'] != digest:raise ValueError('nonce was already used for a different request')
                return self.public(job)
            identity = uuid.uuid4().hex
            directory = self.artifacts / identity
            directory.mkdir(exist_ok=False)
            job = {'job_id': identity, 'task_id': request['task_id'], 'status': 'queued',
                   'request': normalized, 'request_sha256': digest,
                   'submitted_at': datetime.now(timezone.utc).isoformat(),
                   'submitted_monotonic': time.monotonic(), 'directory': directory}
            (directory/'request.json').write_text(json.dumps(normalized, indent=2)+'\n')
            self.jobs[identity] = job
            if nonce:self.nonces[nonce] = identity
            self._persist(job)
            self.queue.put(identity)
            return self.public(job)

    def _persist(self, job):
        result = {key: value for key, value in job.items() if key not in ('request', 'directory', 'submitted_monotonic')}
        temporary = job['directory']/'job.json.tmp'
        temporary.write_text(json.dumps(result, indent=2)+'\n')
        temporary.replace(job['directory']/'job.json')

    def _worker(self):
        while True:
            identity = self.queue.get()
            with self.lock:
                job = self.jobs[identity]
                job['status'] = 'running'
                self._persist(job)
            try:
                self._solve(job)
            except Exception as error:
                # Preserve the failure for the operator; the public contract
                # returns an empty solution rather than a permanently running job.
                job['operator_error'] = f'{type(error).__name__}: {error}'
                job['solution'], job['trace'] = '', ''
            with self.lock:
                job.update(status='done', elapsed_s=round(time.monotonic()-job['submitted_monotonic'], 3))
                self._persist(job)
            self.queue.task_done()

    def _solve(self, job):
        req, directory = job['request'], job['directory']
        inp, out = directory/'input', directory/'output'
        inp.mkdir();out.mkdir()
        (inp/'prompt.txt').write_text(req['prompt'])
        (inp/'interface.txt').write_text(req['interface'])
        remaining = req['deadline_s']-(time.monotonic()-job['submitted_monotonic'])
        if remaining <= 0:
            job.update(solution='', trace='', budget_exhausted_in_queue=True)
            return
        script = 'run_baseline.sh' if req['mode']=='baseline' else 'run.sh'
        environment = {**os.environ, 'AGENT_DEADLINE_S': str(max(1, int(remaining)))}
        environment.pop('FPGACHINA_TOKEN', None)
        # The same outer supervisor and remaining budget apply to both modes.
        operation = supervise([str(self.package/script), str(inp), str(out)],
                              directory/'command.log', max(.01, remaining-1), env=environment,
                              cwd=self.package)
        (directory/'command.json').write_text(json.dumps(operation, indent=2)+'\n')
        job['solution'] = (out/'solution.v').read_text() if (out/'solution.v').exists() else ''
        job['trace'] = (out/'trace.jsonl').read_text() if (out/'trace.jsonl').exists() else ''

    def handler(self):
        application = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # Never include request headers or bearer credentials in logs.
                return

            def respond(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                actual = self.headers.get('Authorization', '')
                if not hmac.compare_digest(actual, 'Bearer '+application.token):
                    self.respond(401, {'detail': 'bad token'})
                    return False
                return True

            def do_GET(self):
                if not self.authorized():return
                path = urlsplit(self.path).path
                if path == '/v1/health':
                    try:
                        ready = application.ready()
                        memory = sum(card['used_bytes'] for card in gpu_snapshot())/1e9
                    except Exception:
                        ready, memory = False, 0.0
                    self.respond(200, {'ready': bool(ready), 'track': 'rtl',
                                      'model': application.serving['v']['model'], 'vram_gb': round(memory, 3)})
                elif path.startswith('/v1/jobs/'):
                    with application.lock:
                        job = application.jobs.get(path.rsplit('/', 1)[1])
                        result = application.public(job) if job else None
                    self.respond(200 if result else 404, result or {'detail': 'unknown job_id'})
                else:self.respond(404, {'detail': 'unknown endpoint'})

            def do_POST(self):
                if not self.authorized():return
                if urlsplit(self.path).path != '/v1/solve':
                    self.respond(404, {'detail': 'unknown endpoint'});return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 1_048_576:raise ValueError('Invalid request body size')
                    request = json.loads(self.rfile.read(length))
                    result = application.submit(request)
                except (ValueError, UnicodeDecodeError) as error:
                    self.respond(422, {'detail': str(error)});return
                self.respond(202, result)
        return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--serving', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--port', type=int, default=7860)
    args = parser.parse_args()
    serving = yaml.safe_load(args.serving.read_text())
    application = Submission(args.package, serving, args.artifacts, os.environ.get('FPGACHINA_TOKEN', ''))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), application.handler())
    print(f'RTL submission endpoint listening on 127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':main()
