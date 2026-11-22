# Atta: plan de construcción

> Tu colonia de agentes de software. Fuente de diseño: [`docs/design/arquitectura.md`](design/arquitectura.md).
> Identidad: [`brand/README.md`](../brand/README.md).
> Estado: F1 (la espina) hecha; F2 (la colonia en Claude Code) en curso. Atta se escribe desde cero.

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
| **F2 Colonia en Claude Code** (en curso: piezas hechas, falta confirmar la corrida de punta a punta, ver [`f2-colonia.md`](../odd/tasks/f2-colonia.md)) | Definiciones propias de los roles (Exploradora, Guía, Cortadora, Jardineras, Soldado, Reina), comando `/atta`, guard de herramientas en Python, dashboard con tokens y logo; **fixtures propios** (jobs ficticios en español, uno por camino: aceptado y mergeado, rechazado, filtro fallido, escalado) | Claude Code | Los fixtures se ven en el dashboard; un issue de prueba llega a PR contra `develop` |
| **F2.x Operación** | `atta doctor` (G1), `atta init --check` (G2) | Claude Code | `atta doctor` detecta cada tropiezo de la corrida real de F2 |
| **F3 Calidad determinista (Kernia + gentle-ai)** | Ratchet `PREVENTION:` + Cultivadora + `atta/memory/` (K1) como **tema que evoluciona** (G9), `CHANGES` sin hallazgos rechazado (K12), ast-grep bloqueante en el Filtro (K4), alcance del diff `FILES:` (K8), merge `--match-head-commit` (K6), diff antes/después de sync (K7), preflight de frescura (K14), plantilla de PR (K16), checklist núcleo corto (K2); **riesgo por reglas** con la lista de alto riesgo (G3), **Refutador** antes de bloquear (G4), **corrección acotada** (G5), **Rastreador** adversarial en riesgo alto (G6), reporte estructurado de la Cortadora (G10), criterios de aceptación citados textualmente (G11), presupuesto de contexto por agente (G12) | Claude Code | Cada mecanismo con su test de rechazo; menos rondas por job |
| **F4 Ruteo y velocidad** | Router para todas las etapas (tiers T0-T3, matriz etapa×riesgo, escalera por intento, independencia de modelo), lentes por riesgo, Reina determinista en `low`, Vigía en medio/alto (K10), dependencias `BLOCKED-BY` en la cola (K5), timeouts por etapa; **cadenas de fallback de modelos** en `atta models` (G7); orquestador de `/atta` en Haiku (G8) | Claude Code | Job `low` sin LLM en la Reina; tiempos por etapa en el dashboard |
| **F5 Pi como segundo runtime** | Adaptador `PiRunner` con el contrato headless (`pi --mode json`, parseo de eventos, `agent_settled`, `--no-approve`, `--tools`, `-ne`/`-e atta-guard`, versiones fijadas, contenedor); Exploradora y Jardineras en modelos no-Claude | Claude Code + Pi | Mismos issues de referencia: costo y calidad comparados |
| **F6 Benchmark** | Repo de demostración propio + issues de referencia propios; re-ejecutarlos con varias matrices de modelos; `atta models report` (S12); comparar **juicio doble ciego** contra un juez caro (G13). Los jobs reales resultantes pasan a ser los **ejemplos oficiales** de Atta | ambos | Tabla costo × tasa de merge × rondas × tiempo |
| **F7 Conductor sin LLM** (si F6 lo justifica) | Orquestación determinista, Cortadora multi-tier, `/atta` como comando de Pi | Pi | Un issue de punta a punta sin orquestador LLM |

Cada fase se entrega en commits de unidad de trabajo (~400 líneas) con tests y docs junto al comportamiento.

### 6.1 Lecciones de gentle-ai para Atta (backlog G1-G13)

Salen de usar gentle-ai / gentle-shell durante la construcción de Atta y de su documentación. Orden de
prioridad: G1 → G3 → G4/G5 → G7 → G6/G13.

| # | Idea de gentle-ai | En Atta | Fase | Por qué vale |
|---|---|---|---|---|
| G1 | `gentle-ai doctor` | `atta doctor`: `atta` en PATH, Python con pip, cuenta de `gh` con acceso al repo, `origin/develop`, labels, hook instalado, `checks.json` no vacío, versión de Claude Code y `/atta` registrado | F2.x | Habría detectado de entrada cada tropiezo de la corrida real |
| G2 | `gentle-ai sync` (assets administrados) | `atta init --check`: avisa si los agentes, el skill o el comando del proyecto quedaron atrás de la versión instalada | F2.x | Hoy se actualiza a mano con `--force` |
| G3 | Profundidad de revisión por riesgo + lista de alto riesgo (datos irreversibles, seguridad, contratos consumidos, concurrencia, entrega/CI, cambios sin test que los detecte) | El riesgo sale de **reglas** sobre el diff además del juicio de la Exploradora; si el diff toca esas categorías, sube | F3 | Hace verificable el riesgo que decide revisores y auto-merge (T13) |
| G4 | Refutador de hallazgos graves | Un Refutador barato (Haiku, solo lectura) confirma o descarta cada `CHANGES` antes de abrir otra ronda | F3 | Cada `CHANGES` falso cuesta una ronda entera: ataca la lentitud |
| G5 | Una sola corrección acotada | La ronda de corrección solo puede tocar lo señalado por los hallazgos (alcance verificado por la espina); si no alcanza → `needs-human` | F3 | Evita quemar rondas en cambios que se desvían |
| G6 | Verificador independiente adversarial | Rol **Rastreador** (solo riesgo alto): intenta romper el cambio y reporta bypasses | F3 | En la construcción de Atta encontró bypasses que los tests no veían |
| G7 | Perfiles con cadenas de fallback (cuota, rate limit, red; no contexto) | `atta models` con `fallbacks` por rol; el cambio queda en `events.jsonl` | F4 | Un 429 no corta el job |
| G8 | Orquestador barato (full-delegation) | `/atta` corre en Haiku: con `atta next` el orquestador ya no razona el pipeline | F4 | El orquestador era la mayor parte del costo |
| G9 | Engram: memoria como tema que evoluciona (buscar → actualizar, no duplicar) | La Cultivadora actualiza una lección existente en vez de agregar otra | F3 | `lessons.md` no crece hasta volverse inmanejable |
| G10 | Reporte estructurado de los workers (status, cambios, evidencia, riesgos, foco de revisión) | `build.md` con secciones fijas que la espina valida | F3 | La Reina revisa evidencia, no prosa |
| G11 | Specs con citas textuales (ODD) | La Guía copia tal cual los criterios de aceptación del issue | F3 | El modelo barato no «interpreta» de más |
| G12 | Presupuesto de evidencia y de contexto | Cada agente recibe solo spec + diff + lecciones relevantes, con tope | F3 | Menos tokens y menos tiempo por etapa |
| G13 | Judgment Day (dos jueces ciegos) | Soldado en riesgo alto: dos jueces baratos e independientes contra un juez caro, medido | F6 | Calidad parecida a menor costo, si el benchmark lo confirma |

**No se adopta:** el review nativo con consentimiento interactivo por candidato (choca con una fábrica
desatendida) ni la ceremonia de admisión de writers (Atta ya tiene su máquina de estados). La regla
«nunca le busques la vuelta al guard» ya está en el skill `/atta`.

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
