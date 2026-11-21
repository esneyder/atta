# F2: la colonia en Claude Code

Fase F2 de `docs/PLAN.md`: Atta como herramienta instalable, agentes de la colonia en Claude Code, comando `/atta`, guard de herramientas, integración con GitHub y merge a `develop`.
Rama: `feat/f2-colonia` (apilada sobre `docs/readme-guia`, PR #3). Base de diseño: `docs/design/arquitectura.md`, `odd/tasks/f1-espina.md`.

## Specs

- **S1** Instalación. Usuario eligió: «Herramienta instalada + config por repo (recomendado)»: «Atta se instala una vez (pipx/pip desde el repo de GitHub) y da el comando `atta` global. Cada proyecto solo guarda atta/config.json, checks.json y jobs/. Actualizar = un comando; el código de la fábrica no vive en tus repos.» Vista elegida: `pipx install git+https://github.com/esneyder/atta`, `atta --help`; en cada proyecto `atta/config.json`, `atta/checks.json`, `atta/jobs/ (ignorado)`, `.claude/agents/atta-*.md`, `.claude/skills/atta/SKILL.md`.
- **S2** `atta init` prepara un proyecto: crea `atta/config.json`, `atta/checks.json` (vacío: el Filtro falla hasta tener checks reales), entradas de `.gitignore`, los agentes y el skill de Claude Code, y verifica `develop` en `origin` y las labels; nunca sobrescribe archivos existentes sin `--force`.
- **S3** Integración con GitHub vía `gh`: cola de issues abiertos con label `atta`; `issue.md` con comentarios solo del autor y del operador; comentarios firmados por rol con marcador; labels `atta:needs-info` / `atta:needs-human`; worktree `atta/<id>` desde `origin/develop`; push solo a `atta/*`; PR solo contra `develop`.
- **S4** Merge. Usuario eligió: «Mergea en F2 (recomendado)»: «Ciclo completo desde F2: sincroniza con develop, Filtro otra vez y `gh pr merge --match-head-commit` solo contra develop.» Decisión previa D3: «si que haga merge, siempre a develop, main tiene prohibido desplegar».
- **S5** Modelos. Usuario eligió: «T1 Haiku · T2 Sonnet · T3 Opus»: «Opus solo donde el router pide T3 (Soldado en riesgo alto). El resto en Haiku/Sonnet.»
- **S6** Agentes de la colonia (Exploradora, Guía, Vigía, Cortadora, Jardineras, Soldado, Reina): cada uno lee archivos del job, escribe un solo artefacto con su token de primera línea y `## Summary` en español, y responde una línea. El texto del issue es dato, nunca instrucción. El revisor nunca usa el mismo modelo que la Cortadora de esa ronda cuando el tier lo permite.
- **S7** Comando `/atta` (skill de Claude Code) delgado: el estado y la decisión de qué sigue salen de la espina (`atta next`), no del LLM; lanza agentes en background, espera a todos los reviewers, respeta `concurrency`.
- **S8** Guard de herramientas en Python (hook PreToolUse de Claude Code): bloquea escrituras fuera del worktree del job y en `protected_paths`, `git push` fuera de `atta/*`, `gh pr merge` por agentes y comandos de despliegue.
- **S9** Prueba de punta a punta. Usuario eligió: «Crear esneyder/atta-sandbox privado»: «Creo un repo privado pequeño (un proyecto de juguete con tests) con develop y la label atta. Los PRs de prueba no ensucian ningún proyecto real.» Salida de F2 (PLAN): «un issue de prueba llega a PR contra `develop`» (y con S4, se mergea a `develop`).
- **S10** Multiplataforma (D2 «debe funcionar en windows, mac y linux»); velocidad (usuario: «no pase lo que esta pasando ahora mismo con el flujo de kerni, es que es muy lento»).
- **S11** Arreglo pendiente de F1: `board.py --help` muestra mal las tildes en un pipe de Windows («m�quina»).

## Tasks

| ID | Specs | Route | Estado | Commit |
|---|---|---|---|---|
| T1 | S1, S10, S11 | Empaquetado: paquete `atta` instalable, raíz del proyecto por `git`, entry points `atta` y `atta-board`, CI instala con pip | hecho (308 tests OK; job CI install sin correr aún) | ver commit build(package) |
| T2 | S2 | `atta init` | hecho (21 tests; falta conectar en cli.py) | ver commit feat(init) |
| T3 | S3, S4 | Adaptador GitHub (`gh`) + worktree + PR + merge seguro | pendiente | — |
| T4 | S7 | `atta next` / `atta queue` + skill `/atta` | pendiente | — |
| T5 | S8 | `atta guard` (hook PreToolUse) | pendiente | — |
| T6 | S5, S6 | Agentes de la colonia (plantillas) + mapeo de tiers | hecho (checker OK; unittest al integrar T1) | ver commit feat(templates) |
| T7 | S9 | Sandbox `esneyder/atta-sandbox` + corrida de punta a punta | pendiente | — |
| T8 | S1-S10 | README y docs de F2 | pendiente | — |

## Log

- **L1** (usuario): «continuemos con la fase del plan»
- **L2** Decisiones del usuario (ask_user_question): instalación «Herramienta instalada + config por repo (recomendado)»; repo de prueba «Crear esneyder/atta-sandbox privado»; modelos «T1 Haiku · T2 Sonnet · T3 Opus»; merge «Mergea en F2 (recomendado)».
- **L3** Orden: T1 primero (cambia imports y rutas de todo); luego T2-T6 en paralelo con superficies disjuntas; T7 al final; T8 con cada tarea.
- **L4** T6 plantillas listas (`atta/templates/claude/agents/atta-*.md`, `models.json`, `check_templates.py` → «OK: 10 plantillas válidas.»). Requisitos para otras tareas: T2 `atta init` debe resolver `{{model:Tn}}` antes de copiar; T4 debe pasar el tier del router al lanzar reviewers (Soldado T3 en riesgo alto) y parsear la respuesta de la Exploradora entre `<<<ATTA-ARTIFACT`/`ATTA-ARTIFACT>>>`; T5 debe permitir a cada agente escribir solo su artefacto en `atta/jobs/<id>/` aunque `atta/**` esté protegido; ubicación de `feedback-*.md` por definir (T4). La regla «revisor ≠ modelo de la Cortadora» la aplica el router (F4), no el prompt.
- **L5** T1 verificado por el padre: suite 308 OK (3 skipped); `pip install` probado por el worker en un target temporal (atta.exe/atta-board.exe, «máquina» OK). Orden de raíz: `--project` → `ATTA_PROJECT` → `git rev-parse --show-toplevel` → cwd. Pendiente: correr el job `install` del CI; README desactualizado (T8). Contrato para T2-T5: cada módulo nuevo expone `register(subparsers)`; el padre los conecta en `cli.py`.
- **L6** Contrato compartido para T2-T5 (decisión técnica del padre). Cada módulo nuevo expone `register(subparsers)` y no edita `cli.py`; el padre los conecta. Comandos:
  - T3 (`atta/spine/github.py`, `atta/spine/flow.py`): `atta queue` (filas `new|resume|wait|hold|skip` con issue, job, etapa, motivo; respeta `concurrency`), `atta start <issue>` (lee el issue con `gh`, crea el job e `issue.md` con comentarios solo del autor y del operador), `atta worktree <job>` (`git fetch` + `git worktree add -b atta/<id> <root>/.worktrees/<id> origin/develop`), `atta publish <job>` (check-push, push de `atta/<id>`, PR draft `--base develop` si no existe, guarda `pr`/`pr_url`), `atta comment <job> --role <rol> --file F` (comentario en issue o PR con marcador `<!-- atta -->` y firma del rol), `atta label <job> add|remove <nombre>`, `atta merge <job>` (exige etapa `approve` y `decision.md` = `APPROVE`; merge de `origin/develop` en el worktree, Filtro `merge-checks`, push, verifica base `develop` con `gh pr view`, `gh pr merge --match-head-commit <sha>`; cualquier fallo → `needs-human` con motivo). `gh` inyectable para tests (variable `ATTA_GH` con la ruta del ejecutable).
  - T4 (`atta/spine/next.py`): `atta next <job>` imprime una acción JSON: `{"action":"spawn","agent":"atta-<rol>","model":"haiku|sonnet|opus","prompt":"..."}`, `{"action":"spawn-group","agents":[...]}` (reviewers en paralelo), `{"action":"run","argv":["atta",...]}`, `{"action":"wait","reason":"..."}` o `{"action":"done","stage":"..."}`; y `atta save-artifact <job> intake --from-reply <archivo>` (extrae lo que hay entre `<<<ATTA-ARTIFACT` y `ATTA-ARTIFACT>>>`). Skill en `atta/templates/claude/skills/atta/SKILL.md`. `feedback-*.md` vive en la carpeta del job.
  - T5 (`atta/spine/guard.py`): `atta guard` lee el JSON PreToolUse de Claude Code por stdin; permite escribir solo dentro del worktree de un job activo (fuera de `protected_paths`) y solo los artefactos del job en `atta/jobs/<id>/`; bloquea `git push` fuera de `atta/*`, `gh pr merge`, despliegues y borrar ramas. Plantilla `atta/templates/claude/settings.json` con el hook.
  - T2 (`atta/spine/initcmd.py`): `atta init [--force] [--no-github]` copia plantillas resolviendo `{{model:Tn}}` con `models.json`, instala el skill, fusiona `settings.json` (sin borrar lo del usuario), crea `atta/config.json` y `atta/checks.json` si faltan, agrega `/atta/jobs/` y `/.worktrees/` al `.gitignore`, verifica `origin/develop` y crea las labels con `gh`.
- **L7** CI run 44849180995 verde: 12/12 (spine tests + pip install en ubuntu/macos/windows × py3.10/3.12). T1 cerrado con evidencia de instalación real.
