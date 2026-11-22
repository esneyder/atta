"""Validate Atta's Claude Code agent templates (stdlib only, no atta imports).

Usage: python atta/templates/claude/check_templates.py [templates_dir]

`templates_dir` defaults to the folder of this script; it must hold `models.json` and `agents/`.
Checks every `agents/atta-*.md`:

- the expected set of agents is present, no more and no fewer;
- frontmatter between `---` lines with keys exactly name, description, tools, model, one simple
  `key: value` line each (no `: ` or ` #` inside values, so it stays valid YAML once resolved);
- `name` equals the file name without `.md`;
- `model` is exactly `{{model:Tn}}` with Tn a tier of models.json `claude-code`;
- `tools` is exactly the role's tool set (Exploradora without Bash/Write/Edit; Guía, Vigía,
  Jardineras, Soldado, Refutador and Reina without Edit);
- the prompt contains its contract tokens, `## Summary`, `issue.md` and the one-line reply rule;
- no URLs and no references to other factories or projects.

Exits 0 and prints «OK» when everything passes; otherwise prints one line per problem and exits 1.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FRONT_KEYS = ("name", "description", "tools", "model")
TIERS = ("T0", "T1", "T2", "T3")

READ = {"Read", "Grep", "Glob"}
REVIEW_TOKENS = ("VERDICT: PASS", "VERDICT: CHANGES", "PREVENTION:",
                 "[blocker|major|minor] path:line")

AGENTS = {
    "atta-exploradora": {
        "tools": READ,
        "tiers": ("T1",),
        "tokens": ("DECISION: ACCEPT|REJECT|NEEDS-INFO", "RISK: low|medium|high",
                   "SURFACES: none|ui|security|ui,security", "BLOCKED-BY:", "MIGRATION:",
                   "<<<ATTA-ARTIFACT", "ATTA-ARTIFACT>>>", "## Scope", "## Questions"),
    },
    "atta-guia": {
        "tools": READ | {"Write"},
        "tiers": ("T2",),
        "tokens": ("STATUS: READY", "## Goal", "## Acceptance criteria", "## Files", "FILES:",
                   "## Failure paths", "## Out of scope"),
    },
    "atta-vigia": {
        "tools": READ | {"Write"},
        "tiers": ("T2",),
        "tokens": ("VERDICT: READY|REVISE", "spec-review.md"),
    },
    "atta-cortadora": {
        "tools": READ | {"Edit", "Write", "Bash"},
        "tiers": ("T2",),
        "tokens": ("STATUS: DONE|BLOCKED", "## Changes", "## Tests", "## Blocked", "build.md",
                   "checks.md", "review-*.md", "feedback-*.md"),
    },
    "atta-jardinera-code": {
        "tools": READ | {"Bash", "Write"},
        "tiers": ("T1", "T2", "T3"),
        "tokens": REVIEW_TOKENS + ("review-code.md", "reliability", "readability"),
    },
    "atta-jardinera-resilience": {
        "tools": READ | {"Bash", "Write"},
        "tiers": ("T1", "T2", "T3"),
        "tokens": REVIEW_TOKENS + ("review-resilience.md",),
    },
    "atta-jardinera-ux": {
        "tools": READ | {"Bash", "Write"},
        "tiers": ("T0", "T1", "T2", "T3"),
        "tokens": REVIEW_TOKENS + ("review-ux.md",),
    },
    "atta-jardinera-ui": {
        "tools": READ | {"Bash", "Write"},
        "tiers": ("T0", "T1", "T2", "T3"),
        "tokens": REVIEW_TOKENS + ("review-ui.md",),
    },
    "atta-soldado": {
        "tools": READ | {"Bash", "Write"},
        "tiers": ("T2", "T3"),
        "tokens": REVIEW_TOKENS + ("review-security.md",),
    },
    "atta-refutador": {
        "tools": READ | {"Bash", "Write"},
        "tiers": ("T0", "T1"),
        "tokens": ("REFUTE: UPHELD|REFUTED|PARTIAL", "- UPHELD|REFUTED <reviewer> <path:line>",
                   "[blocker|major|minor] path:line", "refute.md", "Never invent findings",
                   "never block"),
    },
    "atta-reina": {
        "tools": READ | {"Bash", "Write"},
        "tiers": ("T2", "T3"),
        "tokens": ("APPROVE", "ESCALATE", "## Why", "decision.md"),
    },
}

COMMON_TOKENS = ("## Summary", "issue.md", "exactly one line")
FORBIDDEN_TOOLS = {
    "atta-exploradora": ("Bash", "Write", "Edit"),
    "atta-guia": ("Edit",),
    "atta-vigia": ("Edit",),
    "atta-reina": ("Edit",),
    "atta-soldado": ("Edit",),
    "atta-refutador": ("Edit",),
}
for _name in AGENTS:
    if _name.startswith("atta-jardinera-"):
        FORBIDDEN_TOOLS[_name] = ("Edit",)

URL = re.compile(r"\b(?:https?|ftp)://|\bwww\.", re.IGNORECASE)
FOREIGN = re.compile(r"\b(?:kernia|gentle(?:-ai)?|engram|opencode|openspec|kamal|"
                     r"atta-sandbox|esneyder)\b", re.IGNORECASE)
MODEL = re.compile(r"\{\{model:(T[0-9])\}\}\Z")
FRONT_LINE = re.compile(r"([a-z]+): (\S.*)\Z")


def _tiers(models_path: Path, problems: list[str]) -> tuple[str, ...]:
    try:
        data = json.loads(models_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{models_path.name}: no se pudo leer: {exc}")
        return ()
    table = data.get("claude-code") if isinstance(data, dict) else None
    if not isinstance(table, dict) or set(table) != set(TIERS) \
            or not all(isinstance(v, str) and v for v in table.values()):
        problems.append(f"{models_path.name}: «claude-code» debe mapear T0, T1, T2 y T3 a modelos.")
        return ()
    return tuple(table)


def _frontmatter(text: str, label: str, problems: list[str]) -> tuple[dict, str] | None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        problems.append(f"{label}: la primera línea debe ser «---».")
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        problems.append(f"{label}: falta el «---» que cierra el frontmatter.")
        return None
    front: dict[str, str] = {}
    for lineno, line in enumerate(lines[1:end], start=2):
        m = FRONT_LINE.match(line)
        if not m:
            problems.append(f"{label}: línea {lineno}: «{line}» no tiene la forma «clave: valor».")
            continue
        key, value = m.group(1), m.group(2).rstrip()
        if key in front:
            problems.append(f"{label}: línea {lineno}: «{key}» aparece dos veces.")
        if ": " in value or " #" in value or value[0] in "'\"|>&*!%@`":
            problems.append(f"{label}: línea {lineno}: el valor de «{key}» no es YAML simple.")
        front[key] = value
    if tuple(front) != FRONT_KEYS:
        problems.append(f"{label}: el frontmatter debe tener exactamente "
                        f"{', '.join(FRONT_KEYS)} en ese orden; tiene {', '.join(front) or 'nada'}.")
    return front, "\n".join(lines[end + 1:])


def check_agent(path: Path, tiers: tuple[str, ...], problems: list[str]) -> None:
    label = f"agents/{path.name}"
    name = path.stem
    rule = AGENTS[name]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{label}: no se pudo leer: {exc}")
        return
    parsed = _frontmatter(text, label, problems)
    if parsed is None:
        return
    front, body = parsed
    if front.get("name") != name:
        problems.append(f"{label}: name «{front.get('name')}» no coincide con el archivo.")
    if not front.get("description"):
        problems.append(f"{label}: falta description.")

    m = MODEL.match(front.get("model", ""))
    if not m:
        problems.append(f"{label}: model «{front.get('model')}» debe ser {{{{model:Tn}}}}.")
    elif m.group(1) not in tiers:
        problems.append(f"{label}: model usa {m.group(1)}, que no está en models.json.")
    elif m.group(1) not in rule["tiers"]:
        problems.append(f"{label}: model usa {m.group(1)}; para este rol usa "
                        f"{' o '.join(rule['tiers'])}.")

    tools = [t.strip() for t in front.get("tools", "").split(",") if t.strip()]
    if len(set(tools)) != len(tools):
        problems.append(f"{label}: tools repite herramientas.")
    for tool in FORBIDDEN_TOOLS.get(name, ()):
        if tool in tools:
            problems.append(f"{label}: este rol no puede tener la herramienta {tool}.")
    if set(tools) != rule["tools"]:
        problems.append(f"{label}: tools debe ser {', '.join(sorted(rule['tools']))}; "
                        f"es {', '.join(tools) or 'nada'}.")

    if not body.strip():
        problems.append(f"{label}: el prompt está vacío.")
    for token in COMMON_TOKENS + rule["tokens"]:
        if token not in body:
            problems.append(f"{label}: el prompt no contiene «{token}».")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if URL.search(line):
            problems.append(f"{label}: línea {lineno}: no se permiten URLs.")
        hit = FOREIGN.search(line)
        if hit:
            problems.append(f"{label}: línea {lineno}: referencia ajena «{hit.group(0)}».")


def check(root: Path) -> list[str]:
    problems: list[str] = []
    tiers = _tiers(root / "models.json", problems)
    agents_dir = root / "agents"
    if not agents_dir.is_dir():
        return problems + [f"No existe la carpeta {agents_dir}."]
    found = {p.stem: p for p in agents_dir.glob("*.md")}
    for name in sorted(set(found) - set(AGENTS)):
        problems.append(f"agents/{name}.md: agente desconocido; agrégalo a check_templates.py.")
    for name in sorted(set(AGENTS) - set(found)):
        problems.append(f"agents/{name}.md: falta la plantilla.")
    for name in sorted(set(found) & set(AGENTS)):
        check_agent(found[name], tiers, problems)
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent
    problems = check(root)
    out = sys.stdout
    if problems:
        for problem in problems:
            print(problem, file=out)
        print(f"{len(problems)} problema(s) en las plantillas.", file=out)
        return 1
    print(f"OK: {len(AGENTS)} plantillas válidas.", file=out)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv))
