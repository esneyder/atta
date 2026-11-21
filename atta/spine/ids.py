"""Job ids: built from issue number + title slug, and sanitized before any path use."""

from __future__ import annotations

import re
import unicodedata

MAX_SLUG_WORDS = 4
_VALID = re.compile(r"[0-9a-z][0-9a-z-]*\Z")


class InvalidId(ValueError):
    """An id that must not be used to build a path."""


def slugify(title: str) -> str:
    """ASCII, lowercase, accents stripped, at most MAX_SLUG_WORDS words joined by '-'."""
    decomposed = unicodedata.normalize("NFKD", title or "")
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[0-9a-z]+", ascii_text)
    return "-".join(words[:MAX_SLUG_WORDS])


def job_id(issue: int, title: str) -> str:
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        raise InvalidId(f"Número de issue inválido: {issue!r}.")
    slug = slugify(title)
    return sanitize_id(f"{issue}-{slug}" if slug else str(issue))


def sanitize_id(value) -> str:
    """Return value if it is a safe job id; raise InvalidId otherwise."""
    if not isinstance(value, str):
        raise InvalidId(f"Id de job inválido {value!r}: debe ser texto.")
    if not value:
        raise InvalidId(f"Id de job inválido {value!r}: está vacío.")
    if "/" in value or "\\" in value:
        raise InvalidId(f"Id de job inválido {value!r}: contiene separadores de ruta.")
    if ".." in value:
        raise InvalidId(f"Id de job inválido {value!r}: contiene '..'.")
    if not _VALID.match(value):
        raise InvalidId(f"Id de job inválido {value!r}: solo se permiten caracteres [0-9a-z-].")
    return value
