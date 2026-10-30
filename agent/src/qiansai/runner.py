"""Thin configuration adapter around the unchanged ChipMATE CLI."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def parser(description: str) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--input", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--limit", type=int)
    result.add_argument("--config", type=Path, default=ROOT / "configs/chipmate_4b.yaml")
    result.add_argument("--serving", type=Path, default=ROOT / "configs/serving.yaml")
    return result


def settings(args) -> tuple[dict, dict]:
    if not args.input.is_file():
        raise ValueError(f"Missing input: {args.input}")
    if args.out.exists():
        raise ValueError(f"Refusing to overwrite existing results: {args.out}")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    workflow = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    serving = yaml.safe_load(args.serving.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    return workflow, serving


def main() -> None:
    if not any(arg.split("=", 1)[0] == "--input" for arg in sys.argv[1:]):
        from .case_runner import main as run_case
        run_case()
        return
    args = parser("Run the pinned original ChipMATE workflow").parse_args()
    workflow, serving = settings(args)
    command = [
        sys.executable, "-m", "chipmate.cli",
        "--input", str(args.input), "--out", str(args.out),
        "--provider", "openai-compat", "--api-key", "local",
        "--model", serving["v"]["model"], "--base-url", serving["v"]["base_url"],
        "--p-model", serving["p"]["model"], "--p-base-url", serving["p"]["base_url"],
    ]
    for key in ("n", "max_turns", "num_verify_tests", "temperature", "seed", "workers"):
        command.extend(["--" + key.replace("_", "-"), str(workflow[key])])
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
