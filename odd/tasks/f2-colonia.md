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
| T1 | S1, S10, S11 | Empaquetado: paquete `atta` instalable, raíz del proyecto por `git` (config/jobs/checks del proyecto, assets del paquete), entry points `atta` y `atta-board`, CI instala con pip | pendiente | — |
| T2 | S2 | `atta init` | pendiente | — |
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
