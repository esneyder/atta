"""`events.jsonl`: one JSON line per transition, plus cost computation."""

from __future__ import annotations

import json
import warnings
from datetime import datetime
from pathlib import Path

from .timeutil import to_iso, utcnow

EVENTS_FILE = "events.jsonl"


class MissingPriceWarning(UserWarning):
    """A model has no price in prices_usd_per_mtok; its cost is recorded as null."""


def cost(model: str | None, usage: dict, prices: dict) -> float | None:
    """USD cost for `usage` ({tokens_in, tokens_out}) at prices per million tokens.

    Missing price -> None plus a MissingPriceWarning, never an exception.
    """
    price = (prices or {}).get(model) if model else None
    if not price:
        warnings.warn(
            f"Sin precio para el modelo «{model}» en prices_usd_per_mtok; costo = null.",
            MissingPriceWarning,
            stacklevel=2,
        )
        return None
    tokens_in = usage.get("tokens_in") or 0
    tokens_out = usage.get("tokens_out") or 0
    total = (tokens_in * price["input"] + tokens_out * price["output"]) / 1_000_000
    return round(total, 6)


def make_event(job: str, from_stage: str, to_stage: str, duration_s: float,
               now: datetime | None = None, *, model: str | None = None,
               tier: str | None = None, tokens_in: int | None = None,
               tokens_out: int | None = None, cost_usd: float | None = None) -> dict:
    event = {
        "ts": to_iso(now or utcnow()),
        "job": job,
        "from": from_stage,
        "to": to_stage,
        "duration_s": duration_s,
    }
    for key, value in (("model", model), ("tier", tier),
                       ("tokens_in", tokens_in), ("tokens_out", tokens_out)):
        if value is not None:
            event[key] = value
    # cost_usd is explicit null when a model ran but had no price.
    if cost_usd is not None or model is not None:
        event["cost_usd"] = cost_usd
    return event


def append_event(job_dir: str | Path, event: dict) -> None:
    line = json.dumps(event, ensure_ascii=False, separators=(", ", ": ")) + "\n"
    with open(Path(job_dir) / EVENTS_FILE, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
        fh.flush()


def read_events(job_dir: str | Path) -> list[dict]:
    path = Path(job_dir) / EVENTS_FILE
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
