"""One direct V-model completion per task, without skills, tools, or retries."""

from __future__ import annotations

import json
from pathlib import Path
import time

from chipmate.prompts import extract_verilog
from openai import OpenAI

from .datasets import read_tasks
from .runner import parser, settings
from .request_trace import ChatTrace, usage_summary


def main() -> None:
    cli = parser("Generate directly with the same V-model inference service")
    cli.add_argument("--trace-dir", type=Path)
    args = cli.parse_args()
    workflow, serving = settings(args)
    tasks = read_tasks(args.input)
    if args.limit is not None:
        tasks = tasks[:args.limit]
    if args.trace_dir is not None:
        args.trace_dir.mkdir(parents=True, exist_ok=False)
        (args.trace_dir / "inputs.jsonl").write_text(
            "".join(json.dumps(task, ensure_ascii=False) + "\n" for task in tasks), encoding="utf-8",
        )
        (args.trace_dir / "config.json").write_text(json.dumps({
            "workflow": workflow, "serving": serving,
            "protocol": "one user question, one completion, no tool calls or retries",
            "temperature": workflow["temperature"], "top_p": 0.95, "max_tokens": 4096,
        }, indent=2) + "\n")
    client = OpenAI(
        api_key="local", base_url=serving["v"]["base_url"], max_retries=0, timeout=900,
    )
    errors = 0
    with args.out.open("x", encoding="utf-8") as output:
        for task in tasks:
            start = time.monotonic()
            row = {"task_id": task["task_id"], "verilog": ""}
            try:
                kwargs = dict(
                    model=serving["v"]["model"],
                    messages=[{"role": "user", "content": task["question"]}],
                    temperature=workflow["temperature"], top_p=0.95, max_tokens=4096,
                )
                if args.trace_dir is not None:
                    with ChatTrace(client.chat.completions, args.trace_dir / task["task_id"]):
                        response = client.chat.completions.create(**kwargs)
                else:
                    response = client.chat.completions.create(**kwargs)
                raw = response.choices[0].message.content or ""
                row.update(verilog=extract_verilog(raw), response=response.model_dump())
            except Exception as error:
                errors += 1
                row["error"] = str(error)
            row["wall_seconds"] = time.monotonic() - start
            row["tokens"] = usage_summary([row], row["wall_seconds"])
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
