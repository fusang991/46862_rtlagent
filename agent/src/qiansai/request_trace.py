"""Observe SDK completions without changing generation arguments or retries."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
import time


def usage_summary(records: list[dict], seconds: float) -> dict:
    successful = [row for row in records if "response" in row]
    usages = [row["response"].get("usage") or {} for row in successful]
    complete = bool(successful) and all(
        type(usage.get(key)) is int and usage[key] >= 0
        for usage in usages for key in ("prompt_tokens", "completion_tokens")
    )
    prompt = sum(usage["prompt_tokens"] for usage in usages) if complete else None
    completion = sum(usage["completion_tokens"] for usage in usages) if complete else None
    return {
        "source": "chat_completion_response.usage",
        "requests": len(records), "successful_requests": len(successful),
        "failed_requests": len(records) - len(successful),
        "usage_complete": complete, "prompt_tokens": prompt,
        "completion_tokens": completion, "wall_seconds": seconds,
        "output_tokens_per_second": completion / seconds if complete and seconds > 0 else None,
        "rate_scope": "successful output tokens / observed wall time, includes prefill and waiting; not decode-only",
        "retry_scope": "SDK create calls; internal SDK HTTP retries are not separate records",
    }


class ChatTrace:
    """An instance-local wrapper, safe for the upstream parallel completions."""

    def __init__(self, completions, directory: Path):
        self.completions = completions
        self.directory = directory
        self.records: list[dict] = []
        self.lock = Lock()
        self.counter = 0

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        self.original = self.completions.create
        self.completions.create = self.create
        return self

    def create(self, *args, **kwargs):
        with self.lock:
            self.counter += 1
            number = self.counter
        # Explicit allowlist: never serialize credentials, client config or headers.
        request = {key: kwargs[key] for key in (
            "model", "messages", "temperature", "top_p", "max_tokens", "n", "seed",
        ) if key in kwargs}
        record = {
            "started_at": datetime.now(timezone.utc).isoformat(), "request": request,
        }
        started = time.monotonic()
        try:
            response = self.original(*args, **kwargs)
            record["response"] = response.model_dump(mode="json")
            return response
        except Exception as error:
            record["error"] = str(error)
            raise
        finally:
            record["seconds"] = time.monotonic() - started
            record["tokens"] = usage_summary([record], record["seconds"])
            with (self.directory / f"request_{number:04d}.json").open("x") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            with self.lock:
                self.records.append(record)

    def __exit__(self, *args):
        self.completions.create = self.original
