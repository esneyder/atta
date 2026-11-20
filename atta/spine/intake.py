"""Parse the header of `intake.md` (the Exploradora's contract, arquitectura §3).

The header is the block of `KEY: value` lines before the first blank line:

    DECISION: ACCEPT|REJECT|NEEDS-INFO     (always the first line)
    RISK: low|medium|high
    SURFACES: none|ui|security|ui,security
    BLOCKED-BY: #n[, #m]                   (optional)
    MIGRATION: yes|no                      (optional)
"""

from __future__ import annotations

import re
from pathlib import Path

DECISIONS = ("ACCEPT", "REJECT", "NEEDS-INFO")
RISKS = ("low", "medium", "high")
SURFACES = ("ui", "security")
REQUIRED = ("DECISION", "RISK", "SURFACES")
OPTIONAL = ("BLOCKED-BY", "MIGRATION")

_LINE = re.compile(r"([A-Z][A-Z-]*): ?(.*)\Z")
_BLOCKED = re.compile(r"#([1-9][0-9]*)(?:, ?#([1-9][0-9]*))*\Z")


class IntakeError(ValueError):
    """intake.md is unreadable or its header breaks the contract."""


def _bad(lineno: int, key: str, value: str, expected: str) -> IntakeError:
    return IntakeError(f"intake.md: línea {lineno}: {key} «{value}» no es válido; usa {expected}.")


def _surfaces(value: str, lineno: int) -> list[str]:
    if value == "none":
        return []
    parts = value.split(",")
    if any(p not in SURFACES for p in parts) or len(set(parts)) != len(parts):
        raise _bad(lineno, "SURFACES", value, "none, ui, security o ui,security")
    return parts


def _blocked_by(value: str, lineno: int) -> list[int]:
    if not _BLOCKED.match(value):
        raise _bad(lineno, "BLOCKED-BY", value, "#n o #n, #m")
    numbers = [int(n) for n in re.findall(r"#([0-9]+)", value)]
    if len(set(numbers)) != len(numbers):
        raise _bad(lineno, "BLOCKED-BY", value, "#n o #n, #m")
    return numbers


def parse_header(text: str) -> dict:
    """Return {decision, risk, surfaces, blocked_by, migration}; raise IntakeError otherwise."""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line:
            break
        lines.append(line)
    if not lines or not lines[0].startswith("DECISION:"):
        raise IntakeError(
            "intake.md: la primera línea debe ser «DECISION: ACCEPT|REJECT|NEEDS-INFO»."
        )
    seen: dict[str, tuple[int, str]] = {}
    for lineno, line in enumerate(lines, start=1):
        m = _LINE.match(line)
        if not m:
            raise IntakeError(f"intake.md: línea {lineno}: «{line}» no tiene la forma «CLAVE: valor».")
        key, value = m.group(1), m.group(2).strip()
        if key not in REQUIRED and key not in OPTIONAL:
            raise IntakeError(f"intake.md: línea {lineno}: clave desconocida «{key}».")
        if key in seen:
            raise IntakeError(f"intake.md: línea {lineno}: {key} aparece dos veces.")
        seen[key] = (lineno, value)
    for key in REQUIRED:
        if key not in seen:
            raise IntakeError(f"intake.md: falta {key} en la cabecera.")

    lineno, decision = seen["DECISION"]
    if decision not in DECISIONS:
        raise _bad(lineno, "DECISION", decision, "ACCEPT, REJECT o NEEDS-INFO")
    lineno, risk = seen["RISK"]
    if risk not in RISKS:
        raise _bad(lineno, "RISK", risk, "low, medium o high")
    surfaces = _surfaces(seen["SURFACES"][1], seen["SURFACES"][0])
    blocked_by = _blocked_by(seen["BLOCKED-BY"][1], seen["BLOCKED-BY"][0]) if "BLOCKED-BY" in seen else []
    migration = False
    if "MIGRATION" in seen:
        lineno, value = seen["MIGRATION"]
        if value not in ("yes", "no"):
            raise _bad(lineno, "MIGRATION", value, "yes o no")
        migration = value == "yes"
    return {
        "decision": decision,
        "risk": risk,
        "surfaces": surfaces,
        "blocked_by": blocked_by,
        "migration": migration,
    }


def parse_file(path: str | Path) -> dict:
    """Read intake.md as UTF-8 and parse its header."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntakeError(f"No se pudo leer {p}: {exc.strerror or exc}.") from exc
    return parse_header(text)
