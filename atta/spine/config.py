"""Load and validate `atta/config.json`."""

from __future__ import annotations

import copy
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"

DEFAULTS = {
    "integration_branch": "develop",
    "forbidden_branches": ["main", "master"],
    "branch_prefix": "atta/",
    "max_rounds": 3,
    "max_total_rounds": 6,
    "concurrency": 3,
    "labels": {
        "queue": "atta",
        "needs_info": "atta:needs-info",
        "needs_human": "atta:needs-human",
    },
    "protected_paths": ["atta/**", ".claude/**", ".pi/**", ".github/workflows/**"],
    "prices_usd_per_mtok": {},
}


class ConfigError(ValueError):
    """config.json is unreadable or violates a rule."""


def _err(msg: str) -> ConfigError:
    return ConfigError(f"config.json: {msg}")


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def validate(cfg: dict) -> dict:
    """Raise ConfigError on the first invalid value; return cfg unchanged."""
    for key in ("integration_branch", "branch_prefix"):
        if not isinstance(cfg[key], str) or not cfg[key].strip():
            raise _err(f"{key} debe ser un texto no vacío.")
    for key in ("forbidden_branches", "protected_paths"):
        if not _is_str_list(cfg[key]):
            raise _err(f"{key} debe ser una lista de textos.")
    for key in ("max_rounds", "max_total_rounds", "concurrency"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise _err(f"{key} debe ser un entero mayor que 0.")
    if not cfg["branch_prefix"].endswith("/"):
        raise _err("branch_prefix debe terminar en «/».")
    labels = cfg["labels"]
    if not isinstance(labels, dict) or not all(
        isinstance(labels.get(k), str) and labels.get(k) for k in DEFAULTS["labels"]
    ):
        raise _err("labels debe tener queue, needs_info y needs_human como textos.")
    prices = cfg["prices_usd_per_mtok"]
    if not isinstance(prices, dict):
        raise _err("prices_usd_per_mtok debe ser un objeto.")
    for model, price in prices.items():
        if not isinstance(price, dict) or not all(
            isinstance(price.get(k), (int, float)) and not isinstance(price.get(k), bool)
            for k in ("input", "output")
        ):
            raise _err(f"prices_usd_per_mtok[«{model}»] debe tener input y output numéricos.")
    branch = cfg["integration_branch"]
    if branch.strip().lower() in {b.strip().lower() for b in cfg["forbidden_branches"]}:
        raise _err(
            f"integration_branch «{branch}» está en forbidden_branches; "
            "Atta nunca integra en una rama prohibida."
        )
    return cfg


def with_defaults(data: dict) -> dict:
    """Merge user values over DEFAULTS (unknown keys are kept for later features) and validate."""
    if not isinstance(data, dict):
        raise _err("debe ser un objeto JSON.")
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(copy.deepcopy(data))
    return validate(cfg)


def load_config(path: str | Path | None = None) -> dict:
    """Read config.json (default: atta/config.json) and return a validated dict."""
    p = Path(path) if path is not None else CONFIG_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"No se pudo leer {p}: {exc.strerror or exc}.") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json no es JSON válido: {exc}") from exc
    return with_defaults(data)
