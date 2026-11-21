# Atta: plan de construcción

> Tu colonia de agentes de software. Fuente de diseño: [`docs/design/arquitectura.md`](design/arquitectura.md).
> Identidad: [`brand/README.md`](../brand/README.md).
> Estado: diseño, sin código todavía. Atta se escribe desde cero.

## 1. Concepto aplicado a la arquitectura

La metáfora de la colonia no es solo marca: calza 1:1 con el diseño y se usa para nombrar lo que ves.

| Colonia | Atta | Pieza técnica (código, comandos) |
|---|---|---|
| Hormiguero | El repositorio | repo + worktrees `.worktrees/<job>` |
| Hojas que llegan | Issues | label `atta` |
| Caminos de feromona | El pipeline | espina determinista (`atta/bin/job.py`, después `conductor`) |
| Jardín de hongos | El código **y la memoria cultivada** | código + `atta/memory/` (lecciones del ratchet) |
| Exploradora | Evalúa, acota, mide riesgo | `intake` |
| Guía | Marca el camino | `spec-writer` |
| Vigía | Revisa el spec contra el código real (riesgo medio/alto) | `spec-review` |
| Cortadora | Trabajo pesado | `builder` |
| Filtro | Hojas contaminadas no entran | `gate` (script, sin LLM) |
| Jardineras | Calidad y detalle | `reviewer-code`, `reviewer-ux`, `reviewer-ui` (lentes 4R) |
| Soldado | Defiende el hormiguero | `reviewer-security` (lente de riesgo) |
| Reina | Decide qué entra | `approver` (regla determinista en riesgo bajo, LLM en medio/alto) |
| Cultivadora | Cultiva la memoria | `ratchet`: convierte los `PREVENTION:` en lecciones o reglas ast-grep |

Las cortadoras reales cultivan hongo con las hojas: el **ratchet** es exactamente eso, cada revisión
alimenta la memoria que hace más fuerte a la colonia.

## 2. Convenciones de identidad

- **Idioma:** español para todo lo que ve el humano (comentarios del bot en issues y PR, dashboard, docs de
  uso), con la voz de la marca: directo, cálido, frases cortas. Ej.: «Tres rondas sin pasar el filtro. Te
  lo dejamos a ti.»
- **Inglés para lo técnico:** código, identificadores, nombres de archivo, commits y **tokens de contrato**
  (`DECISION: ACCEPT`, `VERDICT: PASS`, `GATE: FAIL`, `STATUS: READY`). Los tokens nunca se traducen: son
  la interfaz máquina-máquina.
- **Nombres técnicos:** comandos `/atta run [n]` y `/atta <texto>`; CLI `atta`; labels `atta`,
  `atta:needs-info`, `atta:needs-human`, `atta:ratchet`; ramas `atta/<job>`; carpeta `atta/`; marcador
  `<!-- atta -->` en comentarios del bot.
- **Dashboard (el "hormiguero"):** usa `brand/tokens.css`. Estados: en curso → `chicamocha`,
  aprobado/merge/filtro pasado → `hoja`, necesita humano → `culona`, **siempre con ícono o palabra además
  del color**. Avatares de agentes con la forma hoja (`radius-hoja`). Tipografías Bricolage Grotesque /
  IBM Plex Sans / IBM Plex Mono.
- **Mensajes del bot** firmados por rol: «La Exploradora aceptó el issue #12 (riesgo bajo).», «El Filtro
  no pasó: 2 tests fallan.», «La Reina lo deja en tus manos: …».

## 3. Política de ramas

Atta integra siempre en `develop` y tiene `main` prohibida para merge, push y despliegue. Se aplica en
código (config con `forbidden_branches`, verificación de la base del PR, guard de push, preflight), no solo
en prompts. Detalle en la sección 12 de [`arquitectura.md`](design/arquitectura.md).

## 4. Multiplataforma

Atta funciona en Windows, macOS y Linux: espina en Python 3.10+ solo con stdlib, lock portable, `pathlib`,
`atta/checks.json` con shell por sistema, hooks en Python o TypeScript y CI en los tres sistemas. Detalle
en la sección 13 de [`arquitectura.md`](design/arquitectura.md).

## 5. Principios de ingeniería

1. Espina determinista dueña del estado; los agentes solo juzgan y escriben un archivo con veredicto en la
   primera línea.
2. Lo mecanizable es script, no prompt (Filtro, merge, sync, alcance, ratchet → reglas).
3. Profundidad proporcional al riesgo: carril rápido para `low`.
4. Modelo por etapa × riesgo × intento; el revisor nunca es el modelo que construyó.
5. Velocidad medida: `events.jsonl` con duración, modelo, tokens y costo por etapa.
6. Memoria local versionada (`atta/memory/`), Engram opcional; artifacts de proveedores solo como export.
7. Runtime intercambiable: Claude Code primero, Pi como segundo runtime.

## 6. Fases

| Fase | Contenido | Runtime | Salida verificable |
|---|---|---|---|
| **F0 Fundación: repo, marca, diseño y plan** ✅ | Repo `atta`, marca en `brand/`, [`arquitectura.md`](design/arquitectura.md), este plan | — | Repo con marca, diseño y plan |
| **F1 Espina propia, identidad y ramas** ✅ | Escrita desde cero en Python 3.10+ solo con stdlib: máquina de estados con transiciones legales, job store en `atta/jobs/<id>/` (único escritor de `job.json`), Filtro con `atta/checks.json` fail-fast, router por riesgo, CLI `atta`. Multiplataforma completo (lock portable, `pathlib`, sin herramientas Unix-only). `events.jsonl` con tiempos y costo no bloqueante; título del issue citado; `protected_paths` (`atta/**`, `.claude/**`, `.pi/**`, `.github/workflows/**`); contadores separados y tope global (K13). Política de ramas en código (`develop`, `main` prohibida). Mensajes del bot en español con la voz de marca. CI en Windows, macOS y Linux | — | Tests de la espina verdes en los tres sistemas; un PR con base `main` es rechazado |
| **F2 Colonia en Claude Code** | Definiciones propias de los roles (Exploradora, Guía, Cortadora, Jardineras, Soldado, Reina), comando `/atta`, guard de herramientas en Python, dashboard con tokens y logo; **fixtures propios** (jobs ficticios en español, uno por camino: aceptado y mergeado, rechazado, filtro fallido, escalado) | Claude Code | Los fixtures se ven en el dashboard; un issue de prueba llega a PR contra `develop` |
| **F3 Calidad determinista (Kernia)** | Ratchet `PREVENTION:` + Cultivadora + `atta/memory/` (K1), `CHANGES` sin hallazgos rechazado (K12), ast-grep bloqueante en el Filtro (K4), alcance del diff `FILES:` (K8), merge `--match-head-commit` (K6), diff antes/después de sync (K7), preflight de frescura (K14), plantilla de PR (K16), checklist núcleo corto (K2) | Claude Code | Cada mecanismo con su test de rechazo |
| **F4 Ruteo y velocidad** | Router para todas las etapas (tiers T0-T3, matriz etapa×riesgo, escalera por intento, independencia de modelo), lentes por riesgo, Reina determinista en `low`, Vigía en medio/alto (K10), dependencias `BLOCKED-BY` en la cola (K5), timeouts por etapa | Claude Code | Job `low` sin LLM en la Reina; tiempos por etapa en el dashboard |
| **F5 Pi como segundo runtime** | Adaptador `PiRunner` con el contrato headless (`pi --mode json`, parseo de eventos, `agent_settled`, `--no-approve`, `--tools`, `-ne`/`-e atta-guard`, versiones fijadas, contenedor); Exploradora y Jardineras en modelos no-Claude | Claude Code + Pi | Mismos issues de referencia: costo y calidad comparados |
| **F6 Benchmark** | Repo de demostración propio + issues de referencia propios; re-ejecutarlos con varias matrices de modelos. Los jobs reales resultantes pasan a ser los **ejemplos oficiales** de Atta | ambos | Tabla costo × tasa de merge × rondas × tiempo |
| **F7 Conductor sin LLM** (si F6 lo justifica) | Orquestación determinista, Cortadora multi-tier, `/atta` como comando de Pi | Pi | Un issue de punta a punta sin orquestador LLM |

Cada fase se entrega en commits de unidad de trabajo (~400 líneas) con tests y docs junto al comportamiento.

## 7. Decisiones

**Tomadas**

| # | Decisión |
|---|---|
| D1 | Roles nuevos: **Vigía** (`spec-review`) y **Cultivadora** (`ratchet`) |
| D2 | Atta funciona en **Windows, macOS y Linux** (sección 4) |
| D3 | La Reina **sí mergea**, siempre a **`develop`**; **`main` prohibida** para merge, push y despliegue (sección 3) |
| D4 | Ejemplos **propios**: fixtures propios en F2 y ejemplos reales en F6 |
| D5 | Repo git con remoto en GitHub (privado) |
| D6 | **Reescritura propia**: Atta se escribe desde cero, sin código ni textos de terceros |

**Abiertas**

1. Licencia: por elegir (hoy no hay archivo `LICENSE`).
2. Proveedores permitidos por repo y si Claude queda como tier T3 de escalamiento (antes de F4/F5).
3. En riesgo bajo, ¿la Reina mergea por regla determinista sin juicio LLM, o siempre con un LLM barato?
   (antes de F4)
4. Revisar `CLAUDE.md` de Kernia (DoD completa) y su carpeta `rules/` (ast-grep) antes de F3.
