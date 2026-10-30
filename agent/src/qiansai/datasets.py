"""Prepare specification-to-RTL tasks without copying reference logic."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def interface_stub(reference: str) -> str:
    """Extract an ANSI module declaration from the pinned VerilogEval sources."""
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", reference, flags=re.DOTALL)
    declaration = re.search(r"\bmodule\s+RefModule\s*\(.*?\)\s*;", clean, re.DOTALL)
    if declaration is None or not re.search(r"\b(input|output|inout)\b", declaration.group()):
        raise ValueError("Expected a VerilogEval ANSI RefModule declaration")
    header = re.sub(r"\bRefModule\b", "TopModule", declaration.group(), count=1)
    return header.strip() + "\nendmodule\n"


def prepare_tasks(dataset: Path) -> list[dict[str, str]]:
    tasks = []
    for prompt in sorted(dataset.glob("*_prompt.txt")):
        task_id = prompt.name.removesuffix("_prompt.txt")
        reference = dataset / f"{task_id}_ref.sv"
        testbench = dataset / f"{task_id}_test.sv"
        if not reference.is_file() or not testbench.is_file():
            raise ValueError(f"Incomplete task: {task_id}")
        tasks.append({
            "task_id": task_id,
            "question": prompt.read_text(encoding="utf-8").strip(),
            "ref_sv": interface_stub(reference.read_text(encoding="utf-8")),
        })
    if not tasks:
        raise ValueError(f"No tasks found under {dataset}; initialize submodules first")
    return tasks


def read_tasks(path: Path) -> list[dict[str, str]]:
    tasks = []
    seen = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        task = json.loads(line)
        for key in ("task_id", "question", "ref_sv"):
            if not isinstance(task.get(key), str) or not task[key].strip():
                raise ValueError(f"{path}:{number}: missing nonempty {key}")
        if task["task_id"] in seen:
            raise ValueError(f"Duplicate task_id: {task['task_id']}")
        seen.add(task["task_id"])
        tasks.append(task)
    if not tasks:
        raise ValueError("Empty task file")
    return tasks


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
