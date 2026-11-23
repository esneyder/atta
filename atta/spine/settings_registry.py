"""Every configurable key of `atta/config.json` (S14, L24): the single registry of `atta settings`.

Adding a configuration = adding one `Setting` here (and its rule to config.validate when the
spine depends on it). Keys are dotted paths into config.json (`labels.queue` →
`{"labels": {"queue": ...}}`). Types: `str` (non-empty text), `int` (integer ≥ 1), `bool`,
`enum` (one of `choices`), `list` (list of non-empty texts) and `summary` (read-only,
`editable=False`: shown as a summary and edited elsewhere, see `how`).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from . import config
from .guard import DEFAULT_DENY_COMMANDS

TYPES = ("str", "int", "bool", "enum", "list", "summary")
EDIT_BY_HAND = "edítalo en atta/config.json"


@dataclass(frozen=True)
class Setting:
    key: str
    type: str
    default: object
    description: str
    editable: bool = True
    choices: tuple = ()
    how: str = ""  # read-only keys: where to change them instead

    @property
    def path(self) -> tuple:
        return tuple(self.key.split("."))


def _default(key: str):
    value = config.DEFAULTS
    for part in key.split("."):
        value = value[part]
    return copy.deepcopy(value)


def _entry(key: str, type_: str, description: str, **extra) -> Setting:
    default = extra.pop("default") if "default" in extra else _default(key)
    return Setting(key, type_, default, description, **extra)


SETTINGS = (
    _entry("integration_branch", "str",
           "Rama donde Atta integra los PRs; nunca una rama prohibida."),
    _entry("forbidden_branches", "list",
           "Ramas donde Atta nunca integra ni empuja (al menos una)."),
    _entry("branch_prefix", "str", "Prefijo de las ramas de trabajo; termina en «/»."),
    _entry("max_rounds", "int", "Rondas máximas del Filtro y de revisión por job."),
    _entry("max_total_rounds", "int", "Tope global de rondas de un job."),
    _entry("concurrency", "int", "Jobs en curso a la vez."),
    _entry("merge_method", "enum", "Cómo mergea Atta el PR en la rama de integración.",
           choices=config.MERGE_METHODS),
    _entry("labels.queue", "str", "Label que pone un issue en la cola de Atta."),
    _entry("labels.needs_info", "str", "Label cuando Atta espera respuesta del autor."),
    _entry("labels.needs_human", "str", "Label cuando un job necesita a un humano."),
    _entry("labels.needs_review", "str",
           "Label cuando el PR está listo y te toca revisarlo y mergearlo (awaiting-merge).",
           default=config.OPTIONAL_LABELS["needs_review"]),
    _entry("protected_paths", "list",
           "Rutas que los agentes no pueden tocar (el Filtro y el guard las vigilan)."),
    _entry("delivery.auto_merge_up_to", "enum",
           "Riesgo máximo que Atta mergea sola en develop; por encima la Reina deja el PR "
           "listo y tú mergeas. none = nunca mergea (siempre PR; lo que escribe atta init), "
           "low = solo riesgo bajo, medium = bajo y medio, high = todo (por defecto en el "
           "código).", choices=config.RISK_LEVELS),
    _entry("scope.enforce", "bool",
           "Si el diff sale de FILES: del spec, o una ronda de corrección toca archivos que los "
           "hallazgos no nombran, el Filtro falla (sí) o solo avisa en checks.md (no)."),
    _entry("scope.max_files", "int",
           "Archivos cambiados a partir de los cuales el Filtro avisa que el diff es grande "
           "(nunca falla por esto)."),
    _entry("guard.deny_commands", "list",
           "Comandos de despliegue que el guard bloquea (reemplaza la lista de fábrica).",
           default=list(DEFAULT_DENY_COMMANDS)),
    _entry("prices_usd_per_mtok", "summary",
           f"Precios por millón de tokens para el costo (solo lectura: {EDIT_BY_HAND}).",
           editable=False, how=EDIT_BY_HAND),
    _entry("routing", "summary",
           f"Reviewers por riesgo y superficie (solo lectura: {EDIT_BY_HAND}).",
           editable=False, how=EDIT_BY_HAND),
    _entry("risk_rules", "summary",
           "Reglas que suben el riesgo según el diff: datos, seguridad, contratos, "
           f"concurrencia, entrega y código sin test (solo lectura: {EDIT_BY_HAND}).",
           editable=False, how=EDIT_BY_HAND),
    _entry("models", "summary", "Modelo de cada tier y rol (solo lectura: usa atta models).",
           default={}, editable=False, how="usa atta models"),
    _entry("profile", "summary",
           "Perfil de fábrica del proyecto (económico, equilibrado o calidad; solo lectura: usa "
           "atta profiles).", default=None, editable=False, how="usa atta profiles"),
)


def find(key: str) -> Setting | None:
    """The registry entry for `key`, or None."""
    return next((s for s in SETTINGS if s.key == key), None)
