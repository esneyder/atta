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
- **S7** Filtro (PLAN F1): `atta/checks.json` con lista ordenada de comandos y shell por SO; fail-fast (corta al primer fallo); se ejecuta la copia del checkout principal con el worktree como cwd; falla si el diff agrega, modifica, renombra o borra cualquier ruta bajo `protected_paths` (`atta/**`, `.claude/**`, `.pi/**`, `.github/workflows/**`), comparando sin distinguir mayúsculas; el `base_ref` se valida y nunca puede ocultar el diff; un worktree sucio falla; escribe `round-N/checks.md` con primera línea `GATE: PASS|FAIL` y `COMMIT: <sha>`.
- **S8** Router por riesgo (PLAN F1): lee `RISK` y `SURFACES` de `intake.md` y produce los reviewers/lentes del job desde `config.json`; riesgo o superficie desconocidos se rechazan.
- **S9** CLI `atta` (PLAN F1 / arquitectura §4.1): `python atta/bin/atta.py <comando>`; comandos mínimos de F1: `create`, `advance`, `gate`, `route`, `status`, `summary`.
- **S10** Voz de marca (PLAN §2): mensajes al humano en español, directos, firmados por rol (p. ej. «La Exploradora aceptó el issue #12 (riesgo bajo).», «Tres rondas sin pasar el filtro. Te lo dejamos a ti.»); tokens de contrato en inglés sin traducir.
- **S11** Diseño con la marca. Usuario: «analizar el dashboard para darle un toque de diseño para que use la nueva identidad de marca, usa la hormiga tanto en la terminal animada como en la parte visual del dashboard». Usar `brand/tokens.css`, logo/silueta de la hormiga (`brand/assets/Logos/`), estados: en curso → `chicamocha`, aprobado → `hoja`, necesita humano → `culona`, siempre con ícono o palabra.
- **S12** Velocidad. Usuario: «no pase lo que esta pasando ahora mismo con el flujo de kerni, es que es muy lento». Todo lo mecánico es script; la animación de terminal no agrega espera (no bloquea, se desactiva sin TTY o con `NO_COLOR`/`ATTA_NO_ANIM`).
- **S13** CI (PLAN F1): tests de la espina en `windows-latest`, `macos-latest`, `ubuntu-latest`.

## Tasks

| ID | Specs | Route | Estado | Commit |
|---|---|---|---|---|
| T1 | S11, S12 | worker diseño (`design/hormiguero/`) → elección del usuario | hecho (A elegida) | 31a29e8 |
| T2 | S1, S3, S4, S5, S6, S10 | worker núcleo (`atta/spine/`, `tests/`) | hecho (70 tests OK en Windows; Unix pendiente de CI T7) | 57303bd |
| T3 | S7, S1 | worker + verify independiente + corrección | hecho | 933213b |
| T4 | S8, S2 | worker + verify independiente + corrección | hecho | 043ed2e |
| T5 | S9, S10, S12 | worker CLI + term | hecho (57 tests; smoke manual) | ver commit feat(cli) |
| T6 | S11 | worker dashboard A + verify independiente + corrección | hecho | ver commit feat(board) |
| T7 | S13 | CI 3 SO + docs de uso | pendiente | — |

## Log

- **L1** (usuario): «continuar con la implementacion del plan que hemos definido, analizar el dashboard para darle un toque de diseno para que use la nueva identidad de marca, usa la orgamiza tanto en la terminal animada como en la parte visual del dashboard»
- **L2** Decisión técnica: layout `atta/bin/*.py` (entradas finas) + paquete `atta/spine/` (lógica) + `tests/` con `unittest` (stdlib). No hay dashboard previo que analizar (se eliminó con la reescritura propia, D6): T1 diseña uno nuevo.
- **L3** Interpretación: «orgamiza» = la hormiga de la marca.
- **L4** T2 verificado por el padre: `python -m unittest discover -s tests` → 70 OK (Windows, Python 3.11). Decisiones del worker aceptadas: el tope `max_total_rounds` se copia al crear el job; superar el tope solo permite `needs-human`; `needs-human → build` no reinicia ni suma `total_rounds`; `sync_attempts` suma al entrar a `merging`; claves desconocidas de config se conservan. Pendiente para T4: aplicar `max_rounds` por contador (hoy solo rige el tope global).
- **L5** Verify independiente de T3/T4 (Risk item 2): S2 y S8 cumplidos; S7 y S1 no: B1 ruta protegida con otra capitalización (`ATTA/checks.json`) pasa como archivo agregado y al mergear pisa `atta/checks.json` en Windows/macOS; B2 `base_ref` sin validar (`""`, `HEAD`, `--output=...`) oculta el diff; B3 shell por defecto `pwsh` no existe en Windows estándar. Advisories: A1 `refs/heads/main` aceptado como integration_branch; A2 agregar archivos bajo rutas protegidas pasa; A3 `git replace` engaña el diff; A4 worktree sucio pasa; A5 timeout no se respeta si un hijo en background mantiene stdout. Decisión del padre: corregir B1-B3 y A1-A5; S7 se endurece a «cualquier cambio, incluido agregar».
- **L6** Diseño T1: `design/hormiguero/` con `01-camino-de-hormigas`, `02-mesa-de-operacion`, `03-mapa-de-la-colonia` (direcciones del dashboard) y `04-terminal` + `terminal-frames.json` (contrato de frames para T5); sync en `design/system.md` y `design/system.html`. Verificado por el worker con capturas headless (claro, oscuro, 420 px), sin URLs externas, solo tokens de marca. Pendiente: elección del usuario.
- **L7** Usuario eligió: dashboard «A · Camino de hormigas» (`design/hormiguero/01-camino-de-hormigas.html`) para T6; terminal: «Siempre ATTA_THEME explícito» (paleta oscura salvo `ATTA_THEME=light`, sin autodetección) para T5. T1 cerrado (commit 31a29e8).
- **L8** Corrección de L5 aplicada (B1-B3, A1-A5) con 27 pruebas de bypass en `tests/test_gate_bypass.py`; 180 tests OK (Windows). Commits: T4 `043ed2e`, T3 `933213b`. Riesgo residual: tras un timeout con hijo en background el Filtro ya no espera, pero el proceso huérfano puede seguir vivo; `--end-of-options` requiere git reciente (probado 2.49). Slice: la rama supera ~400 líneas por commit; al publicar se puede revisar por commit.
- **L9** T5 verificado por el padre: `test_cli` + `test_term` OK; smoke: `create 12` → `12-agregar-exportacion-a-csv`, `check-pr-base main` → exit 2 con el mensaje exacto, `status` tabla en español. Decisiones aceptadas: legalidad antes que presupuesto en `advance`; `route` llena `job.intake`; `ATTA_NO_ANIM` = línea estática sin ANSI. Pendiente: probar la animación en terminales reales (Windows Terminal, macOS, Linux).
- **L10** (usuario) sobre el dashboard A con fixtures: «ya probe, funciona- me gusta el diseno».
- **L11** Verify independiente T6 (Risk item 2): S11 y S12 cumplidos; B1 bloqueante: una junction NTFS (`mklink /J`, sin privilegios) dentro de jobs o round-N hace que el tablero liste y lea `.md` de fuera del job (`is_symlink()` es False para junctions en Python 3.10/3.11). Menores: m1 hardlinks servidos, m2 `STAGE_ROLE[m.stage]` con `constructor` rompe la vista, m3 Host laxo, m4 páginas de error stdlib sin headers, m5 sin timeout de socket, m6 `--host 127.0.0.2` tratado como externo, m7 symlinks de archivo sin probar. Decisión: corregir B1 con confinamiento por `resolve().parent`, m1, m2, m3, m5, m6; m4 aceptado (contenido escapado).
- **L12** Corrección de L11 aplicada (B1 junctions/symlinks por confinamiento de ruta resuelta, m1 hardlinks, m2 mapas sin prototipo, m3 Host estricto, m5 timeout 10 s, m6 loopback 127/8); 275 tests OK, 3 skipped (symlinks sin privilegio en Windows, corren en CI POSIX). Aceptado: con `--host 127.0.0.2` ese mismo Host es válido.
