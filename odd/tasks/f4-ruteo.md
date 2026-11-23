# F4: ruteo y velocidad

Fase F4 de `docs/PLAN.md` §6 (y G7, G8 de §6.1; K5 y K10 de `docs/design/arquitectura.md` §8). Bajar tiempo y costo por job sin perder calidad donde un error es caro.
Rama: `feat/f4-ruteo` (desde `develop`, que ya tiene F3). Mapa del código: L2.

## Specs

- **S1** Objetivo. Fila F4 del plan, textual: «Router para todas las etapas (tiers T0-T3, matriz etapa×riesgo, escalera por intento, independencia de modelo), lentes por riesgo, Reina determinista en `low`, Vigía en medio/alto (K10), dependencias `BLOCKED-BY` en la cola (K5), timeouts por etapa; **cadenas de fallback de modelos** en `atta models` (G7); orquestador de `/atta` en Haiku (G8)». Salida verificable: «Job `low` sin LLM en la Reina; tiempos por etapa en el dashboard».
- **S2** Principio vigente (F3 L9), usuario: «en los pasos en donde se requiere un modelo potente para garantizar calidad, se debe usar». Barato donde el error es barato, potente donde un error es caro.
- **S3** Reina determinista en riesgo bajo. Decisión del usuario: «Regla determinista, sin LLM (Recomendado)» y, tras la propuesta con condiciones, «si dale, dejemoslo asi». Condiciones aceptadas:
  1. Ante cualquier duda, Reina LLM: si una señal falta o es ambigua, la regla no aprueba y deriva a la Reina LLM (no escala a un humano por algo que un LLM resolvería).
  2. Mismo formato de salida: escribe `decision.md` igual que la Reina LLM, así merge y recuperación de handoffs no cambian.
  3. Queda registrado: un evento en `events.jsonl` dice que decidió la regla y no un modelo.
  4. Mantiene los frenos de la Reina actual (migraciones, CI, etc. → escala).
  Señales que la regla verifica: último `checks.md` con `GATE: PASS`; todas las últimas reviews `PASS` (o `CHANGES` refutado); `build.md` válido con criterios ✓ y sin ✗; diff dentro de `FILES:`; riesgo efectivo `low`. La entrega sigue decidida por `auto_merge_up_to`.
- **S4** Proveedores. Decisión del usuario: «Solo Claude por ahora (Recomendado)»: las cadenas de fallback (G7) son entre modelos Claude; otros proveedores llegan en F5 con Pi. Fallback solo por cuota, rate limit o red, no por contexto; el cambio queda en `events.jsonl`. Decisiones del usuario (L4): cadenas «Por tier (Recomendado)»; costo «Nunca más caro (Recomendado)»: la cadena solo acepta modelos de igual o menor costo y, agotada, el job no sube de modelo.
- **S5** Orquestador `/atta` en Haiku (G8): el comando corre en un modelo barato, porque `atta next` ya decide el pipeline.
- **S6** Timeouts por etapa: `atta next` detecta un agente pendiente que superó el límite de su etapa y lo reporta (reintento o `needs-human`), sin colgar el job (F2 L20: intake de 1359 s por una corrida trabada).
- **S7** `BLOCKED-BY` en la cola (K5): un job cuyo bloqueante no está mergeado espera; dependientes de un job rechazado no avanzan.
- **S8** Router para todas las etapas: matriz etapa×riesgo, escalera por intento (la Cortadora escala desde la ronda 2, como dice F3 L9), independencia de modelo (los reviewers evitan el modelo de la Cortadora, F2 L8).
- **S9** Regla vigente: multiplataforma (D2), Atta solo integra en `develop` (D3), configuración solo por humanos (L29); un cambio del routing por defecto avisa a proyectos existentes vía `atta doctor` (F3 L18).

## Tasks

| ID | Specs | Route | Estado | Commit |
|---|---|---|---|---|
| T1 | S3, S2 | Reina determinista en riesgo bajo (`next._approve`, `decision.md`, evento) | hecho (2 rondas de verificación, L5-L6; 1063 OK) | ver git log |
| T2 | S5 | `/atta` en Haiku (`model:` en el comando, configurable por tier) | hecho (comando y skill en T0; `models set T0` los sincroniza) | 66c2223 |
| T3 | S8, S2 | Matriz etapa×riesgo, escalera desde ronda 2, reviewers evitan el modelo de la Cortadora | pendiente | — |
| T4 | S4 | Cadenas de fallback Claude en `atta models` + evento | en curso: parte 1 (config, CLI, guard) sin commitear | — |
| T5 | S6 | Timeouts por etapa en `atta next` | pendiente | — |
| T6 | S7 | `BLOCKED-BY` en la cola (persistir en `route`, esperar en `queue`/`next`) | hecho en `route`/`queue`/`start` (1001 tests); falta cablear `deps.check` en `next` (con T3) | 9ffd929 |
| T7 | S1, S9 | Docs, aviso de doctor, corrida en el sandbox | pendiente | — |

## Log

- **L1** (usuario) «si dale, armalo y continuenos con la implementacion». Decisiones previas del usuario: Reina en bajo «Regla determinista, sin LLM (Recomendado)»; proveedores «Solo Claude por ahora (Recomendado)»; consulta «que la reina sea riesgo bajo si es buena idea ?» → propuesta con cuatro condiciones → «si dale, dejemoslo asi».
- **L2** Mapa (explorador): `models.Table.resolve` (`atta/spine/models.py:175`) es el único resolvedor; el router (`router.py:53`) solo planifica reviewers y `spec_review`; Vigía ya depende del riesgo (`config.py:14-22`, K10 hecho salvo su modelo). Cortadora escala con `total_rounds >= 2` (`next.py:532`), o sea desde la ronda 3, contra F3 L9 «desde la ronda 2». Reina: `next.py:730-741` (`_approve`), merge en `flow.py:1100` lee la primera línea de `decision.md` (`flow.py:576`). `BLOCKED-BY` ya se parsea (`intake.py:44-49`) pero `cmd_route` lo descarta (`cli.py:86`). Duraciones por etapa ya existen (`duration_s`, board); no hay timeout por etapa ni fallback de modelos. Comando y skill `/atta` sin `model:`; la doc de Claude Code acepta `model` en el frontmatter de comandos y skills.
- **L3** T2 hecho (66c2223). T6 hecho (9ffd929): `wait` «espera a #n», `hold` si el bloqueante fue rechazado o hay ciclo; un job bloqueado no ocupa cupo de `concurrency` (si no, con límite 1 un bloqueo sin job nunca arrancaría). API `deps.check(job, deps.by_issue(jobs), closed)` para `next`. T1 implementado (21 tests, 1001 OK): criterio conservador, un `CHANGES` con solo menores y evidencia vieja (reviews anteriores a `checks.md`, `COMMIT` ≠ HEAD) cuentan como duda → Reina LLM (S3 condición 1); en verificación independiente (Risk item 2).
- **L4** (usuario) T4: PLAN G7 dice cadenas «por rol», arquitectura §4 «por tier» y «Nunca hay fallback silencioso a un tier más caro». Respuestas: «Por tier (Recomendado)» y «Nunca más caro (Recomendado)». Mapa (explorador): hoy nada detecta cuota/rate limit/red; `models.config_error` (`models.py:98`) rechaza claves fuera de `tiers`/`roles`; guard bloquea `models set|unset|reset` (`guard.py:253`); hook propuesto: comando del spine que el skill `/atta` llama cuando el Agent tool falla por cuota/rate limit/red, que registra `model_fallback` y hace que `next` resuelva el siguiente modelo de la cadena. Suite: 1028 OK con T1 sin commitear.
- **L5** Verificación independiente de T1: S3 cond. 2 y 3 y S2 cumplen; cond. 1 y 4 no (aprobaciones falsas por regla reproducidas): B1 PASS que lista blocker/major; B2 un `-retry` tapaba un primario válido; B3 hallazgo ilegible (`- [blocker]  x`) contado como refutado; B4 frenos de contenido salteados cuando el patch no tiene líneas (ruta con espacio, `-diff`); B5 dependencias/build/config en la allowlist (`requirements/`, `build.rs`, `gulpfile.js`, `fabfile.py`, `ci/`, `settings.py`; también `CLAUDE.md`/`AGENTS.md`). Corregido en `decide.py` con 9 tests nuevos (RED observado, 170 OK en decide+next). Decisión propia: un PASS con notas `[minor]` sigue aprobando (el template lo permite); un primario inválido con retry válido aprueba (como `next._review`). T4 parte 1 (config, `atta models fallback`, guard): 1049 OK.
- **L6** Re-verificación de T1: B1-B5 corregidos; quedaban R1 (hallazgos en otro markup: `**[blocker]**`, `1. [blocker]`, escapados, headings) y R2 (la denylist por nombre nunca es completa: `build.py`, `deploy.py`, `Package.swift`, `webpack.prod.js`, `.cursor/`...). Nueva hipótesis estructural: contar toda severidad entre corchetes del texto; dudar por carpeta oculta, carpeta de NOT_SOURCE_DIRS, palabra de BUILD_TOKENS en el nombre de un fuente y `.txt` fuera de docs; `evaluate` atrapa cualquier excepción (→ Reina LLM); `rule_risk` tolera `intake`/`agents` mal formados. Fuera de alcance: `next._risk` (compartido, previo) revienta con un `job.json` mal formado, sin aprobar. 175 OK en decide+next.
