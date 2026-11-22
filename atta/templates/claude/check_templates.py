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
  Jardineras, Soldado, Rastreador, Refutador and Reina without Edit);
- the prompt contains its contract tokens, `## Summary`, `issue.md`, the one-line reply rule and
  its context budget (`## Context budget`, «do not read the whole repo», G12);
- the Cortadora's `build.md` (G10) and the Guía's `spec.md` artifact blocks have exactly their
  fixed sections in order; the Guía copies the issue's criteria verbatim and `FILES:` is
  mandatory (G11); the Vigía caps `REVISE` at 2 rounds of factual errors only;
- reviewers (Jardineras, Soldado, Rastreador) keep the bounded scope and a `## Core checklist`
  of 1 to 12 items with the core points (K2);
- the Rastreador (G6, high risk only) runs on T3 only and keeps its adversarial contract: try
  to break the change, grep the call and not the loop it touched, every caller, writer and
  reader, bypasses, concurrency, partial failure, data loss, and never edits code;
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
                 "[blocker|major|minor] path:line",
                 "correctness and the stated requirements only; do not invent work",
                 "## Core checklist", "top-down, then bottom-up", "try to produce the failure",
                 "sad-path test", "fails without the fix", "callers and writers", "from memory",
                 "server-side", "pinned by a test")
MAX_CHECKLIST = 12

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
                   "## Failure paths", "## Out of scope", "verbatim",
                   "The `FILES:` line is mandatory"),
        "artifact": ("Summary", "Goal", "Acceptance criteria", "Files", "Failure paths",
                     "Out of scope"),
    },
    "atta-vigia": {
        "tools": READ | {"Write"},
        "tiers": ("T2",),
        "tokens": ("VERDICT: READY|REVISE", "spec-review.md", "at most 2 `REVISE` rounds",
                   "concrete factual errors only", "verbatim"),
    },
    "atta-cortadora": {
        "tools": READ | {"Edit", "Write", "Bash"},
        "tiers": ("T2",),
        "tokens": ("STATUS: DONE|BLOCKED", "## Changes", "## Tests", "## Acceptance", "## Risks",
                   "## Blocked", "build.md", "checks.md", "review-*.md", "feedback-*.md",
                   "is not evidence", "verbatim", "«ninguno»", "red → green"),
        "artifact": ("Summary", "Changes", "Tests", "Acceptance", "Risks", "Blocked"),
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
    "atta-rastreador": {
        "tools": READ | {"Bash", "Write"},
        "tiers": ("T3",),
        "tokens": REVIEW_TOKENS + ("VERDICT: PASS|CHANGES", "review-rastreador.md",
                                   "try to break the change",
                                   "grep the call, not the loop you touched",
                                   "every caller, writer and reader", "bypass", "edge inputs",
                                   "concurrency", "partial failure", "data loss",
                                   "Never edit code"),
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

COMMON_TOKENS = ("## Summary", "issue.md", "exactly one line", "## Context budget",
                 "do not read the whole repo")
REVIEWERS = {"atta-jardinera-code", "atta-jardinera-resilience", "atta-jardinera-ux",
             "atta-jardinera-ui", "atta-soldado", "atta-rastreador"}
FORBIDDEN_TOOLS = {
    "atta-exploradora": ("Bash", "Write", "Edit"),
    "atta-guia": ("Edit",),
    "atta-vigia": ("Edit",),
    "atta-reina": ("Edit",),
    "atta-soldado": ("Edit",),
    "atta-rastreador": ("Edit",),
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


def _artifact_sections(body: str) -> list[str]:
    """`## ` headings inside the first ```text block after `## The artifact`."""
    lines = body.splitlines()
    try:
        start = lines.index("## The artifact")
    except ValueError:
        return []
    inside, sections = False, []
    for line in lines[start + 1:]:
        if not inside:
            inside = line.strip() == "```text"
        elif line.strip() == "```":
            break
        elif line.startswith("## "):
            sections.append(line[3:].strip())
    return sections


def _checklist_items(body: str) -> int:
    """Numbered items in the `## Core checklist` section."""
    count, inside = 0, False
    for line in body.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Core checklist"
        elif inside and re.match(r"\d+\. ", line):
            count += 1
    return count


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
    expected = rule.get("artifact")
    if expected:
        found_sections = _artifact_sections(body)
        if tuple(found_sections) != expected:
            problems.append(f"{label}: las secciones del artefacto deben ser "
                            f"{', '.join(expected)} en ese orden; son "
                            f"{', '.join(found_sections) or 'ninguna'}.")
    if name in REVIEWERS:
        items = _checklist_items(body)
        if not 1 <= items <= MAX_CHECKLIST:
            problems.append(f"{label}: «## Core checklist» debe tener entre 1 y "
                            f"{MAX_CHECKLIST} puntos; tiene {items}.")
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
