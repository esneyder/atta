"""Memoria cultivada (F3 T4, S6; arquitectura §8 K1 y §10; PLAN §6.1 G9 y G12).

The Cultivadora as deterministic code. `collect(job_dir)` reads every `round-*/review-*.md` and
`round-*/refute*.md` of a job and keeps each `PREVENTION:` line of a review with findings (`CHANGES`, or a `PASS` with
minors) that still has at least one finding the Refutador did not refute (a finding with any UPHELD line
stands, as in next.py; a clean PASS never counts). `Lessons(root)` owns
`<root>/atta/memory/lessons.md`: one `## <slug>` section per lesson, parsed and written
deterministically, replaced atomically under the lock of `atta/memory/`.

A lesson is an evolving topic, not a log (G9): `upsert` finds the most similar existing rule
(token Jaccard ≥ 0.6 after lowercasing, accent folding and stopword removal) and updates it —
new case first, at most 5 cases, `Veces` counts distinct jobs, the rule text stays and a new
phrasing is kept under `Variantes:` (newest first, at most 3) only when it is < 0.9 similar to
every phrasing already there. Otherwise it creates a lesson. `harvest(job_id)` is collect +
upsert for one job, idempotent through `atta/memory/.harvested` (one job id per line).
`relevant(paths)` picks lessons for a prompt within BUDGET_CHARS (G12).

Ratchet (K1, F3.x T3): `ratchet(open_issue)` calls `open_issue(lesson)` once for every lesson that is
mechanizable (`ast-grep` or `test`), seen in RATCHET_TIMES or more jobs and still without an issue,
and keeps the returned number as `Issue: #N` right away (one atomic write per issue, under the
lock), so a lesson never gets a second issue.

CLI (wired by cli.py): `atta memory` lists the lessons; `atta memory harvest <job>` harvests one.
"""

from __future__ import annotations

import os
import posixpath
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from fnmatch import fnmatchcase
from pathlib import Path

from .ids import InvalidId, sanitize_id
from .lock import LockTimeout, job_lock
from .paths import project_rel, root_of_job_dir
from .store import _replace
from .timeutil import utcnow

MEMORY_RELPATH = Path("atta") / "memory"
LESSONS_FILE = "lessons.md"
HARVESTED_FILE = ".harvested"
LESSONS_LABEL = "atta/memory/lessons.md"

MERGE_SIMILARITY = 0.6
SAME_SIMILARITY = 0.9
MAX_CASES = 5
MAX_VARIANTS = 3
MAX_SLUG_WORDS = 6
BUDGET_CHARS = 1500
RULE_CHARS = 300
MECHANIZABLE = ("ast-grep", "test", "no")
RATCHET_MECHANIZABLE = ("ast-grep", "test")
RATCHET_TIMES = 2
OK, REFUSED = 0, 2

STOPWORDS = frozenset("""
a al algo como con de del e el en entre es esta este esto la las le les lo los mas mismo muy
no o para pero por que se si sin sobre son su sus un una unas uno unos y ya
an and are as at be by do does each every for from if in into is it its not of on or so than
that the their then this to was were when which while with
""".split())

HEADER = ("# Lecciones de Atta\n\n"
          "Memoria cultivada: la escribe `atta memory harvest` con las `PREVENTION:` de las "
          "revisiones. Una lección por tema; las parecidas se actualizan, no se repiten.\n")

_ROUND = re.compile(r"round-(\d+)\Z")
_REVIEW = re.compile(r"review-(.+)\.md\Z")
_FINDING_AT = re.compile(r"^- \[(blocker|major|minor)\] `?([^\s`]+)`?", re.M)
_REFUTE_LINE = re.compile(r"^- (UPHELD|REFUTED) `?([^\s`]+)`? `?([^\s`]+)`?(?:\s|$)", re.M)
_PREVENTION = re.compile(r"^[ \t]*PREVENTION:[ \t]*(.*)$", re.M)
_LINE_SUFFIX = re.compile(r":\d+(?:[-:]\d+)*\Z")
_QUOTED = re.compile(r"""^(?:(?:checklist|bug-core|ítem|item)[^"“«']*)?["“«'](.+?)["”»']\s*\.?\Z""",
                     re.I)
_SLUG = re.compile(r"[0-9a-z][0-9a-z-]*\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_FIELD = re.compile(r"(Regla|Variantes|Casos|Veces|Actualizada|Mecanizable|Issue):[ \t]*(.*)\Z")
_ISSUE = re.compile(r"#([1-9][0-9]*)\Z")


class LessonsError(RuntimeError):
    """lessons.md is unreadable or damaged, or the job to harvest does not exist."""


@dataclass
class Lesson:
    slug: str
    rule: str
    cases: list = field(default_factory=list)  # [(job_id, [path, ...])], newest first
    times: int = 1
    updated: str = ""
    mechanizable: str | None = None
    variants: list = field(default_factory=list)
    issue: int | None = None  # the `atta:ratchet` issue opened for it (K1)


# ---------------------------------------------------------------- text helpers

def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


def tokens(text: str) -> list[str]:
    """Words of `text`, lowercased, accent-folded, without stopwords (order kept)."""
    return [w for w in re.findall(r"[0-9a-z]+", _fold(text)) if w not in STOPWORDS]


def similarity(a: str, b: str) -> float:
    """Jaccard index of the token sets of two rules; 0.0 when either has no tokens."""
    sa, sb = set(tokens(a)), set(tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def generalize(prevention: str) -> str:
    """The rule of a PREVENTION line: one line, without a `checklist item "…"` wrapper."""
    text = _clean(prevention)
    m = _QUOTED.match(text)
    return _clean(m.group(1)) if m else text


def _mechanizable(rule: str) -> str | None:
    return "ast-grep" if "ast-grep" in _fold(rule) else None


def _slug(rule: str, taken: set[str]) -> str:
    base = "-".join(tokens(rule)[:MAX_SLUG_WORDS]) or "leccion"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").strip()


# ---------------------------------------------------------------- collect

def _round_dirs(job_dir: Path) -> list[Path]:
    found = []
    for p in job_dir.iterdir() if job_dir.is_dir() else []:
        m = _ROUND.match(p.name)
        if m and p.is_dir():
            found.append((int(m.group(1)), p))
    return [p for _, p in sorted(found)]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _refute_reviewer(token: str) -> str:
    """`code`, `review-code.md` or `atta-jardinera-code` → `code` (as next.py reads it)."""
    name = token.strip("`")
    for suffix in ("-retry.md", ".md"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    for prefix in ("review-", "atta-jardinera-", "atta-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return "security" if name == "soldado" else name


def collect(job_dir: str | Path, root: str | Path | None = None) -> list[dict]:
    """Preventions of one job: [{rule, job, reviewer, paths}] in round and reviewer order.

    Skips malformed reviews, reviews without findings (a clean PASS) and reviews whose every
    finding was REFUTED in a
    `refute*.md` of the same round. `paths` are the non-refuted finding paths, without line,
    relative to the project (S2): an absolute path inside `root` or its job worktree
    `<root>/.worktrees/<id>/` loses that prefix. Without `root` it is inferred from a
    `<root>/atta/jobs/<id>` job folder."""
    job_dir = Path(job_dir)
    if root is None:
        root = root_of_job_dir(job_dir)
    jid = job_dir.name
    out = []
    for rd in _round_dirs(job_dir):
        verdicts: dict[tuple[str, str], set[str]] = {}
        for ref in sorted(rd.glob("refute*.md")):
            for m in _REFUTE_LINE.finditer(_read(ref)):
                verdicts.setdefault((_refute_reviewer(m.group(2)), m.group(3)),
                                    set()).add(m.group(1))
        for rev in sorted(rd.glob("review-*.md")):
            name = _REVIEW.match(rev.name)
            text = _read(rev)
            lines = text.splitlines()
            if not name or not lines or lines[0].strip() not in ("VERDICT: CHANGES", "VERDICT: PASS"):
                continue
            reviewer = name.group(1)
            paths = []
            for m in _FINDING_AT.finditer(text):
                if verdicts.get((reviewer, m.group(2))) == {"REFUTED"}:
                    continue
                path = project_rel(_LINE_SUFFIX.sub("", m.group(2)), root)
                if path and path not in paths:
                    paths.append(path)
            if not paths:
                continue
            for m in _PREVENTION.finditer(text):
                rule = generalize(m.group(1))
                if rule:
                    out.append({"rule": rule, "job": jid, "reviewer": reviewer,
                                "paths": list(paths)})
    return out


# ---------------------------------------------------------------- parse / serialize

def _damaged(lineno: int, why: str) -> LessonsError:
    return LessonsError(f"{LESSONS_LABEL} está dañado (línea {lineno}): {why} "
                        "Corrígelo a mano o restáuralo desde git; Atta no lo tocó.")


def _finish(lesson: dict | None, lineno: int, out: list[Lesson]) -> None:
    if lesson is None:
        return
    for key, label in (("rule", "Regla"), ("times", "Veces"), ("updated", "Actualizada")):
        if lesson.get(key) is None:
            raise _damaged(lineno, f"a la lección «{lesson['slug']}» le falta «{label}:».")
    out.append(Lesson(slug=lesson["slug"], rule=lesson["rule"], cases=lesson["cases"],
                      times=lesson["times"], updated=lesson["updated"],
                      mechanizable=lesson.get("mechanizable"), variants=lesson["variants"],
                      issue=lesson.get("issue")))


def parse(text: str) -> list[Lesson]:
    """Lessons of a lessons.md text, in file order. Raises LessonsError on any damage."""
    out: list[Lesson] = []
    cur: dict | None = None
    seen_fields: set[str] = set()
    target: str | None = None  # "cases" or "variants" while reading a `- ` list
    slugs: set[str] = set()
    lineno = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if line.startswith("## "):
            _finish(cur, lineno, out)
            slug = line[3:].strip()
            if not _SLUG.match(slug):
                raise _damaged(lineno, f"«{slug}» no es un nombre de lección válido.")
            if slug in slugs:
                raise _damaged(lineno, f"la lección «{slug}» está repetida.")
            slugs.add(slug)
            cur = {"slug": slug, "rule": None, "times": None, "updated": None,
                   "cases": [], "variants": []}
            seen_fields, target = set(), None
            continue
        if cur is None or not line.strip():
            continue  # header before the first lesson, blank lines
        if line.startswith("- "):
            item = line[2:].strip()
            if target == "variants" and item:
                cur["variants"].append(item)
            elif target == "cases" and item:
                job, _, rest = item.partition(":")
                try:
                    job = sanitize_id(job.strip())
                except InvalidId:
                    raise _damaged(lineno, f"el caso «{item}» no empieza con un id de job.") \
                        from None
                paths = [p.strip() for p in rest.split(",") if p.strip()]
                cur["cases"].append((job, paths))
            else:
                raise _damaged(lineno, f"la línea «{line}» no pertenece a Casos ni a Variantes.")
            continue
        m = _FIELD.match(line)
        if not m:
            raise _damaged(lineno, f"no se entiende la línea «{line}».")
        key, value = m.group(1), m.group(2).strip()
        if key in seen_fields:
            raise _damaged(lineno, f"«{key}:» aparece dos veces en «{cur['slug']}».")
        seen_fields.add(key)
        target = None
        if key in ("Variantes", "Casos"):
            if value:
                raise _damaged(lineno, f"«{key}:» va sola y sus elementos en líneas «- ».")
            target = "variants" if key == "Variantes" else "cases"
        elif key == "Regla":
            if not value:
                raise _damaged(lineno, "«Regla:» está vacía.")
            cur["rule"] = value
        elif key == "Veces":
            if not value.isdigit() or int(value) < 1:
                raise _damaged(lineno, f"«Veces: {value}» debe ser un entero mayor que 0.")
            cur["times"] = int(value)
        elif key == "Actualizada":
            try:
                ok = bool(_DATE.match(value)) and date.fromisoformat(value) is not None
            except ValueError:
                ok = False
            if not ok:
                raise _damaged(lineno, f"«Actualizada: {value}» debe ser una fecha AAAA-MM-DD.")
            cur["updated"] = value
        elif key == "Issue":
            m = _ISSUE.match(value)
            if not m:
                raise _damaged(lineno, f"«Issue: {value}» debe ser un número de issue como «#12».")
            cur["issue"] = int(m.group(1))
        else:  # Mecanizable
            if value not in MECHANIZABLE:
                raise _damaged(lineno, f"«Mecanizable: {value}» debe ser "
                                       f"{', '.join(MECHANIZABLE)}.")
            cur["mechanizable"] = value
    _finish(cur, lineno + 1, out)
    return out


def serialize(lessons: list[Lesson]) -> str:
    parts = [HEADER]
    for l in lessons:
        lines = [f"## {l.slug}", f"Regla: {_clean(l.rule)}"]
        if l.variants:
            lines += ["Variantes:"] + [f"- {_clean(v)}" for v in l.variants]
        lines.append("Casos:")
        for job, paths in l.cases:
            lines.append(f"- {job}: {', '.join(paths)}" if paths else f"- {job}")
        lines += [f"Veces: {l.times}", f"Actualizada: {l.updated}"]
        if l.mechanizable:
            lines.append(f"Mecanizable: {l.mechanizable}")
        if l.issue:
            lines.append(f"Issue: #{l.issue}")
        parts.append("\n".join(lines) + "\n")
    return "\n".join(parts)


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- merge (pure)

def _merge(lessons: list[Lesson], item: dict, today: str) -> str:
    """Fold one prevention into `lessons` in place; 'created' or 'updated'."""
    rule = _clean(item["rule"])
    job = sanitize_id(item["job"])
    paths = [_norm_path(p) for p in item.get("paths") or [] if _norm_path(p)]
    best, best_score = None, 0.0
    for lesson in lessons:
        score = max(similarity(rule, text) for text in [lesson.rule, *lesson.variants])
        if score > best_score:
            best, best_score = lesson, score
    if best is None or best_score < MERGE_SIMILARITY:
        lessons.append(Lesson(slug=_slug(rule, {l.slug for l in lessons}), rule=rule,
                              cases=[(job, paths)], times=1, updated=today,
                              mechanizable=_mechanizable(rule)))
        return "created"
    old = next((c for c in best.cases if c[0] == job), None)
    if old is None:
        best.times += 1
        merged = paths
    else:
        best.cases.remove(old)
        merged = paths + [p for p in old[1] if p not in paths]
    best.cases = [(job, merged)] + best.cases[:MAX_CASES - 1]
    if all(similarity(rule, t) < SAME_SIMILARITY for t in [best.rule, *best.variants]):
        best.variants = [rule] + best.variants[:MAX_VARIANTS - 1]
    best.mechanizable = best.mechanizable or _mechanizable(rule)
    best.updated = today
    return "updated"


# ---------------------------------------------------------------- relevance (G12)

def _related(case_path: str, query: str) -> bool:
    c, q = _norm_path(case_path), _norm_path(query)
    if fnmatchcase(c, q) or fnmatchcase(q, c):
        return True
    return posixpath.dirname(c) == posixpath.dirname(q)


def _truncate(text: str, size: int) -> str:
    return text if len(text) <= size else text[:size - 1].rstrip() + "…"


def render(lessons: list[Lesson]) -> str:
    """The prompt block of `lessons`: one line each, rule truncated to RULE_CHARS."""
    return "\n".join(f"- {_truncate(l.rule, RULE_CHARS)} (Veces: {l.times})" for l in lessons)


# ---------------------------------------------------------------- the store

class Lessons:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.dir = self.root / MEMORY_RELPATH
        self.path = self.dir / LESSONS_FILE
        self.harvested_path = self.dir / HARVESTED_FILE

    def load(self) -> list[Lesson]:
        """The lessons in file order; [] when lessons.md does not exist."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as exc:
            raise LessonsError(f"No se pudo leer {LESSONS_LABEL}: {exc}.") from exc
        return parse(text)

    def _harvested(self) -> list[str]:
        try:
            text = self.harvested_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as exc:
            raise LessonsError(f"No se pudo leer atta/memory/{HARVESTED_FILE}: {exc}.") from exc
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        return job_lock(self.dir)

    def upsert(self, lesson: dict, *, now: datetime | None = None) -> str:
        """Fold one prevention ({rule, job, paths}) into lessons.md: 'created' or 'updated'."""
        today = (now or utcnow()).date().isoformat()
        with self._locked():
            lessons = self.load()
            outcome = _merge(lessons, lesson, today)
            _write_atomic(self.path, serialize(lessons))
        return outcome

    def harvest(self, job_id: str, *, jobs_dir: str | Path | None = None,
                now: datetime | None = None) -> dict:
        """collect + upsert for one job, once: {created, updated, already}."""
        jid = sanitize_id(job_id)
        if jobs_dir is None:
            from . import project  # lazy: project imports the Filtro
            jobs_dir = project.jobs_dir(self.root)
        job_dir = Path(jobs_dir) / jid
        if not (job_dir / "job.json").exists():
            raise LessonsError(f"No existe el job {jid}.")
        today = (now or utcnow()).date().isoformat()
        with self._locked():
            done = self._harvested()
            if jid in done:
                return {"created": 0, "updated": 0, "already": True}
            lessons = self.load()
            counts = {"created": 0, "updated": 0}
            for item in collect(job_dir, self.root):
                counts[_merge(lessons, item, today)] += 1
            if counts["created"] or counts["updated"]:
                _write_atomic(self.path, serialize(lessons))
            _write_atomic(self.harvested_path, "".join(f"{j}\n" for j in sorted({*done, jid})))
        return {**counts, "already": False}

    def ratchet(self, open_issue) -> list[tuple[str, int]]:
        """Open the `atta:ratchet` issue of every lesson that needs one: [(slug, number)].

        `open_issue(lesson)` returns the issue number; it runs under the lock of atta/memory/ and
        each number is written before the next lesson, so an error keeps the issues already
        opened and a second run never opens them again."""
        opened: list[tuple[str, int]] = []
        with self._locked():
            lessons = self.load()
            for lesson in lessons:
                if (lesson.issue is None and lesson.times >= RATCHET_TIMES
                        and lesson.mechanizable in RATCHET_MECHANIZABLE):
                    lesson.issue = int(open_issue(lesson))
                    _write_atomic(self.path, serialize(lessons))
                    opened.append((lesson.slug, lesson.issue))
        return opened

    def relevant(self, paths: list[str], limit: int = 5,
                 budget: int = BUDGET_CHARS) -> list[Lesson]:
        """Lessons for a prompt: those whose case paths share a directory or glob with `paths`
        (most Veces first), then the rest by Veces; at most `limit`, render() ≤ `budget`."""
        lessons = self.load()

        def key(item):
            index, l = item
            return (-l.times, -int(l.updated.replace("-", "")), index)

        ranked = sorted(enumerate(lessons), key=key)
        related = [l for _, l in ranked
                   if any(_related(p, q) for _, ps in l.cases for p in ps for q in paths)]
        ordered = related + [l for _, l in ranked if l not in related]
        chosen: list[Lesson] = []
        for l in ordered:
            if len(chosen) >= limit:
                break
            if len(render(chosen + [l])) <= budget:
                chosen.append(l)
        return chosen


# ---------------------------------------------------------------- CLI

def _err(message: str) -> None:
    import sys
    print(message, file=sys.stderr)


def cmd_memory(args, cfg, jobs) -> int:
    store = Lessons(args.root)
    try:
        if getattr(args, "memory_command", None) == "harvest":
            result = store.harvest(args.job, jobs_dir=jobs.root)
            if result["already"]:
                print(f"{args.job}: ya estaba cosechado; no se agregó nada.")
            else:
                print(f"{args.job}: {result['created']} lecciones nuevas, "
                      f"{result['updated']} actualizadas.")
            return OK
        lessons = sorted(store.load(), key=lambda l: -l.times)
    except (LessonsError, InvalidId, LockTimeout) as exc:
        _err(str(exc))
        return REFUSED
    if not lessons:
        print(f"Todavía no hay lecciones en {LESSONS_LABEL}.")
        return OK
    width = max(len("LECCIÓN"), *(len(l.slug) for l in lessons))
    print(f"{'LECCIÓN':<{width}}  {'VECES':>5}  {'MECANIZABLE':<11}  REGLA")
    for l in lessons:
        print(f"{l.slug:<{width}}  {l.times:>5}  {l.mechanizable or '-':<11}  "
              f"{_truncate(l.rule, 60)}")
    return OK


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "memory", help="Lecciones de la memoria cultivada (atta/memory/lessons.md).",
        description="Sin subcomando lista las lecciones: nombre, veces, si es mecanizable y la "
                    "regla. `harvest <job>` agrega las PREVENTION: de las revisiones con hallazgos del "
                    "job que el Refutador no refutó; una lección parecida se actualiza en vez de "
                    "repetirse. Cosechar dos veces el mismo job no agrega nada.",
        epilog="Códigos de salida: 0 ok, 2 lessons.md dañado, job inexistente o id inválido.")
    sub = p.add_subparsers(dest="memory_command", metavar="<subcomando>")
    h = sub.add_parser("harvest", help="Cosecha las PREVENTION: de un job.",
                       description="Cosecha las PREVENTION: de un job en lessons.md (una vez).")
    h.add_argument("job", help="id del job")
    p.set_defaults(func=cmd_memory)
