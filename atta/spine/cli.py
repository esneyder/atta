"""CLI `atta` (arquitectura §4.1): the `atta` console script, or `python atta/bin/atta.py <comando>`.

It works on the project resolved by spine/project.py (`--project`, $ATTA_PROJECT, git top level
or cwd): config and checks from `<root>/atta/`, jobs in `<root>/atta/jobs` unless `--jobs-dir`.
F1 commands are local and deterministic (no GitHub, no network). Exit codes:
0 ok, 1 negative verdict (the Filtro did not pass), 2 refused or usage error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import (branches, config, events, gate, intake, issue, machine, project, router, store,
               term, voice)
from .ids import InvalidId
from .lock import LockTimeout, job_lock
from .timeutil import from_iso, utcnow

INTAKE_FILE = "intake.md"
OK, NEGATIVE, REFUSED = 0, 1, 2

# Counter whose budget (max_rounds) is checked before entering `to` from `frm`.
_BUDGET = {("gate", "build"): "gate_rounds", ("review", "build"): "review_rounds"}
# Transitions whose voice line needs a PR number.
_NEEDS_PR = {("approve", "merging"), ("merging", "merged")}


class Refusal(Exception):
    """A refused command: message to stderr, exit 2."""


def _err(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------- commands

def cmd_create(args, cfg, jobs: store.JobStore) -> int:
    body = None
    if args.body_file:
        path = Path(args.body_file)
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise Refusal(f"No se pudo leer {path}: {exc.strerror or exc}.") from exc
    job = jobs.create(args.issue, args.title, None, cfg)
    issue.write_issue_md(jobs.job_dir(job["id"]), args.issue, args.title, body)
    print(job["id"])
    return OK


def _voice_line(new: dict, frm: str, to: str, cfg: dict) -> str:
    if (frm, to) in _NEEDS_PR and new.get("pr") is None:
        return f"{new['id']}: {frm} → {to}"
    try:
        line = voice.for_transition(new, frm, to, cfg["integration_branch"])
    except (KeyError, TypeError, ValueError):
        line = None
    return line or f"{new['id']}: {frm} → {to}"


def cmd_advance(args, cfg, jobs: store.JobStore) -> int:
    job = jobs.load(args.job)
    frm, to = job["stage"], args.stage
    counter = "sync_attempts" if to == "merging" else _BUDGET.get((frm, to))
    if counter:
        machine.check(job, to)
        try:
            branches.assert_round_budget(job, counter, cfg)
        except branches.RoundBudgetExhausted as exc:
            raise Refusal(str(exc)) from None
    new = machine.advance(jobs, args.job, to)
    print(_voice_line(new, frm, to, cfg))
    return OK


def cmd_route(args, cfg, jobs: store.JobStore) -> int:
    jid = jobs.load(args.job)["id"]
    directory = jobs.job_dir(jid)
    header = intake.parse_file(directory / INTAKE_FILE)
    plan = router.plan_for_intake(header, cfg)
    with job_lock(directory):
        job = jobs.load(jid)
        job["intake"] = {"risk": header["risk"], "surfaces": list(header["surfaces"]),
                         "decision": header["decision"],
                         "blocked_by": list(header["blocked_by"]),
                         "migration": header["migration"]}
        job["agents"] = plan
        jobs.save(job)
    reviewers = ", ".join(f"{r['reviewer']} {r['tier']}" for r in plan["reviewers"])
    count = len(plan["reviewers"])
    print(f"El router asignó {count} reviewer{'' if count == 1 else 's'} a {jid} "
          f"(riesgo {voice.RISK_ES[plan['risk']]}, spec-review {'sí' if plan['spec_review'] else 'no'}): "
          f"{reviewers}.")
    return OK


def cmd_gate(args, cfg, jobs: store.JobStore) -> int:
    job = jobs.load(args.job)
    round_dir = jobs.job_dir(job["id"]) / f"round-{job['total_rounds'] + 1}"
    with term.ant("Filtro", "corriendo los checks"):
        try:
            result = gate.run_gate(args.root, Path(args.worktree), job, round_dir,
                                   base_ref=args.base, config=cfg, kind=args.kind)
        except gate.GateError as exc:
            error = str(exc)
        else:
            error = None
    if error is not None:
        raise Refusal(error)
    print(result.summary)
    return OK if result.passed else NEGATIVE


def cmd_check_pr_base(args, cfg, jobs) -> int:
    try:
        branches.check_pr_base(args.base, cfg)
    except branches.BranchRefused as exc:
        raise Refusal(str(exc)) from None
    print(f"La Reina acepta la base {args.base}.")
    return OK


def cmd_check_push(args, cfg, jobs) -> int:
    try:
        branches.check_push_target(args.ref, cfg)
    except branches.BranchRefused as exc:
        raise Refusal(str(exc)) from None
    print(f"Atta puede empujar a {args.ref}.")
    return OK


# ---------------------------------------------------------------- status and summary

def _elapsed(job: dict, now) -> str:
    end = from_iso(job["ended"]) if job.get("ended") else now
    secs = max(int((end - from_iso(job["started"])).total_seconds()), 0)
    if secs < 60:
        return f"{secs} s"
    if secs < 3600:
        return f"{secs // 60} min {secs % 60} s"
    return f"{secs // 3600} h {secs % 3600 // 60} min"


def _cost(value) -> str:
    return "-" if value is None else f"US$ {value:.2f}"


def _models_used(job_dir) -> str:
    """Models recorded in events.jsonl (T12), in order of first use; `-` when none."""
    try:
        evs = events.read_events(job_dir)
    except (OSError, ValueError):
        return "-"
    seen: list[str] = []
    for ev in evs:
        found = [ev.get("model")] + [m.get("model") for m in ev.get("models") or []
                                     if isinstance(m, dict)]
        seen += [m for m in found if isinstance(m, str) and m and m not in seen]
    return ", ".join(seen) or "-"


def _table(jobs_list: list[dict], models: dict | None = None) -> list[str]:
    """The jobs table; with `models` ({job id: text}) it adds the MODELOS column."""
    now = utcnow()
    header = ("JOB", "ISSUE", "ETAPA", "RONDAS", "COSTO", "TIEMPO")
    rows = [header + (("MODELOS",) if models is not None else ())]
    for j in jobs_list:
        row = (j["id"], f"#{j['issue']}", j["stage"],
               f"{j['total_rounds']}/{j['max_total_rounds']}", _cost(j.get("cost_usd")),
               _elapsed(j, now))
        rows.append(row + ((models.get(j["id"], "-"),) if models is not None else ()))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]


def _all(jobs: store.JobStore) -> list[dict]:
    return [jobs.load(jid) for jid in jobs.list_ids()]


def cmd_status(args, cfg, jobs: store.JobStore) -> int:
    listed = [jobs.load(args.job)] if args.job else _all(jobs)
    if not listed:
        print("No hay jobs.")
        return OK
    models = None
    if getattr(args, "models", False):
        models = {j["id"]: _models_used(jobs.job_dir(j["id"])) for j in listed}
    print("\n".join(_table(listed, models)))
    return OK


def cmd_summary(args, cfg, jobs: store.JobStore) -> int:
    listed = _all(jobs)
    if not listed:
        print("No hay jobs.")
        return OK
    running = sum(1 for j in listed if j["stage"] not in machine.TERMINAL)
    merged = sum(1 for j in listed if j["stage"] == "merged")
    rejected = sum(1 for j in listed if j["stage"] == "rejected")
    costs = [j["cost_usd"] for j in listed if j.get("cost_usd") is not None]
    total = _cost(round(sum(costs), 6) if costs else None)
    noun = "job" if len(listed) == 1 else "jobs"
    lines = _table(listed)
    lines.append(f"Total: {len(listed)} {noun} · {running} en curso · {merged} merged · "
                 f"{rejected} rejected · costo {total}")
    print("\n".join(lines))
    return OK


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atta",
        description="Atta, tu colonia de agentes: espina determinista y flujo con GitHub (init, "
                    "doctor, queue, start, refresh, worktree, next, model-failed, publish, comment, "
                    "label, guard, models, profiles, settings, memory y merge seguro a develop).",
        epilog="Códigos de salida: 0 ok, 1 veredicto negativo, 2 rechazado o error de uso.",
    )
    p.add_argument("--project", metavar="<carpeta>",
                   help=f"raíz del proyecto (por defecto: ${project.ENV_VAR}, la raíz git de la "
                        "carpeta actual o la carpeta actual)")
    p.add_argument("--jobs-dir",
                   help="carpeta de los jobs (por defecto: atta/jobs del proyecto)")
    sub = p.add_subparsers(dest="command", metavar="<comando>", required=True)

    c = sub.add_parser("create", help="Crea un job para un issue e imprime su id.",
                       description="Crea un job para un issue e imprime su id.")
    c.add_argument("issue", type=int, help="número del issue")
    c.add_argument("--title", required=True, help="título del issue (se guarda citado)")
    c.add_argument("--body-file", help="archivo UTF-8 con el cuerpo del issue")
    c.set_defaults(func=cmd_create)

    a = sub.add_parser("advance", help="Mueve un job a otra etapa (transición legal).",
                       description="Mueve un job a otra etapa; respeta max_rounds y el tope global. "
                                   "needs-human → gate solo vale cuando el Filtro se detuvo "
                                   "por una falla de configuración (no gasta ronda). La "
                                   "tercera REVISE de la Vigía no vuelve a spec: va a "
                                   "needs-human.")
    a.add_argument("job", help="id del job")
    a.add_argument("stage", help="etapa destino: " + ", ".join(machine.STAGES))
    a.set_defaults(func=cmd_advance)

    r = sub.add_parser("route", help="Lee intake.md y guarda los reviewers del job.",
                       description="Lee RISK y SURFACES de intake.md y guarda el plan del router "
                                   "(en riesgo alto suma al Rastreador); guarda también "
                                   "BLOCKED-BY y MIGRATION, y la cola espera a los bloqueantes. "
                                   "Después, si las reglas "
                                   "sobre el diff suben el riesgo, el Filtro puede volver a "
                                   "planear los reviewers con el riesgo efectivo.")
    r.add_argument("job", help="id del job")
    r.set_defaults(func=cmd_route)

    g = sub.add_parser("gate", help="Corre el Filtro sobre el worktree del job.",
                       description="Corre el Filtro sobre el worktree con atta/checks.json y "
                                   "sgconfig.yml del checkout principal (que debe estar en "
                                   "develop). Primero la configuración: sin checks, sin shell o "
                                   "sin ast-grep falla con KIND: config (lo corrige una persona, "
                                   "no gasta ronda). Luego avisa si el checkout está atrás de "
                                   "origin (frescura), compara el diff con FILES: del spec "
                                   "(alcance; bloquea con scope.enforce), corre ast-grep sobre "
                                   "los archivos del diff (solo severity: error bloquea) y los "
                                   "checks en orden; los checks con when risk>=medium se saltean "
                                   "en riesgo bajo. Si pasa, sube el riesgo cuando las reglas "
                                   "sobre el diff lo piden (nunca lo baja) y entonces vuelve a "
                                   "planear los reviewers. Frescura, alcance y riesgo solo "
                                   "corren con --kind checks. Sale "
                                   "0 si pasa, 1 si no pasa (checks.md dice KIND: config o "
                                   "code), 2 si el Filtro está mal configurado (base, "
                                   "protected_paths o risk_rules inválidos).")
    g.add_argument("job", help="id del job")
    g.add_argument("--worktree", required=True, help="ruta del worktree del job")
    g.add_argument("--base", required=True, help="ref base del diff (p. ej. origin/develop)")
    g.add_argument("--kind", choices=gate.KINDS, default="checks",
                   help="checks (por defecto) o merge-checks")
    g.set_defaults(func=cmd_gate)

    s = sub.add_parser("status", help="Tabla de jobs (o de un job).",
                       description="Tabla de jobs: id, issue, etapa, rondas, costo y tiempo. "
                                   "Con --models agrega los modelos usados (events.jsonl).")
    s.add_argument("job", nargs="?", help="id del job (opcional)")
    s.add_argument("--models", action="store_true",
                   help="agrega la columna MODELOS: los modelos que corrieron en cada job")
    s.set_defaults(func=cmd_status)

    m = sub.add_parser("summary", help="Tabla de todos los jobs con totales.",
                       description="Tabla de todos los jobs con totales.")
    m.set_defaults(func=cmd_summary)

    b = sub.add_parser("check-pr-base", help="Verifica la base de un PR (solo develop).",
                       description="Sale 0 si la base es la rama de integración; 2 si no.")
    b.add_argument("base", help="rama base del PR")
    b.set_defaults(func=cmd_check_pr_base)

    u = sub.add_parser("check-push", help="Verifica que una rama se pueda empujar.",
                       description="Sale 0 solo para ramas atta/<job-id>; 2 si no.")
    u.add_argument("ref", help="rama o ref destino del push")
    u.set_defaults(func=cmd_check_push)

    # F2 commands live in their own modules; imported here to keep startup cheap
    # for the F1 commands and to avoid import cycles.
    from . import (doctor, fallback, flow, guard, initcmd, memory, models, next as next_cmd,
                   profiles, settings)
    initcmd.register(sub)
    doctor.register(sub)
    next_cmd.register(sub)
    fallback.register(sub)
    flow.register(sub)
    guard.register(sub)
    models.register(sub)
    profiles.register(sub)
    settings.register(sub)
    memory.register(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    project.utf8_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else REFUSED
    try:
        args.root = project.find_root(args.project)
        try:
            cfg = project.load_config(args.root)
        except config.ConfigError:
            # `atta doctor` reports an invalid config as a ✗ line instead of refusing.
            if not getattr(args, "tolerates_bad_config", False):
                raise
            cfg = None
        jobs_dir = args.jobs_dir or project.jobs_dir(args.root)
        return args.func(args, cfg, store.JobStore(jobs_dir))
    except (Refusal, project.ProjectError, config.ConfigError, store.StoreError, InvalidId, machine.Refused,
            intake.IntakeError, router.RoutingError, branches.BranchRefused, LockTimeout) as exc:
        _err(str(exc))
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
