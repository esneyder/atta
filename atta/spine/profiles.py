"""Packaged profiles (F2.y T3, S6): saved sets of tier models and delivery, chosen per project.

`templates/claude/profiles.json` holds the profiles Atta ships (`economico`, `equilibrado`,
`calidad`); it is loaded and validated like models.json. A project picks one with
`"profile": "<id>"` in atta/config.json (absent = no profile = factory). Names are accepted
with accents and any case («Económico» is `economico`); the stored id is ASCII.

Precedence for models (S6): factory < profile < the project's `models` tiers/roles overrides:
`models.table` layers them, and `atta models` shows origin `perfil` for a tier the profile
decided. Delivery: a profile fixes `delivery.auto_merge_up_to`, but the config key is the only
thing the pipeline reads (config.auto_merges), so `atta profiles use` WRITES it explicitly next
to `profile` and says so in its output; an explicit `delivery` is never changed behind the
person's back, and `atta profiles clear` leaves it as written.

`atta profiles` lists them (current marked; `--json`), `show <name>` previews the effect against
the current config, `use <name>` / `clear` write atta/config.json atomically (models.py's
writer) and sync the agents' `model:` lines like `atta models set`. Anything invalid exits 2
and writes nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config, models

OK, REFUSED = 0, 2
SIGN = "— Atta"
KEYS = ("description", "models", "delivery")


class ProfilesError(models.ModelsError):
    """Invalid profile name or profiles.json; message in Spanish."""


def _path(templates) -> Path:
    return models._templates(templates) / "claude" / "profiles.json"


def _check(name, entry) -> None:
    where = f"profiles.json: {name}"
    if not isinstance(entry, dict) or set(entry) != set(KEYS):
        raise ProfilesError(f"{where} debe ser un objeto con {', '.join(KEYS)}.")
    if not isinstance(entry["description"], str) or not entry["description"]:
        raise ProfilesError(f"{where}.description debe ser un texto no vacío.")
    per_runtime = entry["models"]
    if not isinstance(per_runtime, dict) or set(per_runtime) != set(models.RUNTIMES):
        raise ProfilesError(f"{where}.models debe definir {', '.join(models.RUNTIMES)}.")
    for runtime, tiers in per_runtime.items():
        if not isinstance(tiers, dict) or set(tiers) != set(models.TIERS):
            raise ProfilesError(f"{where}.models.{runtime} debe definir {', '.join(models.TIERS)}.")
        for tier, model in tiers.items():
            problem = models.model_error(runtime, model)
            if problem:
                raise ProfilesError(f"{where}.models.{runtime}.{tier}: {problem}")
    delivery = entry["delivery"]
    if (not isinstance(delivery, dict) or set(delivery) != {"auto_merge_up_to"}
            or delivery["auto_merge_up_to"] not in config.RISK_LEVELS):
        raise ProfilesError(f"{where}.delivery debe ser {{\"auto_merge_up_to\": "
                            f"{'|'.join(config.RISK_LEVELS)}}}.")


def load(templates: str | Path | None = None) -> dict:
    """Profile id → entry from profiles.json, validated (ProfilesError otherwise)."""
    path = _path(templates)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfilesError(f"No se pudo leer la plantilla profiles.json: "
                            f"{exc.strerror or exc}.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProfilesError(f"La plantilla profiles.json no es JSON válido ({exc}).") from exc
    if not isinstance(data, dict) or not data:
        raise ProfilesError("profiles.json debe ser un objeto con al menos un perfil.")
    for name, entry in data.items():
        if name != models.role_name(name):
            raise ProfilesError(f"profiles.json: el id «{name}» debe ir en minúsculas y sin "
                                "acentos.")
        _check(name, entry)
    return data


def names_text(found: dict) -> str:
    ids = list(found)
    return ", ".join(ids[:-1]) + f" o {ids[-1]}" if len(ids) > 1 else ids[0]


def resolve_name(name, templates=None) -> str:
    """The profile id for `name` (accents and case ignored), or ProfilesError."""
    found = load(templates)
    key = models.role_name(name) if isinstance(name, str) else name
    if key in found:
        return key
    raise ProfilesError(f"El perfil «{name}» no existe: usa {names_text(found)}.")


def tiers(name: str, runtime: str = models.RUNTIME, templates=None) -> dict:
    """The tier → model map the profile `name` fixes for `runtime`."""
    return dict(load(templates)[resolve_name(name, templates)]["models"][runtime])


def level(name: str, templates=None) -> str:
    return load(templates)[resolve_name(name, templates)]["delivery"]["auto_merge_up_to"]


def config_error(value) -> str | None:
    """Why the `profile` value of config.json is invalid (without the «config.json: » prefix)."""
    try:
        found = load()
    except ProfilesError as exc:
        return str(exc)
    if isinstance(value, str) and value in found:
        return None
    shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return f"profile «{shown}» no existe: usa {names_text(found)}."


# ---------------------------------------------------------------- effect

def current(cfg: dict | None) -> str | None:
    value = (cfg or {}).get("profile")
    return value if isinstance(value, str) and value else None


def _delivery(cfg: dict | None) -> str:
    return ((cfg or {}).get("delivery") or {}).get("auto_merge_up_to", "high")


def effect(cfg: dict, name: str, templates=None) -> dict:
    """What `use name` changes against the current config (read-only; machine-readable)."""
    name = resolve_name(name, templates)
    entry = load(templates)[name]
    before = models.table(cfg, templates=templates)
    after = models.table(dict(cfg, profile=name), templates=templates)
    overrides = models.project_models(cfg).get("tiers") or {}
    wanted = entry["models"][models.RUNTIME]
    return {
        "agents": {r: {"from": before.agent_model(r), "to": after.agent_model(r)}
                   for r in models.ROLES if before.agent_model(r) != after.agent_model(r)},
        "delivery": {"from": _delivery(cfg), "to": entry["delivery"]["auto_merge_up_to"]},
        "kept_overrides": {t: overrides[t] for t in models.TIERS
                           if t in overrides and overrides[t] != wanted[t]},
        "role_overrides": sorted(models.project_models(cfg).get("roles") or {}),
        "tiers": {t: {"from": before.tiers.get(t), "to": after.tiers.get(t)}
                  for t in models.TIERS if before.tiers.get(t) != after.tiers.get(t)},
    }


def _tiers_text(tiers_: dict) -> str:
    return " · ".join(f"{t} {tiers_[t]}" for t in models.TIERS)


def _effect_lines(eff: dict, name: str, cur: str | None) -> list[str]:
    lines = [f"Efecto de elegir {name} sobre la configuración actual "
             f"(perfil actual: {cur or 'ninguno'}):"]
    for t, ch in eff["tiers"].items():
        lines.append(f"  {t}: {ch['from']} → {ch['to']}")
    d = eff["delivery"]
    if d["from"] != d["to"]:
        lines.append(f"  entrega (delivery.auto_merge_up_to): {d['from']} → {d['to']}")
    if eff["agents"]:
        lines.append("  agentes que cambian de modelo: " + ", ".join(
            f"{r} {c['from']} → {c['to']}" for r, c in eff["agents"].items()))
    if eff["kept_overrides"]:
        lines.append("  tus overrides de atta models ganan sobre el perfil: " + ", ".join(
            f"{t} se queda en {m}" for t, m in eff["kept_overrides"].items()))
    if eff["role_overrides"]:
        lines.append("  roles con modelo propio, que el perfil no toca: "
                     + ", ".join(eff["role_overrides"]))
    if len(lines) == 1:
        lines.append("  Sin cambios: ya tienes estos modelos y esta entrega.")
    return lines


def state(root: Path, cfg: dict, templates=None) -> dict:
    """Read-only, machine-readable state of `atta profiles --json` (the /atta:profiles menu)."""
    found, cur = load(templates), current(cfg)
    out = {}
    for name, entry in found.items():
        out[name] = {"current": name == cur, "delivery": entry["delivery"]["auto_merge_up_to"],
                     "description": entry["description"],
                     "effect": effect(cfg, name, templates),
                     "models": dict(entry["models"][models.RUNTIME])}
    return {"current": cur, "delivery": _delivery(cfg), "profiles": out, "root": str(root),
            "runtime": models.RUNTIME}


def render(root: Path, cfg: dict, templates=None) -> str:
    found, cur = load(templates), current(cfg)
    out = [f"Perfiles de Atta en {root} (runtime {models.RUNTIME}):"]
    for name, entry in found.items():
        mark = "*" if name == cur else " "
        out.append(f"{mark} {name}")
        out.append(f"    {_tiers_text(entry['models'][models.RUNTIME])} · "
                   f"entrega hasta {entry['delivery']['auto_merge_up_to']}")
        out.append(f"    {entry['description']}")
    out.append(f"Perfil actual: {cur}." if cur else "Perfil actual: ninguno (modelos de fábrica).")
    out += ["Mira el efecto con `atta profiles show <perfil>`; elige con `atta profiles use "
            "<perfil>` o vuelve a fábrica con `atta profiles clear`.", SIGN]
    return "\n".join(out)


# ---------------------------------------------------------------- commands

def _refuse(message: str) -> int:
    print(f"{message} No cambié nada.", file=sys.stderr)
    return REFUSED


def _use(root: Path, cfg: dict, name: str) -> int:
    name = resolve_name(name)
    entry = load()[name]
    new_level = entry["delivery"]["auto_merge_up_to"]
    data = models._raw_config(root)
    raw_delivery = data.get("delivery") if isinstance(data.get("delivery"), dict) else {}
    if data.get("profile") == name and raw_delivery.get("auto_merge_up_to") == new_level:
        print(f"Atta: el proyecto ya usa el perfil {name}; no cambié nada.\n{SIGN}")
        return OK
    old_level = _delivery(cfg)
    data["profile"] = name
    data["delivery"] = {**raw_delivery, "auto_merge_up_to": new_level}
    return models._change(
        root, data, models.RUNTIME,
        f"Atta: el perfil {name} queda fijo en {{config}}: "
        f"{_tiers_text(entry['models'][models.RUNTIME])}, y la entrega "
        f"(delivery.auto_merge_up_to) queda escrita: {old_level} → {new_level}. Tus overrides "
        "de atta models siguen ganando sobre el perfil.")


def _clear(root: Path) -> int:
    data = models._raw_config(root)
    if "profile" not in data:
        print(f"Atta: el proyecto no usa un perfil; no cambié nada.\n{SIGN}")
        return OK
    name = data.pop("profile")
    shown = ((data.get("delivery") or {}).get("auto_merge_up_to")
             or config.DEFAULTS["delivery"]["auto_merge_up_to"])
    return models._change(
        root, data, models.RUNTIME,
        f"Atta quitó el perfil {name} de {{config}}: vuelven los modelos de fábrica (con tus "
        f"overrides de atta models). La entrega sigue como está ({shown}); cámbiala con "
        "`atta settings set delivery.auto_merge_up_to <nivel>`.")


def cmd_profiles(args, cfg, jobs) -> int:
    root = Path(args.root)
    action = getattr(args, "profiles_action", None)
    try:
        if getattr(args, "json", False):
            if action is not None:
                return _refuse("--json solo se usa sin acción (atta profiles --json).")
            print(json.dumps(state(root, cfg), indent=2, sort_keys=True, ensure_ascii=False))
            return OK
        if action is None:
            print(render(root, cfg))
            return OK
        if action == "show":
            name = resolve_name(args.name)
            entry = load()[name]
            eff = effect(cfg, name)
            print("\n".join([
                f"Perfil {name}: {entry['description']}",
                f"  Modelos: {_tiers_text(entry['models'][models.RUNTIME])}",
                f"  Entrega: Atta mergea sola hasta riesgo {entry['delivery']['auto_merge_up_to']}",
                *_effect_lines(eff, name, current(cfg)),
                f"Elígelo con `atta profiles use {name}`.", SIGN]))
            return OK
        if action == "use":
            return _use(root, cfg, args.name)
        return _clear(root)
    except models.ModelsError as exc:  # includes ProfilesError
        return _refuse(str(exc))
    except OSError as exc:
        print(f"No se pudo escribir en {root}: {exc.strerror or exc}.", file=sys.stderr)
        return REFUSED


def register(subparsers) -> None:
    """Add `atta profiles` (and its show/use/clear actions) to the `atta` CLI."""
    p = subparsers.add_parser(
        "profiles", help="Perfiles de fábrica (económico, equilibrado, calidad) que fijan modelos "
                         "y entrega.",
        description="Sin acción, lista los perfiles con el actual marcado (con --json, el mismo "
                    "estado y el efecto de cada uno como JSON, solo lectura). show <perfil> "
                    "muestra qué cambiaría; use <perfil> guarda «profile» y la entrega "
                    "(delivery.auto_merge_up_to) en atta/config.json y vuelve a escribir la "
                    "línea model: de los agentes; clear quita «profile». Tus overrides de atta "
                    "models ganan sobre el perfil. Un perfil inexistente sale 2 sin escribir "
                    "nada.",
        epilog="Perfiles: económico (también economico), equilibrado y calidad. Los acentos y "
               "las mayúsculas no importan.")
    p.add_argument("--json", action="store_true",
                   help="solo lectura: imprime en JSON (claves ordenadas) los perfiles, el actual "
                        "y el efecto de cada uno; no escribe nada (lo usan los menús /atta:*)")
    p.set_defaults(func=cmd_profiles, profiles_action=None)
    sub = p.add_subparsers(dest="profiles_action", metavar="<acción>")
    s = sub.add_parser("show", help="Muestra un perfil y su efecto sobre la configuración actual.",
                       description="Muestra los modelos y la entrega del perfil y qué cambiaría "
                                   "frente a la configuración actual. No escribe nada.")
    s.add_argument("name", metavar="<perfil>", help="economico, equilibrado o calidad")
    u = sub.add_parser("use", help="Elige un perfil para este proyecto.",
                       description="Guarda «profile» y delivery.auto_merge_up_to en "
                                   "atta/config.json y sincroniza la línea model: de los "
                                   "agentes.")
    u.add_argument("name", metavar="<perfil>", help="economico, equilibrado o calidad")
    sub.add_parser("clear", help="Quita el perfil del proyecto (vuelven los modelos de fábrica).",
                   description="Quita «profile» de atta/config.json y vuelve a escribir la línea "
                               "model: de los agentes. La entrega queda como está.")
