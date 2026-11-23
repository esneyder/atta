# Atta: arquitectura y diseño

> Estado: F1, F2 y F3 implementadas; F3.x pendiente (K7 y el issue `atta:ratchet` de K1, §8). Lo que
> describe F4 en adelante (matriz completa de §5, Pi en §6.2) sigue siendo diseño. Este documento
> define cómo funciona Atta y por qué.
> El orden de construcción está en [`docs/PLAN.md`](../PLAN.md) y la identidad en [`brand/README.md`](../../brand/README.md).
> Los tokens de contrato (`VERDICT: PASS`, `GATE: FAIL`…) y los identificadores de código van en inglés; todo lo demás, en español.

---

## 1. Qué es Atta

Atta es una colonia de agentes de software. Etiquetas un issue con `atta` y la colonia lo evalúa, lo
especifica, lo construye, lo prueba, lo revisa y abre el pull request contra `develop`.

La idea central es simple: **el código manda y los modelos opinan**. Todo lo que define el estado de un
trabajo (qué etapa sigue, cuántas rondas van, si el build pasa, qué se mergea) lo decide código
determinista. Los modelos de lenguaje solo hacen lo que el código no puede: juzgar y escribir.

---

## 2. Principios

1. **La espina determinista es dueña del estado.** Una máquina de estados en Python es la única que
   escribe `job.json` y decide transiciones. Ningún agente cambia el estado directamente.
2. **Un agente, un artefacto, un veredicto.** Cada agente lee lo que necesita, produce **un solo
   archivo** y la primera línea de ese archivo es su veredicto (`DECISION:`, `STATUS:`, `VERDICT:`,
   `APPROVE`/`ESCALATE`). La espina lee esa línea; lo que el modelo diga en el chat no cuenta. Eso hace a
   Atta agnóstica del modelo.
3. **Lo mecanizable es script, no prompt.** Gate, merge, sincronización, comparación de diffs, verificación
   de alcance y conversión de lecciones en reglas corren sin LLM.
4. **Profundidad proporcional al riesgo.** Un issue `low` va por el carril rápido; la revisión cara solo
   aparece donde el riesgo la paga.
5. **Modelo por etapa × riesgo × intento.** Barato por defecto, caro solo cuando el riesgo o un fracaso
   medido lo justifican. Quien revisa nunca es el modelo que construyó.
6. **Fallar rápido.** El Filtro corre los checks de lo más rápido a lo más lento y corta en el primer fallo.
   Ningún LLM opina sobre un build roto.
7. **Medir antes de optimizar.** Cada transición queda en `events.jsonl` con etapa, modelo, tier,
   tokens, costo y duración.
8. **Memoria local y versionada.** Lo que la colonia aprende vive en `atta/memory/`, dentro del repo.
   Engram es un espejo opcional para búsqueda.
9. **El runtime es un adaptador.** Claude Code primero, Pi segundo. Cambiar de runtime no toca la espina.
10. **El issue es dato, no instrucción.** Título y cuerpo entran citados; nunca se ejecutan como órdenes.

---

## 3. La colonia: roles y contratos

| Rol | Nombre técnico | Qué hace | Artefacto | Primera línea |
|---|---|---|---|---|
| **Exploradora** | `intake` | Evalúa si el issue vale la pena, lo acota, mide el riesgo, extrae dependencias | `intake.md` | `DECISION: ACCEPT\|REJECT\|NEEDS-INFO` + `RISK: low\|medium\|high` |
| **Guía** | `spec-writer` | Escribe la especificación: alcance, archivos esperados (`FILES:`), criterios, `## Failure paths` | `spec.md` | `STATUS: READY` |
| **Vigía** | `spec-review` | Revisa el spec contra el código real con contexto limpio. Solo riesgo medio y alto | `spec-review.md` | `VERDICT: READY\|REVISE` |
| **Cortadora** | `builder` | Hace el trabajo pesado: código, tests, commits en su rama | `build.md` | `STATUS: DONE\|BLOCKED` |
| **Filtro** | `gate` | Corre los checks del proyecto. **Es un script, no un agente** | `checks.md` | `GATE: PASS\|FAIL` |
| **Jardineras** | `reviewer-code`, `reviewer-ux`, `reviewer-ui` | Revisan calidad y detalle con lentes 4R (reliability, readability, resilience) | `review-<lente>.md` | `VERDICT: PASS\|CHANGES` |
| **Soldado** | `reviewer-security` | Defiende el hormiguero: lente de riesgo y seguridad | `review-security.md` | `VERDICT: PASS\|CHANGES` |
| **Rastreador** | `rastreador` | Solo riesgo alto. Revisor adversarial: intenta romper el cambio siguiendo a cada caller, writer y reader de lo que tocó (G6) | `review-rastreador.md` | `VERDICT: PASS\|CHANGES` |
| **Refutador** | `refutador` | Antes de abrir otra ronda por un `CHANGES` con hallazgos blocker o major, contrasta cada hallazgo con el diff real; no agrega hallazgos (G4) | `refute.md` | `REFUTE: UPHELD\|REFUTED\|PARTIAL` |
| **Reina** | `approver` | Decide qué entra: aprueba o escala a un humano. En `low`, regla determinista | `decision.md` | `APPROVE\|ESCALATE` |
| **Cultivadora** | `ratchet` | Cosecha los `PREVENTION:` de las revisiones con hallazgos (`CHANGES` o `PASS` con menores) que el Refutador no descartó: actualiza una lección parecida o crea una nueva (G9) | `atta/memory/lessons.md` | — (es código de la espina, no un agente: corre al cerrar el merge y con `atta memory harvest <job>`) |

Reglas del contrato:

- Toda revisión `CHANGES` trae al menos un hallazgo estructurado y una línea `PREVENTION:`. Si no, la
  espina la rechaza.
- Un `CHANGES` con solo hallazgos menores no abre ronda: la Reina decide y los menores quedan como
  notas. Si el Refutador refuta todos los blocker y major (`REFUTED`), tampoco hay ronda.
- Cada artefacto termina con una sección `## Summary` corta, que es lo que leen el dashboard y el PR.
- Los mensajes del bot van firmados por rol: «La Exploradora aceptó el issue #12 (riesgo bajo).»,
  «El Filtro no pasó: 2 tests fallan.», «La Reina lo deja en tus manos: …».

---

## 4. Arquitectura

```
                 Issues de GitHub (label atta)
                              │
          ┌───────────────────▼────────────────────┐
          │  orquestación                           │
          │  hoy: comando /atta en el runtime       │
          │  después: conductor determinista        │
          │  loop: cola → avanzar → lanzar → esperar│
          └──┬──────────────┬───────────────┬───────┘
             │              │               │
     ┌───────▼──────┐ ┌─────▼────────┐ ┌────▼─────────────┐
     │ espina        │ │ router        │ │ adaptador runtime │
     │ job / gate    │ │ etapa×riesgo  │ │ ClaudeRunner      │
     │ estado,       │ │ ×intento →    │ │ PiRunner          │
     │ transiciones, │ │ modelo, tier, │ │ → artefacto,      │
     │ events.jsonl  │ │ tools, fallb. │ │   uso y costo     │
     └───────────────┘ └───────────────┘ └────┬──────────────┘
                                              │
              ┌───────────────────────────────▼──────────────────┐
              │ guard de herramientas del runtime                 │
              │  · bloquea escrituras fuera del worktree y en     │
              │    protected_paths                                │
              │  · bloquea push fuera de atta/* y gh pr merge     │
              └───────────────────────────────────────────────────┘
```

### 4.1 Piezas

| Pieza | Responsabilidad | Naturaleza |
|---|---|---|
| **Cola** | Issues con label `atta`; dependencias `BLOCKED-BY: #n`; migraciones serializadas | Externa + script |
| **Espina** (`atta/spine/machine.py`, `store.py`, `next.py`) | Máquina de estados, único escritor de `job.json`, transiciones legales, contadores, `events.jsonl`; `atta next` decide el próximo paso | Determinista |
| **Filtro** (`atta/spine/gate.py`, `scope.py`, `risk.py`) | Corre `atta/checks.json` y las reglas de `sgconfig.yml` desde el checkout principal; alcance del diff, frescura y riesgo por reglas | Determinista |
| **Router** (`atta/spine/router.py`, `models.py`) | Plan de reviewers por riesgo y superficies; modelo y tier de cada rol | Determinista |
| **Cultivadora** (`atta/spine/memory.py`) | Lecciones de `atta/memory/lessons.md` y las relevantes para cada prompt | Determinista |
| **CLI** (`atta`, `atta/spine/cli.py`) | Punto de entrada: `atta init`, `atta doctor`, `atta queue`, `atta next`, `atta gate`, `atta status`, `atta memory`, entre otros | Determinista |
| **Adaptador de runtime** | Lanza un agente, espera, recoge el artefacto y el uso | Código por runtime |
| **Agentes** | Los roles de la sección 3, uno por archivo de definición | LLM |
| **Dashboard** («el hormiguero») | `atta-board` o `python atta/bin/board.py`: sirve los jobs en JSON y una vista con `brand/tokens.css` | Determinista |

### 4.2 Pipeline

```
intake → route → worktree → spec → [spec-review] → build → gate → reviewers (paralelo) → approver → merge
                                                      ▲                       │
                                                      └──── ronda siguiente ──┘
```

- `spec-review` solo en riesgo medio y alto.
- Hasta `max_rounds` vueltas `build → gate → review`, con un tope global `max_total_rounds` que ni el
  feedback humano reinicia.
- Contadores separados en `job.json`: `review_rounds`, `gate_rounds`, `sync_attempts`.
- El estado vive en archivos (`atta/jobs/<id>/`), así que cualquier job se puede reanudar.
- El builder conserva su sesión entre rondas (una sesión persistente por job).

### 4.3 Dos modos de operación sobre el mismo núcleo

- **Desatendido:** `atta run` desde terminal, CI o cron.
- **Supervisado:** desde el runtime interactivo con `/atta`; el humano ve el avance y puede pedir una
  revisión adversarial o más lentes sobre un PR escalado.

---

## 5. Ruteo de modelos

La configuración no usa nombres de modelos sino **tiers lógicos**, que se resuelven a `provider/id` por repo.

| Tier | Uso | Ejemplo genérico |
|---|---|---|
| `T0 local` | Clasificación, resúmenes, formato | Modelo local pequeño (p. ej. `qwen3.5:4b` en Ollama) |
| `T1 rápido` | Exploradora, Jardineras de bajo riesgo, UI | Modelo "flash" en la nube |
| `T2 fuerte` | Guía, Cortadora en riesgo medio, revisión de código | Modelo "pro" en la nube |
| `T3 frontera` | Solo escalamiento: Soldado en high, Cortadora tras fallos, Reina en high | Modelo de frontera (p. ej. Claude) |

Los modelos concretos se eligen con el benchmark; la tabla define la **política**.

**Matriz etapa × riesgo (intento 1)**

| Etapa | low | medium | high | Tools |
|---|---|---|---|---|
| Exploradora | T1 | T1 | T1 | `read,grep,find` (sin shell) |
| Guía | T1 | T2 | T2 | `read,grep,find` |
| Vigía | — | T2 | T2 | `read,grep,find` |
| Cortadora | T1 | T2 | T2→T3 | `read,grep,find,edit,write,bash` (con guard) |
| Jardinera de código | T1 (1 lente: reliability) | T2 (+ readability) | T2 (4R) | `read,grep,find` + `git diff/log` |
| Soldado | — | T2 si toca seguridad | T3 siempre | `read,grep,find` |
| Jardinera de resiliencia | — | — | T2 | `read,grep,find` |
| Rastreador | — | — | T3 | `read,grep,find` + `git diff/log` |
| Refutador (si hay `CHANGES` grave) | T1, nunca bajo el revisor que juzga | ídem | T3 | `read,grep,find` + `git diff/log` |
| Jardineras UX / UI | T1/T0 si hay UI | T2/T1 si hay UI | T2/T1 si hay UI | lectura + navegador CLI |
| Reina | **determinista** | T2 | T3 | `read,grep,find` |

**Cantidad de lentes por riesgo** (tabla `routing` de fábrica): `low` = 1 Jardinera (reliability),
`medium` = 1 Jardinera con 2 lentes (reliability y readability), `high` = Jardinera de código con 2
lentes, resiliencia, Soldado (T3) y Rastreador (T3). Las superficies suman reviewers: `ui` agrega UX y
UI, `security` agrega al Soldado. El riesgo que cuenta es el efectivo: si el Filtro lo sube por reglas
(G3), el router vuelve a planear los reviewers.

**Refutador:** nunca corre con un modelo más débil que el del revisor más fuerte que juzga, evita
repetir el modelo de esos revisores cuando hay otro y en riesgo alto sube a T3.

**Implementado en F4 (T3):** la matriz conservadora (`low`: la Guía y la Jardinera UX bajan un tier;
`medium`: igual que antes; `high`: la Cortadora, la Reina y el revisor de seguridad suben a T3;
Reina y Refutador nunca bajan; riesgo desconocido cuenta como `high` para subir y nunca baja) vive en
`models.Table.stage_tier`; la Cortadora sube a T3 desde la ronda 2; los reviewers evitan el modelo de la
Cortadora solo hacia un modelo que no sea más débil y, bajo `high`, no más caro (así `medium` no encarece
cada revisión). `"routing_matrix": false` en `atta/config.json` la apaga y `atta doctor` avisa del cambio a
los proyectos que no fijaron la clave. Lo demás de esta sección (escalera en tres tiers, familia distinta)
sigue siendo diseño.

**Implementado en F4 (T5):** timeouts por etapa. `atta next` compara el `at` del agente pendiente con el
límite de su etapa (`timeouts.py`; `stage_timeouts_s` en `atta/config.json`, solo humanos); la primera vez
reintenta (evento `stage_timeout`), la segunda deja el job a una persona (needs-human; en intake y spec,
que no tienen ese paso, label, comentario y `wait`). No consume la cadena de fallback: un timeout no es una
falla de cuota, rate limit o red.

**Escalera por intento:** si la ronda N falla (`GATE: FAIL` o `VERDICT: CHANGES`), la ronda N+1 sube a la
Cortadora un tier (`T1 → T2 → T3`) y la última ronda siempre usa T3. Lo caro solo se paga cuando lo barato
demostró no alcanzar. Cada subida queda en `events.jsonl`.

**Independencia del revisor:** ninguna Jardinera, ni el Soldado, ni la Reina corre con el mismo modelo que
la Cortadora de esa ronda; idealmente, tampoco de la misma familia. Si la matriz los iguala, el router
elige el siguiente modelo del tier en otra familia. El router lo valida, no el prompt.

**Riesgo primero, costo después:** el tier se elige por riesgo y por evidencia de fracaso, nunca solo por
precio ni por tamaño del diff.

**Reina determinista en `low`:** si el riesgo es `low`, el Filtro pasó, todas las revisiones dicen `PASS`,
el diff está bajo el umbral de líneas y no toca rutas sensibles, la Reina aprueba por regla, sin LLM.

**Fallbacks:** cadena por tier para cuota, rate limit o red, gestionada por Atta y registrada en
`events.jsonl`. Nunca hay fallback silencioso a un tier más caro.

**Costo:** se toma de los eventos de uso que entrega el runtime, por invocación. Un modelo sin precio
conocido genera una advertencia, nunca bloquea el ruteo.

---

## 6. Runtime: Claude Code primero, Pi segundo

El adaptador de runtime expone una sola operación: «corre este rol, con este modelo y estas tools, sobre
este worktree, y devuélveme el artefacto, el uso y el costo». La espina no sabe qué runtime hay detrás.

### 6.1 Claude Code (primer runtime)

- La orquestación inicial es el comando `/atta` dentro de Claude Code, que solo llama a la CLI `atta` y
  lanza agentes; no decide estado.
- Cada rol tiene su definición propia de agente, escrita para Atta.
- El guard de herramientas y el nudge del Filtro al terminar la Cortadora son hooks escritos en Python.
- Costo leído de los registros de uso del runtime.

### 6.2 Pi (segundo runtime)

Pi es el motor headless natural para Atta: multi-modelo, con allowlist de tools y costo exacto por
mensaje. El `PiRunner` cumple este contrato, sin excepciones:

1. **`pi --mode json`** y parseo de los eventos JSONL. **El exit code no basta:** un fallo o aborto del
   asistente no produce un código distinto de cero; hay que leer `stopReason` y los errores en los eventos.
2. **Esperar `agent_settled`** antes de dar la etapa por terminada, y drenar siempre stdout.
3. **`--no-approve` explícito** en modo json, print o rpc; nada queda esperando consentimiento.
4. **`--tools` explícito por etapa.** Ojo: `--tools` no quita tools MCP salvo entradas `mcp__*`.
5. **`-ne` + `-e atta-guard`** para un set de extensiones reproducible: solo la extensión de Atta.
6. **`--model provider/id`** y `--thinking` según el router; `--session-id` por job para la Cortadora y
   `--session-dir` por job.
7. **Versiones fijadas** de Pi y extensiones: Pi no publica política de versionado y ha tenido cambios
   incompatibles en extensiones.
8. **Contenedor cuando sea posible:** Pi no trae sandbox.

La extensión `atta-guard` usa `tool_call` para bloquear escrituras fuera del worktree y en
`protected_paths`, y `agent_before_settle` en la Cortadora para correr un Filtro rápido como nudge. El
Filtro oficial siempre es el de la espina.

---

## 7. Seguridad

- **Frontera de confianza:** el label `atta` más el issue citado (título incluido y sanitizado).
- **Mínimo privilegio por etapa:** `--tools` por rol + guard que rechaza escrituras fuera del worktree y en
  `protected_paths`. La Exploradora no tiene shell; Jardineras, Soldado y Reina no tienen `write`/`edit`.
- **`protected_paths`:** `atta/**`, `.claude/**`, `.pi/**`, `.github/workflows/**`, `atta/checks.json` y la
  configuración. Una rama que los toque no se auto-mergea.
- **Denylist de la Cortadora:** deploy, `git push` fuera de `atta/*`, `gh pr merge`, borrar ramas,
  `curl | sh`. La defensa real es la postcondición de la espina (diff y rutas), no el hook.
- **Solo la espina hace push y merge.**
- **Credenciales** de proveedores fuera de los worktrees; los agentes no las ven.
- **Política de proveedores por repo:** qué código puede salir del equipo (forzar T0/local o solo
  proveedores aprobados).
- **Ids sanitizados** antes de usarlos en rutas.

---

## 8. Mecanismos de calidad (rescatados de Kernia)

Kernia es el proyecto del usuario con el flujo de agentes más maduro. Su valor está en la **disciplina de
calidad**; las personas y lo específico de Rails, Kamal y Linear no se trasladan. Regla de traslado:
**lo que en Kernia es un grep de transcript o una instrucción al agente, en Atta pasa a ser un chequeo
determinista de la espina cuando sea mecanizable.**

| # | Mecanismo | Debilidad en Kernia | Cómo entra en Atta | Tipo |
|---|---|---|---|---|
| K1 | **Ratchet**: cada `CHANGES` trae `PREVENTION:`; la lección generalizada va al checklist o, si es mecánica, a una regla ast-grep o un test | Depende de que el agente cumpla | `PREVENTION:` obligatorio; la espina rechaza la revisión sin él. La Cultivadora (`atta/spine/memory.py`) cosecha las prevenciones al cerrar el merge en `atta/memory/lessons.md` como temas que evolucionan (G9). **Pendiente (F3.x):** abrir un issue `atta:ratchet` para convertir una lección mecanizable en regla; hoy no se abre ninguno | Script + prompt |
| K2 | **Checklist de bugs**: releer el diff arriba-abajo y abajo-arriba; intentar producir la falla, no confirmar el happy path; sad path por comportamiento nuevo; bugfix empieza con test rojo; autorización del lado del servidor; cada límite de un guard fijado por un test; mutation-check | Muy largo y de dominio: diluye | Núcleo corto («## Core checklist») dentro de los prompts de los revisores, con «grep a callers y writers antes de afirmar» y «no trabajar de memoria con APIs de terceros»; los revisores juzgan correctness y requisitos declarados, sin inventar trabajo | Prompt |
| K3 | **DoD**: no se termina sin tests corridos y revisión independiente | Solo prueba que el comando se intentó; se apaga con texto | El Filtro corre los checks él mismo y exige `GATE: PASS` más todas las revisiones. Un diff de código sin tests cambiados sube el riesgo (categoría `untested` de G3, configurable en `risk_rules`). Nunca hay opt-out por texto | Script |
| K4 | **ast-grep** como guardarraíl | Nunca bloquea | Reglas del `sgconfig.yml` del checkout principal (nunca las de la rama) + `ast-grep scan` sobre los archivos del diff dentro del Filtro; solo `severity: error` **bloquea**, los warnings informan. Con `testConfigs`, las reglas pasan sus propios tests antes. Sin `sgconfig.yml` el paso se saltea; con reglas y sin binario falla como configuración. Destino natural de K1 | Script |
| K5 | **Cola con grafo de dependencias**: `blocked_by`, máximo paralelo, migraciones solas, dependientes de un fallo se saltan, ciclos detectados | Orquestado por agentes | La Exploradora extrae `BLOCKED-BY: #n` y `MIGRATION: yes/no`; `atta queue` respeta dependencias y serializa migraciones | Script |
| K6 | **Merge seguro**: CI verde en el SHA exacto | Lo ejecuta un agente | `job merge` usa `gh pr merge --match-head-commit` con el SHA que pasó el Filtro tras la sincronización | Script |
| K7 | **Diff antes/después de sincronizar** | Manual | **Pendiente (F3.x), no implementado:** tras sincronizar con `develop`, la espina comparará `origin/develop...HEAD` antes y después; si cambió algo fuera de conflictos triviales, vuelve a revisión | Script |
| K8 | **Alcance del diff**: nada filtrado ni perdido | Checklist de agente | El spec declara `FILES:` (rutas o globs); el Filtro falla o avisa si el diff sale del alcance o supera el límite de archivos | Script |
| K9 | **Reglas de planificación**: plan solo si hay varios PR, migración, cambio de contrato o proveedor nuevo; PR de pocos archivos; sección de fallos | Prompt | La Exploradora divide issues grandes en sub-issues enlazados (K5); `spec.md` exige `## Failure paths`; tope de archivos validado por K8 | Prompt + script |
| K10 | **Revisor del plan con contexto limpio**: verifica contra el código real, corrige errores de hecho, deja preguntas de producto | — | La Vigía, con modelo distinto de la Guía: `VERDICT: READY\|REVISE` antes de construir | Agente |
| K11 | **Reglas de ticket**: contexto y no prescripción, 2-4 criterios de aceptación, dependencias explícitas | — | Plantilla de issue `atta` en `.github/ISSUE_TEMPLATE/`; sin criterios → `NEEDS-INFO` | Prompt + plantilla |
| K12 | **El revisor siempre decide**; `CHANGES` sin hallazgos es un fallo | — | La espina rechaza `VERDICT: CHANGES` sin al menos un hallazgo estructurado | Script |
| K13 | **Presupuestos separados** de rondas de fix y de sincronización | Revisión y CI comparten contador | `review_rounds`, `gate_rounds`, `sync_attempts` separados + `max_total_rounds` | Script |
| K14 | **Preflight de frescura** | Solo aviso | Aviso, no bloqueo: el Filtro escribe `## Frescura` (con `git ls-remote`, sin fetch) si el checkout principal está atrás de `origin/develop`, y `atta doctor` lo marca con ⚠. Lo que sí bloquea es un checkout principal fuera de `develop`: falla de configuración (`KIND: config`) que va a `needs-human` sin gastar ronda | Script |
| K15 | **Denylist de comandos** | Huecos (`bash -c`, `sed` evade hooks de edición) | Guard con denylist generalizada (sección 7); la defensa real es la postcondición de la espina | Script |
| K16 | **Plantilla de PR**: Summary / Changes / Test plan con checkboxes | — | `atta/templates/pr-body.md`, llenada por la espina desde `build.md`; la lista de cambios se contrasta con `git diff` y una diferencia queda como advertencia en el PR | Script |
| K17 | **Formato de incidente**: síntoma, causa raíz, evidencia, radio de impacto, categoría, fix | — | Plantilla opcional de issue tipo bug; la Exploradora la usa para clasificar riesgo | Plantilla |
| K18 | **Índice de dominio** | Específico de Kernia | `atta/memory/context.md` (mapa del proyecto) que el instalador genera y leen Guía y Cortadora | Prompt |

**No se trasladan:** las personas y su rotación, la obligación de persona (no agrega garantías), reglas de
estilo de Rails/Tailwind, Kamal, Linear.

**Revisión de la DoD de Kernia: hecha** (`odd/tasks/f3-calidad.md`, L4). Hallazgo principal: el CI de
Kernia nunca corre ast-grep; de ahí salieron las decisiones de K4 (paso bloqueante, solo `severity:
error`, JSON y no exit code), los checks por riesgo y el núcleo corto de K2.

### 8.1 Mecanismos de gentle-ai implementados en F3

El backlog completo está en [`docs/PLAN.md`](../PLAN.md) §6.1. En F3 entraron:

| # | Mecanismo | Cómo funciona hoy |
|---|---|---|
| G3 | Riesgo por reglas | `risk.py` clasifica el diff en datos, seguridad, contratos, concurrencia, entrega/CI y cambios sin test (`risk_rules`, el proyecto puede reemplazarlas). Cuando el Filtro pasa, el riesgo efectivo es el máximo entre el de la Exploradora, el de las reglas y el previo: nunca baja. Si sube, el router vuelve a planear los reviewers, y la entrega (`auto_merge_up_to`) usa el riesgo efectivo |
| G4 | Refutador | Un `CHANGES` con hallazgos blocker o major pasa por el Refutador antes de abrir ronda. `REFUTED` → approve; `UPHELD` o `PARTIAL` → build; un archivo mal formado se pide una vez más y después va a `needs-human` |
| G5 | Corrección acotada | Desde la ronda 2, el Filtro compara los cambios de la ronda con los hallazgos de la anterior (`## Alcance`). Es aviso; con `scope.enforce: true` falla |
| G6 | Rastreador | Revisor adversarial T3, solo en riesgo alto (tabla `routing` de fábrica). `atta doctor` avisa si un `routing` propio no lo incluye en `high` |
| G9 | Memoria como tema que evoluciona | La Cultivadora actualiza la lección más parecida (similitud ≥ 0,6) o crea una; la cosecha es idempotente |
| G10 | Reporte estructurado de la Cortadora | La espina valida `build.md` (`## Summary`, `## Changes`, `## Tests`, `## Acceptance` con ✓/✗, `## Risks`); si falta algo, un reintento en la misma ronda y después `needs-human` |
| G11 | Criterios citados textualmente | La plantilla de la Guía copia tal cual los criterios del issue y exige `FILES:` |
| G12 | Presupuesto de contexto | Los prompts de la Guía, la Cortadora y los revisores reciben solo las lecciones relevantes para las rutas del diff (o de `FILES:`), con un tope de 1.500 caracteres |

También de F3: el Filtro separa las fallas de **configuración** (`KIND: config`: sin checks, sin shell,
sin ast-grep, reglas que no pasan sus tests, checkout fuera de `develop`) de las de **código**. Una de
configuración va a `needs-human` sin gastar ronda y, cuando una persona la corrige, `atta next` vuelve a
correr el Filtro en la misma ronda. Los checks con `when: "risk>=medium"` se saltean en riesgo bajo. La
Vigía tiene tope: la tercera `REVISE` va a `needs-human`.

---

## 9. Equilibrio entre velocidad y calidad

El flujo de Kernia se siente lento. Causas probables (hipótesis, no medidas): un agente LLM ejecuta
incluso los pasos mecánicos (CI, merge, rebase); revisión de muchos pasos con un checklist enorme; poco
paralelismo; cadena larga de roles secuenciales; re-revisión tras cada rebase. Atta lo resuelve así:

1. **Carril rápido por riesgo.** `low`: sin Vigía, 1 Jardinera T1, Reina determinista. `medium`: 2 lentes.
   `high`: Vigía + 4R + Soldado.
2. **Lo mecánico es script.** Filtro, merge, sincronización, comparación de diff y alcance corren en
   segundos sin LLM.
3. **Fail fast en el Filtro.** `atta/checks.json` ordenado de rápido a lento (format/lint → typecheck →
   tests afectados → suite → build); corta en el primer fallo; cache de dependencias por worktree.
4. **Paralelismo real.** Jardineras en paralelo y varios jobs concurrentes; solo se serializa lo que
   comparte estado (migraciones, dependencias declaradas).
5. **Contexto chico.** Cada agente recibe spec + diff + lecciones relevantes, no el historial completo.
6. **Presupuesto de tiempo** por etapa (`timeouts` en config) y por job; al pasarse, escala o va a
   `atta:needs-human` en vez de seguir iterando.
7. **Medir.** El dashboard muestra tiempo por etapa y p50/p90 por riesgo. Meta inicial: un issue `low` de
   punta a punta en minutos, no en horas; el umbral exacto sale del benchmark.
8. **Re-revisar solo si algo cambió** (K7, pendiente de F3.x): una sincronización limpia no dispara
   otra ronda.

---

## 10. Memoria y documentación del trabajo

Atta no usa artifacts de un proveedor como fuente: quedan fuera del repo, sin versión e ilegibles para
otros runtimes. La memoria tiene capas:

| Capa | Qué guarda | Dónde | Quién la lee |
|---|---|---|---|
| Registro del job | Todos los artefactos (`intake`, `spec`, `build`, `checks`, `review-*`, `decision`) y `events.jsonl`, cada uno con `## Summary` | `atta/jobs/<id>/` (local) | Dashboard, humano, agentes del mismo job |
| Memoria cultivada | Lecciones generalizadas del ratchet, decisiones de arquitectura, convenciones; corta y deduplicada | `atta/memory/` en git (`lessons.md`, `decisions.md`, `context.md`) | Guía y Cortadora de futuros jobs (solo lo relevante) |
| Búsqueda semántica (opcional) | Espejo de la memoria cultivada | Engram, un proyecto por repo | Agentes con acceso a Engram |
| Vista humana | Resumen legible del job | Cuerpo y comentarios del PR (K16) | Humano en GitHub |

Reglas: la espina escribe la memoria al cerrar el job, no el agente a su antojo; se guarda la lección
generalizada, no el log; si una lección es mecanizable, termina como regla (K1/K4) y sale del texto.
Exportar a artifacts para lectura humana es opcional y nunca es la fuente.

---

## 11. Hallazgos sobre gentle-ai y Pi

Revisión de la documentación oficial (gentle-ai 4.6.0, gentle-shell 4.9.0, Engram 3.7.2, Pi 1.6.0;
noviembre de 2026).

| Tema | Hallazgo | Fuente |
|---|---|---|
| Qué es gentle-ai | CLI en Go que **configura** agentes existentes (Pi, Claude Code, OpenCode, Codex, entre otros); no instala un agente por ti | https://github.com/Gentleman-Programming/gentle-ai |
| Workflow | El flujo actual es ODD: tamaño por entendimiento, riesgo y reanudabilidad; delega solo por razón nombrada | gentle-ai: README, `docs/usage.md`, `docs/pi.md` |
| Modelos por fase | No hay matriz por fase de desarrollo; la asignación es por roles | gentle-ai: `docs/agents.md` |
| Filosofía de ruteo | El número de archivos, el tamaño o el precio nunca deciden la ruta por sí solos | gentle-ai: README |
| Verificador independiente | El verificador nunca es el modelo que escribió el cambio | gentle-ai: README |
| Modelos en Pi | Los asigna gentle-shell con perfiles nombrados, pin por repo y fallbacks por cuota | https://github.com/Gentleman-Programming/gentle-shell |
| Costo del orquestador | En su benchmark, una sesión LLM orquestadora fue cerca del 90 % del costo total: orquestar con código ahorra | gentle-shell: referencia del README |
| RDD / 4R | El riesgo decide la profundidad: pasivo = 0 lentes, medio = 1, alto = 4R; una corrección acotada | gentle-ai: `docs/review-integration.md` |
| Restricciones headless | `subagent_run` en background no funciona bajo `pi -p` / `--mode json`; consentimientos interactivos fallan cerrado; el review nativo no declara soporte Windows de punta a punta | gentle-shell: docs |
| Windows | gentle-ai soportado, sin binario público (instalación con `go install`) | gentle-ai: releases |
| Engram | Binario Go + SQLite/FTS5; CLI, HTTP, MCP y TUI; scope por `ENGRAM_PROJECT` | https://github.com/Gentleman-Programming/engram |
| Pi: subagentes | No son nativos, por decisión de diseño | https://pi.dev |
| Pi: modo JSON | Un fallo del asistente no cambia el exit code; esperar `agent_settled`; drenar stdout | https://docs.pi.dev/latest/cli-integration.md, https://docs.pi.dev/latest/json.md |
| Pi: seguridad | Sin sandbox; recomienda contenedor; pasar `--approve`/`--no-approve` explícito | https://docs.pi.dev/latest/security.md |
| Pi: tools | `--tools` no quita tools MCP salvo `mcp__*`; `-ne` + `-e` para extensiones reproducibles | https://docs.pi.dev/latest/cli.md |
| Pi: estabilidad | Sin política de versionado publicada; hubo cambios incompatibles en extensiones → fijar versiones | https://github.com/earendil-works/pi/releases |

**Qué toma Atta de gentle-ai**

| Componente | Uso en Atta | Motivo |
|---|---|---|
| Lentes 4R | Sí, como base de las Jardineras | Ya separan riesgo, resiliencia, legibilidad y fiabilidad, cada una con su modelo |
| Riesgo decide profundidad | Sí, en la matriz de la sección 5 | Mismo principio que el carril rápido |
| Independencia del verificador | Sí, validada por el router | Evita que un modelo apruebe su propio trabajo |
| Formato de agentes y perfiles | Sí, para las definiciones en Pi | Editable desde gentle-shell |
| Judgment Day | Opcional, modo supervisado, PRs escalados en high | Dos jueces ciegos baratos pueden sustituir a uno caro |
| Engram | Opcional, como espejo de `atta/memory/` | Búsqueda por tema sin perder la fuente versionada |
| `subagent_run`, review nativo RDD | Solo modo supervisado | Requieren humano en el loop |
| ODD como formato de job | No | Atta tiene su propio contrato de artefactos |

**Conclusiones:** gentle-ai no trae un orquestador multi-modelo por fases, así que Atta orquesta con su
propia espina. Pi es el motor de ejecución headless ideal; gentle-shell sirve para el modo supervisado y
como fuente de lentes y perfiles, no como corazón de un loop desatendido. La Reina con auto-merge va más
allá del modelo de gentle (allí la entrega es humana): es una decisión explícita de Atta, acotada a
`develop` (sección 12).

---

## 12. Política de ramas

- **Atta integra siempre en `develop`.** Las ramas de trabajo (`atta/<job>`) salen de `origin/develop`,
  los PR apuntan a `develop`, la sincronización previa al merge es contra `develop` y la Reina mergea a
  `develop`.
- **`main` está prohibida para Atta:** nunca crea PR contra `main`, nunca mergea ni empuja a `main`, nunca
  despliega. Pasar de `develop` a `main` es una decisión humana fuera de la colonia.
- Se aplica en código, no solo en prompts:
  - Config: `integration_branch: "develop"` y `forbidden_branches: ["main", "master"]`; la espina rechaza
    una config que integre en una rama prohibida.
  - `job merge` verifica que la base del PR sea `develop` (`gh pr view --json baseRefName`) y usa
    `--match-head-commit` con el SHA que pasó el Filtro.
  - El guard bloquea `git push` a ramas fuera de `atta/*` y cualquier `gh pr merge` de un agente.
  - El Filtro y `atta doctor` fallan si el checkout principal no está en `develop` (de ahí salen
    `checks.json` y las reglas); `atta doctor` verifica que `develop` exista en `origin` y avisa si el
    checkout está atrás.
- El instalador recomienda proteger `main` en GitHub como defensa adicional.

---

## 13. Multiplataforma: Windows, macOS y Linux

- **Espina en Python 3.10+ solo con stdlib**, invocada como `python atta/bin/<script>.py` (sin shebangs
  ni permisos de ejecución) o a través de la CLI `atta`.
- **Nada exclusivo de Unix:** lock de archivos portable (`msvcrt` en Windows, `fcntl` en Unix, detrás de
  una sola función), rutas con `pathlib`, puertos libres detectados con `socket`, sin `rm -rf` ni `sed`
  en scripts.
- **Checks del proyecto** en `atta/checks.json`: lista de comandos y shell por sistema operativo (`bash`
  en macOS/Linux, `pwsh` o Git Bash en Windows).
- **Hooks y guard** en Python o en la extensión TypeScript de Pi, nunca en `.sh`.
- **Dashboard** con servidor propio mínimo en Python que expone los jobs en JSON.
- **CI de Atta** con matriz `windows-latest`, `macos-latest`, `ubuntu-latest`.
- **Dependencias externas documentadas por sistema:** `git`, `gh`, Python, `ast-grep` (opcional) y el
  runtime del agente (Claude Code o Pi).

---

## 14. Riesgos y mitigación

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Modelos baratos producen más rondas y el costo real sube | Alto | Escalera por intento, benchmark y `max_total_rounds` |
| Modelos pequeños rompen el contrato de primera línea | Medio | La espina valida y reintenta una vez con recordatorio de formato; luego sube de tier |
| Contexto chico en modelos locales | Medio | Spec y diffs acotados; T0 solo para resúmenes y clasificación |
| Cambios en la API de Pi o gentle-shell | Medio | Adaptador aislado, versiones fijadas, sin depender de `subagent_run` en el loop desatendido |
| Diferencias entre sistemas operativos | Medio | Stdlib, `pathlib`, `checks.json` por sistema, CI en tres sistemas |
| Fuga de código a proveedores externos | Alto según el repo | Política por repo: T0/local o proveedores aprobados |
| Prompt injection por el issue | Alto | Cita total (título incluido), Exploradora sin shell, guard de tools |
| Auto-merge de algo indebido | Alto | Solo a `develop`, `main` prohibida, `protected_paths`, SHA exacto, Reina determinista solo en `low` |
