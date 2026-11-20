# F1: espina propia, identidad y ramas + diseño del hormiguero

Feature F1 de `docs/PLAN.md` más el diseño visual con la marca (dashboard y terminal animada con la hormiga).
Rama: `feat/f1-espina` (desde `develop`). Diseño de referencia: `docs/design/arquitectura.md` §3, §4, §7, §12, §13.

## Specs

- **S1** Multiplataforma. Usuario: «debe funcionar en windows, mac y linux». Python 3.10+ solo stdlib; scripts invocados como `python atta/bin/<script>.py`; lock portable (msvcrt/fcntl tras una función, lock por job, nunca global); `pathlib`; nada Unix-only (sin `lsof`, `bash` obligatorio, `rm`, `sed`).
- **S2** Política de ramas. Usuario: «si que haga merge, siempre a develop, main tiene prohibido desplegar». `config.json` con `integration_branch: "develop"` y `forbidden_branches: ["main", "master"]`; la espina rechaza una config que integre en una rama prohibida; ramas de trabajo `atta/<job-id>` desde `origin/develop`; una verificación de PR rechaza base distinta de `develop` (un PR con base `main` es rechazado).
- **S3** Máquina de estados (PLAN F1): etapas `intake`, `needs-info`, `spec`, `spec-review`, `build`, `gate`, `review`, `approve`, `merging`, `needs-human`, `merged`, `rejected`; transiciones legales explícitas; una transición ilegal se rechaza sin modificar `job.json` ni `events.jsonl`.
- **S4** Job store (PLAN F1): `atta/jobs/<id>/job.json` con un único escritor (la espina); escritura atómica; contadores separados `review_rounds`, `gate_rounds`, `sync_attempts` y tope global `max_total_rounds` que el feedback humano no reinicia (K13).
- **S5** `events.jsonl` (PLAN F1): una línea JSON por transición con timestamp, etapa origen/destino, duración de la etapa, y campos opcionales de modelo/tokens/costo; un precio faltante produce costo `null` y un aviso, nunca un error.
- **S6** Issue como dato (PLAN F1): título y cuerpo del issue citados en `issue.md`; el título nunca entra sin citar; ids de job sanitizados antes de usarse en rutas.
- **S7** Filtro (PLAN F1): `atta/checks.json` con lista ordenada de comandos y shell por SO; fail-fast (corta al primer fallo); se ejecuta la copia del checkout principal con el worktree como cwd; falla si el diff modifica o borra `protected_paths` (`atta/**`, `.claude/**`, `.pi/**`, `.github/workflows/**`); escribe `round-N/checks.md` con primera línea `GATE: PASS|FAIL` y `COMMIT: <sha>`.
- **S8** Router por riesgo (PLAN F1): lee `RISK` y `SURFACES` de `intake.md` y produce los reviewers/lentes del job desde `config.json`; riesgo o superficie desconocidos se rechazan.
- **S9** CLI `atta` (PLAN F1 / arquitectura §4.1): `python atta/bin/atta.py <comando>`; comandos mínimos de F1: `create`, `advance`, `gate`, `route`, `status`, `summary`.
- **S10** Voz de marca (PLAN §2): mensajes al humano en español, directos, firmados por rol (p. ej. «La Exploradora aceptó el issue #12 (riesgo bajo).», «Tres rondas sin pasar el filtro. Te lo dejamos a ti.»); tokens de contrato en inglés sin traducir.
- **S11** Diseño con la marca. Usuario: «analizar el dashboard para darle un toque de diseño para que use la nueva identidad de marca, usa la hormiga tanto en la terminal animada como en la parte visual del dashboard». Usar `brand/tokens.css`, logo/silueta de la hormiga (`brand/assets/Logos/`), estados: en curso → `chicamocha`, aprobado → `hoja`, necesita humano → `culona`, siempre con ícono o palabra.
- **S12** Velocidad. Usuario: «no pase lo que esta pasando ahora mismo con el flujo de kerni, es que es muy lento». Todo lo mecánico es script; la animación de terminal no agrega espera (no bloquea, se desactiva sin TTY o con `NO_COLOR`/`ATTA_NO_ANIM`).
- **S13** CI (PLAN F1): tests de la espina en `windows-latest`, `macos-latest`, `ubuntu-latest`.

## Tasks

| ID | Specs | Route | Estado | Commit |
|---|---|---|---|---|
| T1 | S11, S12 | worker diseño (`design/hormiguero/`) → canvas → elección del usuario | en curso | — |
| T2 | S1, S3, S4, S5, S6, S10 | worker núcleo (`atta/spine/`, `tests/`) | hecho (70 tests OK en Windows; Unix pendiente de CI T7) | ver Log L4 |
| T3 | S7, S1 | Filtro | pendiente | — |
| T4 | S8, S2 | router + política de ramas | pendiente | — |
| T5 | S9, S10, S12 | CLI + animación de la hormiga en terminal (según dirección elegida en T1) | pendiente | — |
| T6 | S11 | dashboard implementado (`atta/bin/board.py` + vista) según dirección elegida | pendiente | — |
| T7 | S13 | CI 3 SO + docs de uso | pendiente | — |

## Log

- **L1** (usuario): «continuar con la implementacion del plan que hemos definido, analizar el dashboard para darle un toque de diseno para que use la nueva identidad de marca, usa la orgamiza tanto en la terminal animada como en la parte visual del dashboard»
- **L2** Decisión técnica: layout `atta/bin/*.py` (entradas finas) + paquete `atta/spine/` (lógica) + `tests/` con `unittest` (stdlib). No hay dashboard previo que analizar (se eliminó con la reescritura propia, D6): T1 diseña uno nuevo.
- **L3** Interpretación: «orgamiza» = la hormiga de la marca.
- **L4** T2 verificado por el padre: `python -m unittest discover -s tests` → 70 OK (Windows, Python 3.11). Decisiones del worker aceptadas: el tope `max_total_rounds` se copia al crear el job; superar el tope solo permite `needs-human`; `needs-human → build` no reinicia ni suma `total_rounds`; `sync_attempts` suma al entrar a `merging`; claves desconocidas de config se conservan. Pendiente para T4: aplicar `max_rounds` por contador (hoy solo rige el tope global).
