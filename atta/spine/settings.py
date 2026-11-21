"""`atta settings` (S14, L24): view, get, set and reset each key of `atta/config.json`.

`atta settings` prints the table CLAVE / VALOR / POR DEFECTO / DESCRIPCIÓN of every key in
settings_registry.py (a `*` marks values that differ from the default). `get <clave>` prints the
value (JSON for lists and objects). `set <clave> <valor>` parses the value by the key's type
(lists as `a,b` or a JSON array; booleans sí/no/true/false), runs config.validate on the new
config and writes atta/config.json atomically keeping every other key (models.py's writer).
`reset <clave>` removes the key (or nested key) so the default applies. Unknown or read-only keys
and invalid values exit 2 and write nothing. `atta/config.json` is a protected path: only a human
changes it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import models, project, settings_registry
from .settings_registry import Setting

OK, REFUSED = 0, 2
SIGN = "— Atta"
CELL_WIDTH = 40
_MISSING = object()
_TRUE, _FALSE = ("sí", "si", "true"), ("no", "false")


class SettingsError(ValueError):
    """A refused settings command; message in Spanish."""


# ---------------------------------------------------------------- values

def _lookup(data: dict, path: tuple):
    value = data
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def effective(cfg: dict, setting: Setting):
    """The value in effect: the config's, or the registry default when the key is absent."""
    value = _lookup(cfg, setting.path)
    return setting.default if value is _MISSING else value


def show(value) -> str:
    """One-line text of a value: texts as is, booleans sí/no, the rest as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "sí" if value else "no"
    return json.dumps(value, ensure_ascii=False)


def _summary(setting: Setting, value) -> str:
    if setting.key == "prices_usd_per_mtok":
        if not value:
            return "sin precios"
        return f"{len(value)} modelo{'' if len(value) == 1 else 's'}"
    return "de fábrica" if value == setting.default else "propio"


def cell(setting: Setting, value) -> str:
    """Table text of a value (read-only keys summarized, long values cut)."""
    if setting.type == "summary":
        text = _summary(setting, value)
    elif isinstance(value, list):
        text = ", ".join(str(v) for v in value) or "(vacía)"
    else:
        text = show(value)
    return text if len(text) <= CELL_WIDTH else text[:CELL_WIDTH - 1] + "…"


def parse(setting: Setting, text: str):
    """The value of `text` for `setting`'s type; SettingsError (Spanish) when it does not fit."""
    key, raw = setting.key, text.strip()
    if setting.type == "int":
        if re.fullmatch(r"[+-]?\d+", raw) and int(raw) >= 1:
            return int(raw)
        raise SettingsError(f"{key} debe ser un entero mayor que 0, no «{text}».")
    if setting.type == "bool":
        if raw.casefold() in _TRUE:
            return True
        if raw.casefold() in _FALSE:
            return False
        raise SettingsError(f"{key} debe ser sí o no (o true/false), no «{text}».")
    if setting.type == "enum":
        if raw in setting.choices:
            return raw
        choices = setting.choices
        raise SettingsError(f"{key} debe ser {', '.join(choices[:-1])} o {choices[-1]}, "
                            f"no «{text}».")
    if setting.type == "list":
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value):
                return [v.strip() for v in value]
            raise SettingsError(f'{key} debe ser una lista de textos (a,b o ["a", "b"]), '
                                f"no «{text}».")
        return [part.strip() for part in raw.split(",") if part.strip()]
    if not raw:
        raise SettingsError(f"{key} no puede estar vacío.")
    return raw


# ---------------------------------------------------------------- config.json edits

def _assign(data: dict, path: tuple, value) -> None:
    node = data
    for depth, part in enumerate(path[:-1]):
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise SettingsError(f"config.json: {'.'.join(path[:depth + 1])} debe ser un objeto.")
    node[path[-1]] = value


def _remove(data: dict, path: tuple) -> None:
    """Delete the key at `path` and every parent section left empty."""
    parents = [data]
    for part in path[:-1]:
        parents.append(parents[-1][part])
    del parents[-1][path[-1]]
    for depth in range(len(path) - 1, 0, -1):
        if parents[depth]:
            break
        del parents[depth - 1][path[depth - 1]]


def _setting(key: str, *, editing: bool) -> Setting:
    setting = settings_registry.find(key)
    if setting is None:
        raise SettingsError(f"La clave «{key}» no existe; mira las claves con `atta settings`.")
    if editing and not setting.editable:
        raise SettingsError(f"La clave «{key}» es de solo lectura en atta settings: "
                            f"{setting.how}.")
    return setting


def _save(root: Path, data: dict) -> str:
    try:
        models._save_config(root, data)  # validates with config.validate, writes atomically
    except models.ModelsError as exc:
        raise SettingsError(str(exc)) from None
    return project.config_path(root).relative_to(root).as_posix()


def _raw(root: Path) -> dict:
    try:
        return models._raw_config(root)
    except models.ModelsError as exc:
        raise SettingsError(str(exc)) from None


# ---------------------------------------------------------------- commands

def render(root: Path, cfg: dict) -> str:
    rows, changed = [("CLAVE", "VALOR", "POR DEFECTO", "DESCRIPCIÓN")], False
    for setting in settings_registry.SETTINGS:
        value = effective(cfg, setting)
        differs = value != setting.default
        changed = changed or differs
        rows.append((setting.key, cell(setting, value) + (" *" if differs else ""),
                     cell(setting, setting.default), setting.description))
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    rel = project.config_path(root).relative_to(root).as_posix()
    out = [f"Configuración de Atta en {root} ({rel}):"]
    out += ["  ".join([*(c.ljust(w) for c, w in zip(r, widths)), r[3]]) for r in rows]
    if changed:
        out.append("* distinto del valor por defecto.")
    out += ["Consulta con `atta settings get <clave>`; cambia con `atta settings set <clave> "
            "<valor>` o `atta settings reset <clave>`.", SIGN]
    return "\n".join(out)


def _get(cfg: dict, key: str) -> int:
    value = effective(cfg, _setting(key, editing=False))
    if isinstance(value, str):
        print(value)
    elif isinstance(value, dict):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(value, ensure_ascii=False))
    return OK


def _set(root: Path, cfg: dict, key: str, text: str) -> int:
    setting = _setting(key, editing=True)
    value = parse(setting, text)
    old = effective(cfg, setting)
    if value == old and type(value) is type(old):
        print(f"Atta: {key} ya vale {show(old)}; no cambié nada.\n{SIGN}")
        return OK
    data = _raw(root)
    _assign(data, setting.path, value)
    rel = _save(root, data)
    print(f"Atta cambió {key} en {rel}: {show(old)} → {show(value)}.\n{SIGN}")
    return OK


def _reset(root: Path, cfg: dict, key: str) -> int:
    setting = _setting(key, editing=True)
    default = show(setting.default)
    data = _raw(root)
    if _lookup(data, setting.path) is _MISSING:
        print(f"Atta: {key} ya usa el valor por defecto ({default}); no cambié nada.\n{SIGN}")
        return OK
    old = show(effective(cfg, setting))
    _remove(data, setting.path)
    rel = _save(root, data)
    print(f"Atta restableció {key} en {rel}: {old} → {default} (por defecto).\n{SIGN}")
    return OK


def cmd_settings(args, cfg, jobs) -> int:
    root = Path(args.root)
    action = args.settings_action
    try:
        if action is None:
            print(render(root, cfg))
            return OK
        if action == "get":
            return _get(cfg, args.key)
        if action == "set":
            return _set(root, cfg, args.key, args.value)
        return _reset(root, cfg, args.key)
    except SettingsError as exc:
        tail = "" if action == "get" else " No cambié nada."
        print(f"{exc}{tail}", file=sys.stderr)
        return REFUSED
    except OSError as exc:
        print(f"No se pudo escribir en {root}: {exc.strerror or exc}.", file=sys.stderr)
        return REFUSED


def register(subparsers) -> None:
    """Add `atta settings` (and its get/set/reset actions) to the `atta` CLI."""
    keys = ", ".join(s.key for s in settings_registry.SETTINGS)
    p = subparsers.add_parser(
        "settings", help="Muestra o cambia la configuración del proyecto (atta/config.json).",
        description="Sin acción, imprime la tabla CLAVE / VALOR / POR DEFECTO / DESCRIPCIÓN "
                    "(«*» marca lo distinto del valor por defecto). get imprime un valor; set "
                    "lo cambia y reset lo quita para que rija el valor por defecto. Una clave "
                    "desconocida o de solo lectura, o un valor inválido, sale 2 sin escribir "
                    "nada.",
        epilog="Claves: " + keys + ". Listas: a,b o [\"a\", \"b\"]; sí/no: sí, no, true, false.")
    p.set_defaults(func=cmd_settings, settings_action=None)
    sub = p.add_subparsers(dest="settings_action", metavar="<acción>")
    g = sub.add_parser("get", help="Imprime el valor de una clave (JSON para listas).",
                       description="Imprime el valor vigente de una clave (JSON para listas).")
    g.add_argument("key", metavar="<clave>", help="clave con punto, p. ej. labels.queue")
    s = sub.add_parser("set", help="Cambia el valor de una clave.",
                       description="Cambia una clave de atta/config.json; valida antes de "
                                   "escribir y conserva las demás claves.")
    s.add_argument("key", metavar="<clave>", help="clave con punto, p. ej. max_rounds")
    s.add_argument("value", metavar="<valor>", help="valor nuevo según el tipo de la clave")
    r = sub.add_parser("reset", help="Quita una clave para que rija su valor por defecto.",
                       description="Quita la clave de atta/config.json; rige su valor por "
                                   "defecto.")
    r.add_argument("key", metavar="<clave>", help="clave con punto, p. ej. merge_method")
