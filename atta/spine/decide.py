"""Reina determinista en riesgo bajo (F4 T1, S3): approve by rule, without a model.

`atta next` asks `evaluate` before spawning the Reina only when the job's risk is `low`: the
highest of the intake's RISK, the routed risk and the Filtro's `risk_effective` (`rule_risk`;
any value that is not low/medium/high makes it None, never low). Every signal must be present
and clean:

- the round's `checks.md` says `GATE: PASS` and its `COMMIT:` is the worktree HEAD;
- reviews: every `review-*.md` of the round counts (name matched case-insensitively), planned
  or not. Every file is judged: a valid primary and its `-retry` both count; an invalid
  primary is replaced by a valid retry (as `next` does); two primaries or two retries of one
  reviewer, an invalid retry or no valid file are doubts. A review or refute file dated more
  than FUTURE_SKEW_S in the future is a doubt. Every planned reviewer has one, each judged
  file is written after that checks.md, and says `VERDICT: PASS` without blocker/major
  findings (minor notes allowed) or `VERDICT: CHANGES` with only blocker/major findings, all
  REFUTED by the Refutador (a valid `refute.md` or `refute-retry.md` written after those
  reviews). Any finding line the parser cannot read (`- [blocker]  x`, `* [Major] …`), a
  CHANGES without findings, a minor finding in a CHANGES, an unrefuted finding or a malformed
  reviewer plan is a doubt;
- the round's `build.md` says `STATUS: DONE`, keeps the build.md contract (`next.check_build`)
  and has no ✗;
- scope: the spec's `FILES:` exists and names no catch-all entry (`**`, `**/*`, `*`, …); the
  diff `<base>...HEAD` changes at least one non-test path of `FILES:`, and every path outside
  `FILES:` is a test file by the DEFAULT test globs (a project cannot widen them here).
  Lockfiles get no exemption: outside `FILES:` they are a doubt;
- allowlist (S3 condition 4): every changed path, old and new side of a rename, is one of
  (a) source by the DEFAULT `source` globs, (b) a test by the DEFAULT `tests` globs or
  (c) plain docs (`*.md`, `*.rst`; never `.txt`, which is often a dependency list, nor
  agent instructions `CLAUDE.md`/`AGENTS.md`/`GEMINI.md`). A name list can never be
  complete, so the rule also doubts by structure: any path with a hidden component
  (`.cursor/`, `.eslintrc.js`), any NOT_SOURCE_DIRS folder at any depth (`scripts/`, `ci/`,
  `deploy/`, `tools/`, `docker/`, `config/`…) and any source file whose name has a
  BUILD_TOKENS word (`build.py`, `webpack.prod.js`, `settings_prod.py`, `Package.swift`,
  `manage.py`…), besides the NOT_SOURCE globs (`*.config.*`, shell scripts, `conftest.py`…).
  Anything else goes to the LLM Reina;
- no brake, and a project can add brakes but never remove them (an extra layer):
  * HARD_BRAKES: migrations and schema, CI, build and deploy configuration (any changed path,
    old or new);
  * every risk.py category (data, security, contracts, concurrency, delivery, untested) from
    the DEFAULT rules unioned with the project's `risk_rules`, whatever risk they rate;
  * test files outside `FILES:` are scanned too with the security and data content patterns;
  * an added or modified file whose patch has no lines (binary, `-diff` attribute, a path the
    patch parser cannot match) cannot be scanned: doubt.

All clean → `decision.md` in the Reina's format (first line `APPROVE`, `## Summary`, `## Why`
with the signals) and a `reina_rule` event that says the rule decided, not a model (the event
is written first: without decision.md there is no approval). Any signal missing, ambiguous or
failing → the LLM Reina decides as before (never a person): the first reason is recorded once
per stage entry in a `reina_rule` event with outcome `fallback`.

`evaluate` only reads files and runs local read-only git; `atta next` does the writes.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import gate, risk, scope

EVENT = "reina_rule"
REINA = "atta-reina"
SUMMARY = ("La Reina aprueba por regla, sin modelo: el riesgo es bajo y todas las señales están "
           "en verde.")
# Rule-only brakes (the Reina's prompt: migrations, CI, build or deployment configuration).
# risk.py's defaults are shared with the Filtro and stay as they are.
HARD_BRAKES = (
    # data and schema migrations
    "**/migrate/**", "**/migrations/**", "**/migration/**", "**/alembic/**", "**/*.sql",
    "**/schema.*", "**/schema/**", "**/*.prisma", "**/db/**",
    # CI
    "**/.github/**", "**/.gitlab-ci.yml", "**/.gitlab/**", "**/.circleci/**",
    "**/.travis.yml", "**/bitbucket-pipelines.yml", "**/azure-pipelines.yml",
    "**/Jenkinsfile*", "**/.drone.yml", "**/appveyor.yml", "**/.buildkite/**",
    "**/cloudbuild.yaml", "**/cloudbuild.yml", "**/buildspec.yml", "**/.pre-commit-config.yaml",
    "**/.husky/**", "**/tox.ini", "**/noxfile.py",
    # build
    "**/Makefile", "**/GNUmakefile", "**/*.mk", "**/*.gradle", "**/*.gradle.kts",
    "**/gradle/**", "**/gradlew*", "**/pom.xml", "**/build.xml", "**/CMakeLists.txt",
    "**/*.cmake", "**/meson.build", "**/build.sbt", "**/BUILD", "**/BUILD.bazel",
    "**/WORKSPACE", "**/*.bzl", "**/Rakefile", "**/justfile", "**/Taskfile.yml",
    "**/setup.py", "**/setup.cfg", "**/pyproject.toml", "**/package.json", "**/MANIFEST.in",
    # deploy
    "**/Dockerfile*", "**/*.dockerfile", "**/.dockerignore", "**/docker-compose*",
    "**/compose.yml", "**/compose.yaml", "**/Procfile", "**/*.tf", "**/*.tfvars", "**/deploy/**",
    "**/infra/**", "**/k8s/**", "**/helm/**", "**/fly.toml", "**/netlify.toml",
    "**/vercel.json", "**/app.yaml", "**/serverless.yml",
)
# Allowlist: the rule approves only diffs made of these kinds of files (old and new paths).
# Source and test globs are always risk.DEFAULT_RULES's, never the project's.
DOC_GLOBS = ("**/*.md", "**/*.rst")
NOT_DOCS = ("**/*requirements*.txt", "**/*constraints*.txt", "**/requirements/**",
            "**/CLAUDE.md", "**/AGENTS.md", "**/GEMINI.md")
NOT_SOURCE = ("**/*.config.*", "**/tsconfig*.json", "**/*.sh", "**/*.ps1", "**/*.bat",
              "**/*.cmd", "**/.github/**", "**/build.rs", "**/gulpfile.*", "**/Gruntfile.*",
              "**/fabfile.py", "**/settings.py", "**/conanfile.*", "**/vcpkg.json",
              "**/conftest.py")
NOT_SOURCE_DIRS = ("scripts", "bin", "ci", "deploy", "infra", "ops", "config", "configs",
                   "settings", "requirements", "tools", "tooling", "docker", "build",
                   "release", "project", "k8s", "helm", "terraform")
# A source file whose name (split on non-alphanumerics, case-insensitive) has one of these
# words is a build, deploy, task or config file for the rule.
BUILD_TOKENS = frozenset((
    "build", "builder", "deploy", "release", "config", "conf", "cfg", "configure",
    "settings", "setup", "webpack", "rollup", "vite", "esbuild", "eslintrc", "babelrc",
    "prettierrc", "manage", "tasks", "task", "magefile", "fabfile", "gulpfile", "gruntfile",
    "makefile", "package", "requirements", "reqs", "constraints", "pipfile", "gemfile",
    "podfile", "cargo", "dockerfile", "docker", "compose", "install", "bootstrap", "env",
    "ci", "noxfile", "jenkinsfile", "procfile", "manifest", "gradle", "pom", "wsgi", "asgi"))
# A review or refute file dated later than now plus this skew is a doubt.
FUTURE_SKEW_S = 300
_REVIEW_NAME = re.compile(r"review-(.+?)(-retry)?\.md", re.IGNORECASE)
# Any bracketed severity anywhere in a review, readable by next._FINDING_AT or not (bold,
# numbered, quoted, escaped, headings…).
_LOOSE_FINDING = re.compile(r"\\?\[\s*(blocker|major|minor)\s*\\?\]", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9]+")
# Content brakes that also scan test files outside FILES: (code hidden in a test path).
TEST_SCAN = ("security", "data")
_CATCH_ALL = re.compile(r"[*/\\.]*")
_PROBES = ("zz-atta-probe.txt", "zz/atta/probe/x.unlikely")


class _Doubt(Exception):
    """A signal is missing, ambiguous or failing: the LLM Reina decides."""


def rule_risk(job: dict) -> str | None:
    """The highest of the intake's risk, the routed risk and the effective risk; None when
    any of them holds a value that is not a risk level (never treated as low)."""
    intake, agents = job.get("intake") or {}, job.get("agents") or {}
    if not isinstance(intake, dict) or not isinstance(agents, dict):
        return None
    values = (intake.get("risk"), agents.get("risk"), job.get("risk_effective"))
    if any(v is not None and v not in risk.RISKS for v in values):
        return None
    return risk.highest(*values)


def evaluate(job: dict, job_dir: Path, round_dir: Path, worktree: Path | None, base: str,
             cfg: dict) -> tuple[list[str] | None, str | None]:
    """(signals, None) when the rule approves, else (None, reason). Spanish texts."""
    try:
        return _signals(job, Path(job_dir), Path(round_dir), worktree, base, cfg), None
    except _Doubt as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - any surprise goes to the LLM Reina (S3.1)
        return None, f"error inesperado al revisar las señales: {type(exc).__name__}: {exc}"


def _signals(job, job_dir, round_dir, worktree, base, cfg) -> list[str]:
    from . import next as nxt  # lazy: next imports this module
    level = rule_risk(job)
    if level != "low":
        raise _Doubt(f"el riesgo del job es {risk.RISK_ES.get(level, 'desconocido')}")
    rnd = round_dir.name
    signals = ["Riesgo: bajo (Exploradora, ruteo y Filtro)."]

    build = round_dir / nxt.BUILD_FILE
    if nxt._first_line(build) != "STATUS: DONE":
        raise _Doubt(f"{rnd}/build.md no existe o no dice STATUS: DONE")
    problems, open_items = nxt.check_build(nxt._read_text(build))
    if problems:
        raise _Doubt(f"{rnd}/build.md no cumple el contrato: {'; '.join(problems)}")
    if open_items:
        raise _Doubt(f"{rnd}/build.md tiene criterios abiertos (✗)")
    accepted = len(nxt._acceptance_items(nxt.build_section_map(
        nxt._read_text(build)).get("Acceptance", "")))
    signals.append(f"{rnd}/build.md cumple el contrato: {accepted} criterio"
                   f"{'' if accepted == 1 else 's'} ✓ y ninguno ✗.")

    if worktree is None:
        raise _Doubt("el job no tiene worktree")
    checks = round_dir / "checks.md"
    lines = nxt._read_text(checks).splitlines()
    if len(lines) < 2 or lines[0].strip() != "GATE: PASS" or not lines[1].startswith("COMMIT:"):
        raise _Doubt(f"{rnd}/checks.md no existe o no dice GATE: PASS con su COMMIT")
    gated = lines[1][len("COMMIT:"):].strip()
    head = nxt._git_read(worktree, "rev-parse", "--verify", "HEAD^{commit}")
    if not head or head != gated:
        raise _Doubt(f"el HEAD del worktree ({head or '?'}) no es el commit que pasó el Filtro "
                     f"en {rnd}/checks.md ({gated})")
    signals.append(f"El Filtro pasó en {rnd}/checks.md (GATE: PASS) sobre el HEAD {head[:12]}.")

    signals.append(_reviews(nxt, job, round_dir, nxt._mtime(checks) or 0))

    files = scope.parse_files(nxt._read_text(job_dir / nxt.SPEC_FILE))
    if not files:
        raise _Doubt("spec.md no tiene `FILES:` con rutas: no se puede comparar el alcance")
    vague = [f for f in files if _catch_all(f)]
    if vague:
        raise _Doubt(f"`FILES:` de spec.md nombra todo el repositorio ({scope._listed(vague)}): "
                     "no acota el alcance")
    rules = _brake_rules(cfg)
    try:
        base_sha = gate.resolve_base(Path(worktree), base, head)
        diff = risk.changes(worktree, base_sha, head)
    except (gate.GateError, OSError) as exc:
        raise _Doubt(f"no pude leer el diff: {exc}") from None
    if not diff:
        raise _Doubt("el diff está vacío")

    _brakes(diff, rules, files)
    signals.append("Sin frenos: el diff no toca migraciones, CI, build ni despliegue, ni datos, "
                   "seguridad, contratos, concurrencia, entrega o código sin test.")

    tests = risk.DEFAULT_RULES["tests"]
    changed = scope._live_paths([(ch["status"], tuple(ch["paths"])) for ch in diff])
    named = [p for p in changed if scope.matches(p, files)
             and not gate.matches_protected(p, tests)]
    if not named:
        raise _Doubt("el diff no cambia ninguna ruta de `FILES:` (sin contar tests)")
    outside = [p for p in changed if not scope.matches(p, files)
               and not gate.matches_protected(p, tests)]
    if outside:
        raise _Doubt(f"{scope.OUT_OF_FILES} {scope._listed(outside)}")
    signals.append(f"El diff ({len(changed)} archivo{'' if len(changed) == 1 else 's'}) queda "
                   "dentro de `FILES:` (fuera solo hay tests).")
    return signals


def _catch_all(entry: str) -> bool:
    """A FILES entry that names (almost) everything: `**`, `**/*`, `*/**`, `*`, `.`…"""
    if _CATCH_ALL.fullmatch(entry.strip()):
        return True
    return all(scope.matches(p, [entry]) for p in _PROBES)


def _brake_rules(cfg: dict) -> dict:
    """DEFAULT_RULES unioned with the project's `risk_rules`: a project adds brakes, never
    removes them. Test globs are always the defaults."""
    project = (cfg or {}).get("risk_rules")
    if project is not None and risk.rules_error(project):
        raise _Doubt("risk_rules no es válido: no se pueden revisar los frenos")
    project = project or {}
    default = risk.DEFAULT_RULES

    def union(*lists):
        out = []
        for values in lists:
            out += [v for v in values or [] if v not in out]
        return out

    cats = {}
    for cat in risk.CATEGORIES:
        specs = [s for s in (default["categories"].get(cat),
                             (project.get("categories") or {}).get(cat)) if s]
        if not specs:
            continue
        spec = {"risk": "high"}
        if cat != "untested":
            for key in ("paths", "added", "removed"):
                values = union(*(s.get(key) for s in specs))
                if values:
                    spec[key] = values
        cats[cat] = spec
    return {"source": union(default["source"], project.get("source")),
            "tests": list(default["tests"]), "categories": cats}


def _brakes(diff: list[dict], rules: dict, files: list[str]) -> None:
    """_Doubt naming the first brake the diff hits."""
    for ch in diff:
        for path in ch["paths"]:
            glob = next((g for g in HARD_BRAKES if gate.matches_protected(path, [g])), None)
            if glob:
                raise _Doubt(f"frenos de la Reina: el diff toca {path} (migraciones, CI, build "
                             "o despliegue)")
    for ch in diff:
        for path in ch["paths"]:
            if _kind(path) is None:
                raise _Doubt(f"el diff toca {path}, que no es código fuente, test ni "
                             "documentación simple: la regla solo aprueba esos archivos")
    for ch in diff:
        if not ch["status"].startswith("D") and not ch["added"] and not ch["removed"]:
            raise _Doubt(f"no pude leer el contenido de {ch['paths'][-1]} en el diff (binario, "
                         "atributo -diff o ruta no reconocida): no se pueden revisar los frenos")
    found, hits = risk.assess(diff, rules)
    if found["categories"]:
        cats = ", ".join(risk.CATEGORY_ES[c] for c in found["categories"])
        where = ", ".join(sorted({p for p, _c, _w in hits})[:scope.MAX_LISTED])
        raise _Doubt(f"frenos de la Reina: el diff toca {cats} ({where})")
    tests = rules["tests"]
    for ch in diff:
        path = ch["paths"][-1]
        if not gate.matches_protected(path, tests) or scope.matches(path, files):
            continue
        for cat in TEST_SCAN:
            for pattern in rules["categories"].get(cat, {}).get("added") or []:
                rx = re.compile(pattern)
                if any(rx.search(line) for line in ch["added"]):
                    raise _Doubt(f"frenos de la Reina: {path} es un test fuera de `FILES:` y "
                                 f"agrega código de {risk.CATEGORY_ES[cat]}")


def _kind(path: str) -> str | None:
    """'test', 'source' or 'doc' when the rule may approve a change to `path`, else None."""
    parts = path.replace("\\", "/").casefold().split("/")
    dirs = parts[:-1]
    if any(d in NOT_SOURCE_DIRS for d in dirs) or gate.matches_protected(path, NOT_SOURCE):
        return None
    if any(part.startswith(".") for part in parts):
        return None
    default = risk.DEFAULT_RULES
    if gate.matches_protected(path, default["tests"]):
        return "test"
    if gate.matches_protected(path, default["source"]):
        if BUILD_TOKENS.intersection(_TOKEN.findall(parts[-1])):
            return None
        return "source"
    if gate.matches_protected(path, DOC_GLOBS) and not gate.matches_protected(path, NOT_DOCS):
        return "doc"
    return None


def _not_future(nxt, path: Path) -> None:
    if (nxt._mtime(path) or 0) > time.time() + FUTURE_SKEW_S:
        raise _Doubt(f"{path.name} tiene fecha en el futuro: no se puede ordenar con el resto")


def _reviews(nxt, job: dict, round_dir: Path, after: float) -> str:
    """The reviews signal, or _Doubt: the latest review of every reviewer of the round (planned
    or not) is PASS, or CHANGES with only blocker/major findings, all refuted."""
    planned = (job.get("agents") or {}).get("reviewers")
    if not isinstance(planned, list) or not all(
            isinstance(s, dict) and isinstance(s.get("reviewer"), str) and s["reviewer"]
            for s in planned):
        raise _Doubt("los revisores planificados del job no son válidos")
    if not planned:
        raise _Doubt("el job no tiene revisores planificados")
    files: dict[str, dict[str, list[Path]]] = {}
    try:
        entries = sorted(round_dir.iterdir()) if round_dir.is_dir() else []
    except OSError:
        entries = []
    for path in entries:
        m = _REVIEW_NAME.fullmatch(path.name)
        if m is None or not path.is_file():
            continue
        _not_future(nxt, path)
        kind = "retry" if m.group(2) is not None else "primary"
        files.setdefault(m.group(1).casefold(), {"primary": [], "retry": []})[kind].append(path)
    for spec in planned:
        if spec["reviewer"].casefold() not in files:
            raise _Doubt(f"falta la revisión de {spec['reviewer']}")
    labels, judged = [], []
    for name in sorted(files):
        used = _judged_files(nxt, name, files[name])
        found = []
        for path in used:
            if (nxt._mtime(path) or 0) < after:
                raise _Doubt(f"la revisión de {name} es anterior al Filtro")
            verdict = nxt._review_ok(path)
            text = nxt._read_text(path)
            loose = [m.group(1).casefold() for m in _LOOSE_FINDING.finditer(text)]
            parsed = [m.group(1) for m in nxt._FINDING_AT.finditer(text)]
            if len(loose) != len(parsed):
                raise _Doubt(f"{path.name} tiene hallazgos que no se pueden leer")
            if verdict == "PASS":
                if any(sev in nxt.SERIOUS for sev in loose):
                    raise _Doubt(f"{path.name} dice PASS pero lista hallazgos blocker/major")
            else:
                if "minor" in loose:
                    raise _Doubt(f"la revisión de {name} tiene hallazgos menores sin resolver")
                serious = nxt._serious_findings(path)
                if not serious:
                    raise _Doubt(f"{path.name} dice CHANGES sin hallazgos legibles")
                found += serious
                judged.append((name, path, serious))
        if found:
            labels.append(f"{name} CHANGES refutado por el Refutador")
        else:
            labels.append(f"{name} PASS")
    if judged:
        findings: dict[str, list] = {}
        for name, _used, found in judged:
            findings.setdefault(name, []).extend(found)
        newest = max((nxt._mtime(used) or 0) for _name, used, _found in judged)
        outcome = None
        for file in (nxt.REFUTE_FILE, nxt.REFUTE_RETRY_FILE):
            path = round_dir / file
            if path.is_file():
                _not_future(nxt, path)
            if path.is_file() and (nxt._mtime(path) or 0) >= newest:
                outcome = nxt._refute_outcome(path, findings)
                if outcome is not None:
                    break
        if outcome != "REFUTED":
            refused = ", ".join(name for name, _u, _f in judged)
            raise _Doubt(f"hay hallazgos blocker/major de {refused} que el Refutador no refutó")
    return "Revisiones: " + "; ".join(labels) + "."


def _judged_files(nxt, name: str, found: dict) -> list[Path]:
    """The review files of one reviewer that count (see the module docstring)."""
    primaries, retries = found["primary"], found["retry"]
    if len(primaries) > 1 or len(retries) > 1:
        raise _Doubt(f"hay más de una revisión de {name} en la ronda")
    primary = primaries[0] if primaries else None
    retry = retries[0] if retries else None
    if retry is not None and nxt._review_ok(retry) is None:
        raise _Doubt(f"la revisión {retry.name} no cumple el formato")
    if primary is not None and nxt._review_ok(primary) is not None:
        return [primary] + ([retry] if retry is not None else [])
    if retry is not None:
        return [retry]
    raise _Doubt(f"la revisión de {name} ({primary.name}) no cumple el formato")


def decision_text(signals: list[str]) -> str:
    """decision.md in the Reina's format (first line APPROVE)."""
    why = "\n".join(f"- {s}" for s in signals)
    return f"APPROVE\n\n## Summary\n\n{SUMMARY}\n\n## Why\n\n{why}\n"
