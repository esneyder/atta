"""Which model runs each role of the colony (S5, S12): the single source of model resolution.

Defaults come from the packaged `templates/claude/models.json` (tier → model per runtime) and from
each agent template's `{{model:Tn}}` (role → default tier). A project may override both in
`atta/config.json`:

    "models": {"claude-code": {"tiers": {"T2": "haiku"}, "roles": {"soldado": "opus"}}}

Resolution (L18): a role override replaces the tier model of that role; escalation (see the
matrix below, the router's T3) always goes to the T3 model; the Vigía never shares the Guía's model
when another tier allows it, and the Refutador (G4) never shares the model of a reviewer it judges. `atta next`, `atta init` and `atta models` all resolve here.

Stage×risk matrix (F4 T3, S8, S8a; `Table.stage_tier`, the single source, S2: cheap where an error
is cheap, strong where it is expensive). The model of a stage depends on the job's risk:
  low     the Guía and the UX Jardinera run one tier below their default (never below T0) and
          only when no router tier was given: the routing table already runs the reviewers one
          tier down at low. The Exploradora decides the risk, so it runs before there is one and
          is never lowered; the Reina and the Refutador never go down.
  medium  today's routing, unchanged.
  high    the Cortadora, the Reina and the security reviewer go up to T3 (a reviewer that the
          router already sends to T3 too).
An unknown risk counts as high for the ups and never lowers anything. The attempt ladder: the
Cortadora runs on T3 from round 2 (`Round: N` is total_rounds + 1, so the first rework).
Independence (F2 L8): `Table.review_model` makes a reviewer avoid the Cortadora's model, but only
for a model that is not weaker than the reviewer's tier and, below high, not above it (medium
stays as today and a reviewer is never weakened), so the clash of two T2 models is kept.
A project turns the whole matrix off with `"routing_matrix": false` in atta/config.json (human
only; absent or true: on); `atta doctor` tells projects without the key that the default changed.

`atta models [--runtime R]` prints the table; `set <Tn> <modelo>`, `set --role <rol> <modelo>`,
`unset --role <rol>`, `fallback <Tn> <modelo>...` / `fallback <Tn> --clear` and `reset` change `atta/config.json` (atomically, keeping every other key
and its order) and then the `model:` line of each `.claude/agents/atta-<rol>.md` and of the
`/atta` driver (`.claude/commands/atta.md` and `.claude/skills/atta/SKILL.md`, which run on
the cheap tier DRIVER_TIER, F4 S5/G8: `atta next` decides the pipeline). Anything invalid
exits 2 and writes nothing.

Fallback chains (F4 T4, S4): `models.<runtime>.fallbacks` maps a tier to the Claude models that
replace its model, in order, when an agent fails by quota, rate limit or network (never by
context). A fallback never costs more than the model it replaces (COST: haiku < sonnet < opus,
read from the model name): `atta models fallback` refuses it and Table.chain skips an entry that
became more expensive after a later `set`. No chain by default: absent means no fallback.
The runtime side (`atta model-failed`, recorded failures, `atta next` relaunching on the next
model or waiting when the chain is spent) lives in fallback.py and next.py.
"""

from __future__ import annotations

import argparse
import copy
import functools
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

OK, REFUSED = 0, 2
RUNTIME = "claude-code"
RUNTIMES = ("claude-code",)
TIERS = ("T0", "T1", "T2", "T3")
ROLES = ("exploradora", "guia", "vigia", "cortadora", "jardinera-code", "jardinera-resilience",
         "jardinera-ux", "jardinera-ui", "soldado", "rastreador", "refutador", "reina")
ESCALATING = ("cortadora", "soldado", "reina")
# The stage×risk matrix (F4 T3, S8a). Roles that go one tier down at low risk, roles (and the
# reviewer name `security`, run by the Soldado) that go up to T3 at high or unknown risk, and
# the first round of the attempt ladder (the Cortadora escalates from it).
RISKS = ("low", "medium", "high")
MATRIX_DOWN = ("guia", "jardinera-ux")
MATRIX_UP = ("cortadora", "reina")
MATRIX_UP_REVIEWERS = ("security",)
LADDER_ROUND = 2
LEGACY_LADDER_ROUND = 3  # routing_matrix: false keeps the escalation of before F4 T3
# Fallback order for the Vigía when its model equals the Guía's (L8), and for the Refutador
# when its model equals a reviewer's it judges (G4).
AVOID_ORDER = ("T2", "T1", "T3", "T0")
SHORT_NAMES = {"claude-code": ("haiku", "sonnet", "opus")}
FULL_ID = {"claude-code": re.compile(r"claude-[a-z0-9.-]+")}
FULL_ID_EXAMPLE = {"claude-code": "claude-sonnet-4-5"}
# The /atta orchestrator (command and skill, `{{model:T0}}` in their templates) only executes
# what `atta next` prints, so it runs on the cheap tier (F4 S5, G8).
DRIVER_TIER = "T0"
DRIVER_FILES = (".claude/skills/atta/SKILL.md", ".claude/commands/atta.md")
PLACEHOLDER = re.compile(r"\{\{model:([A-Za-z0-9]+)\}\}")
_MODEL_LINE = re.compile(r"^model:[^\r\n]*", re.M)
SIGN = "— Atta"
# Relative cost of a Claude model by family, read from its name (F4 T4, L4: never more expensive).
COST = {"haiku": 0, "sonnet": 1, "opus": 2}
_FAMILY = re.compile(r"haiku|sonnet|opus")
USAGE_FALLBACK = ("Uso: atta models fallback <T0|T1|T2|T3> <modelo> [<modelo>...], o atta "
                  "models fallback <T0|T1|T2|T3> --clear.")


class ModelsError(ValueError):
    """Invalid model, tier, role or runtime; message in Spanish."""


def _templates(templates: str | Path | None) -> Path:
    if templates is not None:
        return Path(templates)
    from .project import TEMPLATES_DIR
    return TEMPLATES_DIR


# ---------------------------------------------------------------- validation

def model_error(runtime: str, value) -> str | None:
    """Why `value` is not a model for `runtime` (Spanish), or None when it is valid."""
    short = SHORT_NAMES[runtime]
    if isinstance(value, str) and (value in short or FULL_ID[runtime].fullmatch(value)):
        return None
    shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return (f"«{shown}» no es un modelo válido para {runtime}: usa "
            f"{', '.join(short[:-1])} u {short[-1]}, o un id completo que cumpla "
            f"{FULL_ID[runtime].pattern} (p. ej. {FULL_ID_EXAMPLE[runtime]}).")


def tier_error(tier) -> str | None:
    if tier in TIERS:
        return None
    return f"El tier «{tier}» no existe: usa {', '.join(TIERS[:-1])} o {TIERS[-1]}."


def role_name(value: str) -> str:
    """`value` without accents and in lowercase (so «Vigía» names the role vigia)."""
    folded = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def role_error(role) -> str | None:
    if role in ROLES:
        return None
    return f"El rol «{role}» no existe: usa {', '.join(ROLES[:-1])} o {ROLES[-1]}."


def cost(model) -> int | None:
    """COST of `model` by the first family name it contains, or None when unknown."""
    m = _FAMILY.search(model) if isinstance(model, str) else None
    return COST[m.group(0)] if m else None


def cost_error(model) -> str | None:
    if cost(model) is not None:
        return None
    return (f"No sé el costo de «{model}»: un fallback necesita haiku, sonnet u opus en el "
            "nombre.")


def chain_error(runtime: str, tier: str, chain) -> str | None:
    """Why `chain` is not a valid fallback list for `tier` (without its config prefix)."""
    seen = set()
    previous = None
    for model in chain:
        problem = model_error(runtime, model) or cost_error(model)
        if problem:
            return problem
        if model in seen:
            return f"«{model}» se repite en la cadena de {tier}."
        if previous is not None and cost(model) > cost(previous):
            # Table.fallback only moves to a model no dearer than the one that failed (S4), so
            # an entry dearer than the one before it could never be reached.
            return (f"«{model}» cuesta más que «{previous}», que va antes en la cadena de "
                    f"{tier}, y nunca se alcanzaría: ordénala de la más cara a la más barata "
                    f"(opus > sonnet > haiku).")
        seen.add(model)
        previous = model
    return None


def config_error(value) -> str | None:
    """Why the `models` value of config.json is invalid (without the «config.json: » prefix)."""
    example = '{"claude-code": {"tiers": {"T2": "haiku"}, "roles": {"soldado": "opus"}}}'
    if not isinstance(value, dict):
        return f"models debe ser un objeto por runtime, p. ej. {example}."
    for runtime, entry in value.items():
        if runtime not in RUNTIMES:
            return f"models: el runtime «{runtime}» no existe; usa {', '.join(RUNTIMES)}."
        where = f"models.{runtime}"
        if not isinstance(entry, dict) or set(entry) - {"tiers", "roles", "fallbacks"}:
            return (f"{where} debe ser un objeto con «tiers», «roles» y/o «fallbacks», p. ej. "
                    f"{example}.")
        for key, check in (("tiers", tier_error), ("roles", role_error)):
            table = entry.get(key, {})
            if not isinstance(table, dict):
                return f"{where}.{key} debe ser un objeto."
            for name, model in table.items():
                problem = check(name) or model_error(runtime, model)
                if problem:
                    return f"{where}.{key}.{name}: {problem}"
        chains = entry.get("fallbacks", {})
        if not isinstance(chains, dict):
            return f"{where}.fallbacks debe ser un objeto."
        for tier, chain in chains.items():
            if tier_error(tier):
                return f"{where}.fallbacks.{tier}: {tier_error(tier)}"
            if not isinstance(chain, list) or not chain:
                return f"{where}.fallbacks.{tier} debe ser una lista no vacía de modelos."
            problem = chain_error(runtime, tier, chain)
            if problem:
                return f"{where}.fallbacks.{tier}: {problem}"
    return None


# ---------------------------------------------------------------- defaults

def default_tiers(runtime: str = RUNTIME, templates: str | Path | None = None) -> dict:
    """Tier → model for `runtime` from models.json."""
    path = _templates(templates) / "claude" / "models.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModelsError(f"No se pudo leer la plantilla models.json: {exc.strerror or exc}.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ModelsError(f"La plantilla models.json no es JSON válido ({exc}).") from exc
    tiers = data.get(runtime) if isinstance(data, dict) else None
    if not isinstance(tiers, dict):
        raise ModelsError(f"models.json no define el runtime «{runtime}».")
    return dict(tiers)


@functools.lru_cache(maxsize=None)
def _role_tiers(agents_dir: str) -> tuple:
    found = []
    for role in ROLES:
        path = Path(agents_dir) / f"atta-{role}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = PLACEHOLDER.search(text)
        if match:
            found.append((role, match.group(1)))
    return tuple(found)


def role_tiers(templates: str | Path | None = None) -> dict:
    """Role → default tier, read from each template's `{{model:Tn}}` (roles without one skipped)."""
    return dict(_role_tiers(str((_templates(templates) / "claude" / "agents").resolve())))


# ---------------------------------------------------------------- the table

class Table:
    """The effective models of one runtime: tier models, role overrides and role default tiers."""

    def __init__(self, tiers: dict, roles: dict | None = None, defaults: dict | None = None,
                 base: dict | None = None, runtime: str = RUNTIME,
                 fallbacks: dict | None = None, matrix: bool = True):
        self.tiers = dict(tiers)
        self.fallbacks = {t: list(c) for t, c in (fallbacks or {}).items()}
        self.roles = dict(roles or {})
        self.defaults = dict(defaults) if defaults is not None else role_tiers()
        self.base = dict(base) if base is not None else dict(tiers)
        self.runtime = runtime
        self.matrix = matrix

    def tier_of(self, role: str) -> str:
        if role not in self.defaults:
            raise ModelsError(role_error(role) or f"La plantilla atta-{role}.md no pide un tier.")
        return self.defaults[role]

    def tier_model(self, tier: str) -> str:
        model = self.tiers.get(tier)
        if not isinstance(model, str) or not model:
            raise ModelsError(f"models.json no define {tier} para {self.runtime}.")
        return model

    def chain(self, tier: str, primary: str | None = None) -> list[str]:
        """The fallback models of `tier` that are not `primary` (the tier model by default)
        and never cost more than it (S4, L4)."""
        primary = primary or self.tier_model(tier)
        top = cost(primary)
        return [m for m in self.fallbacks.get(tier, ()) if m != primary
                and top is not None and cost(m) is not None and cost(m) <= top]

    def fallback(self, tier: str, failed, primary: str | None = None,
                 avoid=None) -> str | None:
        """The next model of `tier`'s chain after the `failed` ones, or None when exhausted.
        `avoid` (one model or a collection: the Guía's for the Vigía, the reviewers' for the
        Refutador) is skipped while another chain model is left, as in `resolve`."""
        failed = set(failed or ())
        left = [m for m in self.chain(tier, primary) if m not in failed]
        avoided = {avoid} if isinstance(avoid, str) else set(avoid or ())
        return next((m for m in left if m not in avoided), left[0] if left else None)

    def stage_tier(self, role: str, *, tier: str | None = None, risk: str | None = None,
                   round: int = 1, reviewer: str | None = None) -> tuple[str, bool]:
        """(tier, escalate) of a stage by the matrix (see the module docstring). `tier` is the
        router's, when it gave one; `reviewer` is the router's reviewer name (`security` is run
        by the Soldado); `round` is the round number the stage works on. Escalate means T3 and
        ignores a role override, as before."""
        base = tier or self.tier_of(role)
        if not self.matrix:  # the routing of before F4 T3
            ladder = role == "cortadora" and round >= LEGACY_LADDER_ROUND
            return ("T3", True) if ladder or tier == "T3" or (
                risk == "high" and role in MATRIX_UP) else (base, False)
        raised = risk not in ("low", "medium") and (
            role in MATRIX_UP or reviewer in MATRIX_UP_REVIEWERS)
        if raised or tier == "T3" or (role == "cortadora" and round >= LADDER_ROUND):
            return "T3", True
        if risk == "low" and tier is None and role in MATRIX_DOWN:
            return TIERS[max(0, TIERS.index(base) - 1)], False
        return base, False

    def review_model(self, role: str, *, tier: str, escalate: bool, risk: str | None,
                     cortadora: str | None) -> str:
        """Model of a reviewer: like `resolve` and, with the matrix on, away from the
        Cortadora's model `cortadora` (F2 L8) when a model that is not weaker than `tier` (and
        below high risk, not above it either) allows it. A role override is never moved."""
        if not self.matrix or cortadora is None or role in self.roles:  # a human's choice stays
            return self.resolve(role, tier=tier, escalate=escalate)
        ceiling = "T3" if risk not in ("low", "medium") else tier
        return self.resolve(role, tier=tier, escalate=escalate, avoid=cortadora, floor=tier,
                            ceiling=ceiling)

    def risk_models(self, role: str, reviewer: str | None = None) -> tuple[str, str, str]:
        """The role's model at low, medium and high risk (first round, no router tier)."""
        out = []
        for risk in RISKS:
            tier, escalate = self.stage_tier(role, risk=risk, reviewer=reviewer)
            out.append(self.resolve(role, tier=tier, escalate=escalate))
        return tuple(out)

    def resolve(self, role: str, *, tier: str | None = None, escalate: bool = False,
                avoid=None, floor: str | None = None, ceiling: str | None = None) -> str:
        """Model for `role`: escalation → T3; else the role override, else the model of `tier`
        (the router's) or of the role's default tier. `avoid` (one model or a collection) is
        skipped for the first tier of AVOID_ORDER whose model is not avoided, when one exists
        (never a tier below `floor` nor above `ceiling`)."""
        if escalate:
            return self.tier_model("T3")
        base = tier or self.tier_of(role)
        if floor in TIERS and TIERS.index(floor) > TIERS.index(base):
            base = floor  # never weaker than what this role judges
        model = self.roles.get(role) or self.tier_model(base)
        avoided = {avoid} if isinstance(avoid, str) else set(avoid or ())
        if model not in avoided:
            return model
        for other in AVOID_ORDER:
            if floor in TIERS and TIERS.index(other) < TIERS.index(floor):
                continue
            if ceiling in TIERS and TIERS.index(other) > TIERS.index(ceiling):
                continue
            if self.tiers.get(other) and self.tiers[other] not in avoided:
                return self.tiers[other]
        return model

    def agent_model(self, role: str) -> str:
        """The model of the role's agent file (no escalation; the Vigía avoids the Guía)."""
        avoid = self.agent_model("guia") if role == "vigia" else None
        return self.resolve(role, avoid=avoid)

    def origin(self, role: str) -> str:
        if role in self.roles:
            origin = "rol"
            if role == "vigia" and self.agent_model(role) != self.roles[role]:
                origin = "rol · regla ≠ guía"
        elif role == "vigia" and self.agent_model(role) != self.tier_model(self.tier_of(role)):
            origin = "regla ≠ guía"
        else:
            origin = "tier"
        if role in ESCALATING:
            origin += f" · escala a T3 ({self.tier_model('T3')})"
        return origin


def project_models(cfg: dict | None, runtime: str = RUNTIME) -> dict:
    """The `models.<runtime>` entry of a config dict ({} when absent)."""
    entry = ((cfg or {}).get("models") or {}).get(runtime) or {}
    return entry if isinstance(entry, dict) else {}


def table(cfg: dict | None = None, runtime: str = RUNTIME,
          templates: str | Path | None = None) -> Table:
    """The effective Table: packaged defaults with the project's `models` merged over them."""
    base = default_tiers(runtime, templates)
    entry = project_models(cfg, runtime)
    tiers = dict(base, **(entry.get("tiers") or {}))
    return Table(tiers, entry.get("roles") or {}, role_tiers(templates), base, runtime,
                 entry.get("fallbacks") or {}, matrix_on(cfg))


def matrix_on(cfg: dict | None) -> bool:
    """The stage×risk matrix applies unless atta/config.json says `"routing_matrix": false`."""
    return (cfg or {}).get("routing_matrix", True) is not False


def resolve(role: str, cfg: dict | None, *, escalate: bool = False, avoid=None,
            tier: str | None = None, floor: str | None = None,
            runtime: str = RUNTIME) -> str:
    """Model for `role` with the project's config (see Table.resolve)."""
    return table(cfg, runtime).resolve(role, tier=tier, escalate=escalate, avoid=avoid,
                                       floor=floor)


# ---------------------------------------------------------------- writing

def _write_atomic(path: Path, data: bytes) -> None:
    from .store import _replace
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_model_line(text: str, model: str) -> str | None:
    """`text` with the frontmatter `model:` line set to `model`; None without such a line."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    end = next((k for k in range(1, len(lines)) if lines[k].rstrip("\r\n") == "---"), None)
    if end is None:
        return None
    front = "".join(lines[1:end])
    if not _MODEL_LINE.search(front):
        return None
    front = _MODEL_LINE.sub(lambda _m: f"model: {model}", front, count=1)
    return lines[0] + front + "".join(lines[end:])


def sync_agents(root: Path, tab: Table) -> list[str]:
    """Rewrite the `model:` line of every `.claude/agents/atta-<rol>.md`; report lines."""
    lines, unchanged = [], 0
    for role in ROLES:
        rel = f".claude/agents/atta-{role}.md"
        path = root / rel
        model = tab.agent_model(role)
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                text = fh.read()
        except FileNotFoundError:
            lines.append(f"falta: {rel}; corre `atta init` para crearlo.")
            continue
        new = set_model_line(text, model)
        if new is None:
            lines.append(f"sin línea model: en {rel}; la dejé igual.")
        elif new == text:
            unchanged += 1
        else:
            _write_atomic(path, new.encode("utf-8"))
            lines.append(f"actualicé: {rel} → {model}")
    if unchanged:
        lines.append(f"sin cambios: {unchanged} agente{'s' if unchanged != 1 else ''}")
    return lines + _sync_driver(root, tab)


def _sync_driver(root: Path, tab: Table) -> list[str]:
    """Rewrite the `model:` line of the /atta command and skill to the DRIVER_TIER model;
    report only what changed or could not change."""
    lines = []
    model = tab.tier_model(DRIVER_TIER)
    for rel in DRIVER_FILES:
        path = root / rel
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                text = fh.read()
        except FileNotFoundError:
            lines.append(f"falta: {rel}; corre `atta init` para crearlo.")
            continue
        new = set_model_line(text, model)
        if new is None:
            lines.append(f"sin línea model: en {rel}; la dejé igual (ponlo al día con "
                         "`atta init --force`).")
        elif new != text:
            _write_atomic(path, new.encode("utf-8"))
            lines.append(f"actualicé: {rel} → {model}")
    return lines


def _raw_config(root: Path) -> dict:
    from . import config, project
    path = project.config_path(root)
    source = path if path.exists() else config.CONFIG_PATH
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ModelsError(f"No se pudo leer {source}: {exc}.") from exc
    if not isinstance(data, dict):
        raise ModelsError("config.json debe ser un objeto JSON.")
    return data


def _save_config(root: Path, data: dict) -> None:
    from . import config, project
    try:
        config.with_defaults(data)
    except config.ConfigError as exc:
        raise ModelsError(str(exc)) from None
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    _write_atomic(project.config_path(root), text.encode("utf-8"))


# ---------------------------------------------------------------- commands

def _tiers_line(tab: Table) -> str:
    parts = []
    for tier in TIERS:
        mark = " (proyecto)" if tab.tiers.get(tier) != tab.base.get(tier) else ""
        parts.append(f"{tier} {tab.tiers.get(tier, '-')}{mark}")
    return "Tiers: " + " · ".join(parts)


def _matrix_lines(tab: Table) -> list[str]:
    """The stage×risk matrix as the models it picks (F4 T3), only for the roles it changes."""
    if not tab.matrix:
        return ["Matriz etapa×riesgo: desactivada (\"routing_matrix\": false en "
                "atta/config.json); el routing es el de antes."]
    shown = (("guia", None, ""), ("jardinera-ux", None, ""), ("cortadora", None, ""),
             ("reina", None, ""), ("soldado", "security", " (revisor de seguridad)"))
    rows = []
    for role, reviewer, note in shown:
        low, medium, high = tab.risk_models(role, reviewer)
        if len({low, medium, high}) == 1:
            continue
        if role == "cortadora":
            note = f" (desde la ronda {LADDER_ROUND}: {tab.tier_model('T3')})"
        rows.append((role, f"{low} · {medium} · {high}", note))
    if not rows:
        return []
    width = max(len(r[0]) for r in rows)
    return (["Matriz etapa×riesgo (modelo en riesgo bajo · medio · alto):"]
            + [f"  {role.ljust(width)}  {models}{note}" for role, models, note in rows])


def _fallback_line(tab: Table) -> str:
    parts = [f"{t} {' → '.join([tab.tier_model(t), *tab.chain(t)])}"
             for t in reversed(TIERS) if tab.tiers.get(t) and tab.chain(t)]
    shown = " · ".join(parts) if parts else (
        "ninguno (`atta models fallback <tier> <modelo>...`)")
    return f"Fallback por cuota, rate limit o red: {shown}."


def render(root: Path, tab: Table) -> str:
    rows = [("ROL", "TIER", "MODELO", "ORIGEN")]
    for role in ROLES:
        rows.append((role, tab.tier_of(role), tab.agent_model(role), tab.origin(role)))
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    out = [f"Modelos de Atta en {root} (runtime {tab.runtime}):"]
    out += ["  ".join([*(c.ljust(w) for c, w in zip(r, widths)), r[3]]) for r in rows]
    out += [
        _tiers_line(tab),
        *_matrix_lines(tab),
        "Las Jardineras, el Soldado y el Rastreador corren en el tier que les asigna el "
        "router; "
        "un modelo por rol lo reemplaza.",
        f"El orquestador /atta (comando y skill) corre en {DRIVER_TIER} "
        f"({tab.tiers.get(DRIVER_TIER, '-')}).",
        _fallback_line(tab),
        "Cambia con `atta models set <T0|T1|T2|T3> <modelo>` o "
        "`atta models set --role <rol> <modelo>`.",
        SIGN,
    ]
    return "\n".join(out)


def _change(root: Path, cfg_data: dict, runtime: str, headline: str) -> int:
    from . import project
    _save_config(root, cfg_data)
    lines = sync_agents(root, table(cfg_data, runtime))
    rel = project.config_path(root).relative_to(root).as_posix()
    out = [headline.format(config=rel)] + [f"  {line}" for line in lines]
    out += ["Revisa la tabla con `atta models`.", SIGN]
    print("\n".join(out))
    return OK


def _entry(data: dict, runtime: str) -> dict:
    models = data.get("models")
    models = copy.deepcopy(models) if isinstance(models, dict) else {}
    data["models"] = models
    return models.setdefault(runtime, {})


def _prune(data: dict, runtime: str) -> None:
    entry = data["models"].get(runtime, {})
    for key in ("tiers", "roles", "fallbacks"):
        if key in entry and not entry[key]:
            del entry[key]
    if not entry:
        data["models"].pop(runtime, None)
    if not data["models"]:
        del data["models"]


def _refuse(message: str) -> int:
    print(f"{message} No cambié nada.", file=sys.stderr)
    return REFUSED


USAGE_SET = ("Uso: atta models set <T0|T1|T2|T3> <modelo>, o atta models set --role <rol> "
             "<modelo>.")


def _fallback(root: Path, data: dict, runtime: str, cfg, args) -> int:
    values, clear = args.values, args.clear
    if not values or (clear and len(values) != 1) or (not clear and len(values) < 2):
        return _refuse(USAGE_FALLBACK)
    tier = values[0].upper()
    if tier_error(tier):
        return _refuse(tier_error(values[0]))
    if clear:
        current = project_models(data, runtime).get("fallbacks") or {}
        if tier not in current:
            print(f"Atta: {tier} no tiene fallback en {runtime}; no cambié nada.\n{SIGN}")
            return OK
        entry = _entry(data, runtime)
        del entry["fallbacks"][tier]
        _prune(data, runtime)
        return _change(root, data, runtime,
                       f"Atta: {tier} ya no tiene fallback en {{config}} ({runtime}).")
    chain = values[1:]
    for model in chain:
        problem = model_error(runtime, model)
        if problem:
            return _refuse(problem)
    problem = chain_error(runtime, tier, chain)
    if problem:
        return _refuse(problem)
    primary = table(data, runtime).tier_model(tier)
    for model in chain:
        if model == primary:
            return _refuse(f"«{model}» ya es el modelo de {tier}: el fallback necesita otro "
                           "modelo.")
        if cost(primary) is None or cost(model) > cost(primary):
            return _refuse(f"«{model}» es más caro que el modelo de {tier} ({primary}): un "
                           "fallback nunca sube de costo.")
    _entry(data, runtime).setdefault("fallbacks", {})[tier] = chain
    return _change(root, data, runtime,
                   f"Atta: si {tier} ({primary}) falla por cuota, rate limit o red, usa "
                   f"{' → '.join(chain)} en {{config}} ({runtime}).")


def cmd_models(args, cfg, jobs) -> int:
    root = Path(args.root)
    runtime = args.runtime
    action = getattr(args, "models_action", None)
    try:
        if action is None:
            print(render(root, table(cfg, runtime)))
            return OK
        data = _raw_config(root)
        if action == "reset":
            if "models" not in data:
                print(f"Atta: el proyecto ya usa los modelos de fábrica; no cambié nada.\n{SIGN}")
                return OK
            del data["models"]
            base = default_tiers(runtime)
            shown = " · ".join(f"{t} {base.get(t, '-')}" for t in TIERS)
            return _change(root, data, runtime,
                           f"Atta quitó «models» de {{config}}: vuelven los modelos de fábrica "
                           f"({shown}).")
        if action == "fallback":
            return _fallback(root, data, runtime, cfg, args)
        role = role_name(args.role) if args.role is not None else None
        if role is not None and role_error(role):
            return _refuse(role_error(role))
        if action == "unset":
            current = project_models(data, runtime).get("roles") or {}
            if role not in current:
                print(f"Atta: {role} no tiene un modelo propio en {runtime}; no cambié nada.\n"
                      f"{SIGN}")
                return OK
            entry = _entry(data, runtime)
            del entry["roles"][role]
            _prune(data, runtime)
            model = table(data, runtime).agent_model(role)
            return _change(root, data, runtime,
                           f"Atta: {role} vuelve al modelo de su tier ({model}) en {{config}}.")
        # set
        values = args.values
        if len(values) != (1 if role is not None else 2):
            return _refuse(USAGE_SET)
        model = values[-1]
        if role is None:
            tier = values[0].upper()
            if tier_error(tier):
                return _refuse(tier_error(values[0]))
        problem = model_error(runtime, model)
        if problem:
            return _refuse(problem)
        entry = _entry(data, runtime)
        if role is not None:
            entry.setdefault("roles", {})[role] = model
            headline = f"Atta: {role} ahora usa {model} en {{config}} ({runtime})."
        else:
            entry.setdefault("tiers", {})[tier] = model
            headline = f"Atta: {tier} ahora es {model} en {{config}} ({runtime})."
        return _change(root, data, runtime, headline)
    except ModelsError as exc:
        return _refuse(str(exc))
    except OSError as exc:
        print(f"No se pudo escribir en {root}: {exc.strerror or exc}.", file=sys.stderr)
        return REFUSED


def register(subparsers) -> None:
    """Add `atta models` (and its set/unset/reset actions) to the `atta` CLI."""
    runtime_help = "runtime de los agentes (por defecto: claude-code)"
    p = subparsers.add_parser(
        "models", help="Muestra o cambia qué modelo corre cada rol en este proyecto.",
        description="Sin acción, imprime la tabla ROL / TIER / MODELO / ORIGEN. set, unset, "
                    "fallback y reset cambian «models» en atta/config.json y la línea model: de "
                    ".claude/agents/atta-*.md y del comando y skill /atta (que corren en "
                    "T0). Un modelo, rol o tier inválido sale 2 sin "
                    "escribir nada.",
        epilog="Modelos válidos en claude-code: haiku, sonnet, opus o un id completo "
               "claude-[a-z0-9.-]+. Roles: " + ", ".join(ROLES) + ".")
    p.add_argument("--runtime", choices=RUNTIMES, default=RUNTIME, help=runtime_help)
    p.set_defaults(func=cmd_models, models_action=None, role=None)
    sub = p.add_subparsers(dest="models_action", metavar="<acción>")

    s = sub.add_parser("set", help="Cambia el modelo de un tier o de un rol.",
                       usage="atta models set <T0|T1|T2|T3> <modelo>\n"
                             "       atta models set --role <rol> <modelo>",
                       description="atta models set <T0|T1|T2|T3> <modelo> cambia el modelo de "
                                   "un tier (todos los roles de ese tier); con --role <rol>, "
                                   "solo ese rol.")
    s.add_argument("values", nargs="+", metavar="[<tier>] <modelo>",
                   help="tier y modelo, o solo el modelo con --role")
    s.add_argument("--role", metavar="<rol>", help="rol: " + ", ".join(ROLES))
    s.add_argument("--runtime", choices=RUNTIMES, default=argparse.SUPPRESS, help=runtime_help)

    u = sub.add_parser("unset", help="Quita el modelo propio de un rol (vuelve a su tier).",
                       description="Quita el modelo propio de un rol; vuelve al de su tier.")
    u.add_argument("--role", metavar="<rol>", required=True, help="rol: " + ", ".join(ROLES))
    u.add_argument("--runtime", choices=RUNTIMES, default=argparse.SUPPRESS, help=runtime_help)

    f = sub.add_parser(
        "fallback", help="Define la cadena de fallback de un tier (cuota, rate limit o red).",
        usage="atta models fallback <T0|T1|T2|T3> <modelo> [<modelo>...]\n"
              "       atta models fallback <T0|T1|T2|T3> --clear",
        description="Si un agente de ese tier falla por cuota, rate limit o red (nunca por "
                    "contexto), Atta prueba estos modelos en orden y deja el cambio en "
                    "events.jsonl. Un fallback nunca cuesta más que el modelo del tier "
                    "(haiku < sonnet < opus). --clear quita la cadena.")
    f.add_argument("values", nargs="+", metavar="<tier> [<modelo>...]",
                   help="tier y los modelos de la cadena, en orden")
    f.add_argument("--clear", action="store_true", help="quita la cadena del tier")
    f.add_argument("--runtime", choices=RUNTIMES, default=argparse.SUPPRESS, help=runtime_help)

    r = sub.add_parser("reset", help="Quita «models» de atta/config.json (modelos de fábrica).",
                       description="Quita la clave «models» de atta/config.json y vuelve a "
                                   "escribir la línea model: de los agentes.")
    r.add_argument("--runtime", choices=RUNTIMES, default=argparse.SUPPRESS, help=runtime_help)

