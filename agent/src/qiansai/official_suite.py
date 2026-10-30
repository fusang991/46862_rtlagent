"""Run the unmodified official selftest with our separately served workflow."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

import yaml

from .official_entry import terminate_tree
from .services import ModelServices

ROOT = Path(__file__).resolve().parents[2]
BASELINE_SHA256 = "17aaa609d4dbd36a94d1390e39f769394448f8588f3ccdd2bd37db9e34ed05e2"


def run_suite(args) -> dict:
    out = args.run_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    official = args.official.resolve()
    serving_path, workflow_path = args.serving.resolve(), args.config.resolve()
    serving = yaml.safe_load(serving_path.read_text())
    baseline = official / "example/baseline.py"
    if hashlib.sha256(baseline.read_bytes()).hexdigest() != BASELINE_SHA256:
        raise ValueError("Official baseline identity differs from the verified f7a8bd4 version")
    package = out / "package"
    package.mkdir()
    for name in ("baseline.py", "run_baseline.sh"):
        shutil.copy2(official / "example" / name, package / name)
    (package / "manifest.json").write_text(json.dumps({"generation": {}}) + "\n")
    command = [sys.executable, "-m", "qiansai.official_entry", '"$1"', '"$2"',
               "--serving", str(serving_path), "--config", str(workflow_path),
               "--deadline-s", str(args.deadline_s)]
    shell_command = " ".join(value if value in ('"$1"', '"$2"') else shlex.quote(value)
                             for value in command)
    (package / "run.sh").write_text("#!/usr/bin/env bash\nset -eo pipefail\nexec " + shell_command + "\n")
    (package / "run.sh").chmod(0o755)
    environment = {**os.environ, "LLM_BACKEND": "openai",
                   "LLM_BASE_URL": serving["v"]["base_url"], "MODEL_NAME": serving["v"]["model"],
                   "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
                   "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT / "third_party/chipmate"),
                   "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
                   "SELFTEST_KEEP_WORK": "1"}
    environment['QIANSAI_OFFICIAL_BASELINE'] = str(package/'baseline.py')
    for name in ("LLM_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        environment.pop(name, None)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = environment["NO_PROXY"]
    os.environ["QIANSAI_VLLM_EXECUTABLE"] = str(ROOT / "serve/vllm_rocm.sh")
    argv = ["bash", str(official / "selftest/run_selftest.sh"), "--agent", str(package),
            "--tasks", str(args.tasks.resolve()), "--out", str(out / "selftest"),
            "--samples", str(args.samples), "--timeout", str(args.judge_timeout)]
    if getattr(args, "no_baseline", False):
        argv.append("--no-baseline")
    selected = sorted(p.parent.name for p in args.tasks.glob("*/task.json"))
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "argv": argv,
                "selected": selected, "samples": args.samples,
                "serving": serving, "workflow": yaml.safe_load(workflow_path.read_text()),
                "official_baseline_sha256": BASELINE_SHA256,
                "scope": "Official development selftest; not the private competition score"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    started = time.monotonic()
    result = {"status": "running"}
    try:
        with ModelServices(serving, args.model_root, out / "services", autostart=not args.no_start,
                           startup_timeout=args.startup_timeout,
                           models_manifest=ROOT / "model/manifest.json") as services:
            (out / "services.json").write_text(json.dumps(services.info, indent=2) + "\n")
            with (out / "selftest.stdout").open("x") as stream:
                process = subprocess.Popen(argv, env=environment, stdout=stream,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    while True:
                        try:
                            process.wait(timeout=5)
                            break
                        except subprocess.TimeoutExpired:
                            services.check()
                except BaseException:
                    terminate_tree(process)
                    raise
            result = {"status": "completed" if process.returncode == 0 else "execution_error",
                      "returncode": process.returncode}
    except BaseException as error:
        result = {"status": "execution_error", "error": str(error)}
        raise
    finally:
        result["wall_seconds_including_startup_and_judging"] = time.monotonic() - started
        result["expected_tasks_per_mode"] = len(selected)
        result["judge_results"] = len(list((out / "selftest/results").glob("*.json")))
        entries = [json.loads(p.read_text()) for p in (out / "selftest/agent").glob("*/s*/entry.json")]
        result["agent_timeouts"] = sum(row["timed_out"] for row in entries)
        result["agent_execution_errors"] = sum(bool(row["returncode"]) and not row["timed_out"] for row in entries)
        result["agent_entry_seconds"] = sum(row["seconds"] for row in entries)
        (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--serving", type=Path, default=ROOT / "configs/serving.rocm.vivado.yaml")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/chipmate_4b.yaml")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--deadline-s", type=float, default=900)
    parser.add_argument("--judge-timeout", type=float, default=300)
    parser.add_argument("--startup-timeout", type=float, default=1200)
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--no-baseline", action="store_true",
                        help="Official agent-only ablation; a reused baseline must be explicitly documented")
    args = parser.parse_args()
    if args.samples < 1 or args.deadline_s <= 0 or args.judge_timeout <= 0:
        parser.error("Samples and time budgets must be positive")
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    result = run_suite(args)
    print(json.dumps(result), flush=True)
    if result["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
