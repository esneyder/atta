# Atta

**Tu colonia de agentes de software.** Etiquetas un issue y una colonia de agentes lo evalúa, lo
especifica, lo construye, lo prueba, lo revisa y abre el pull request contra `develop`.

El nombre viene de *Atta laevigata*, la hormiga culona de Santander, y de las hormigas cortadoras: no
se comen las hojas, las cortan, las llevan al hormiguero y con ellas cultivan su alimento. Atta
funciona igual: los issues son las hojas, el repositorio es el hormiguero y el código es el jardín.

---

## Contenido

1. [Estado actual](#1-estado-actual)
2. [Cómo funciona la fábrica](#2-cómo-funciona-la-fábrica)
3. [Requisitos e instalación](#3-requisitos-e-instalación)
4. [Recorrido rápido](#4-recorrido-rápido)
5. [Referencia de comandos](#5-referencia-de-comandos)
6. [El Filtro](#6-el-filtro)
7. [Configuración](#7-configuración)
8. [Anatomía de un job](#8-anatomía-de-un-job)
9. [El hormiguero (dashboard)](#9-el-hormiguero-dashboard)
10. [La hormiga en la terminal](#10-la-hormiga-en-la-terminal)
11. [Seguridad y guardas](#11-seguridad-y-guardas)
12. [Desarrollo](#12-desarrollo)
13. [Solución de problemas](#13-solución-de-problemas)
14. [Hoja de ruta y documentos](#14-hoja-de-ruta-y-documentos)

---

## 1. Estado actual

Atta se construye por fases (ver [`docs/PLAN.md`](docs/PLAN.md)). Hoy está terminada **F1**:

| Pieza | Estado |
|---|---|
| Espina determinista: estados, transiciones, contadores, `events.jsonl` | ✅ |
| Filtro (`gate`): checks del proyecto + rutas protegidas | ✅ |
| Router por riesgo y política de ramas (`develop` sí, `main` nunca) | ✅ |
| CLI `atta` con la hormiga animada | ✅ |
| El hormiguero (dashboard local) | ✅ |
| CI en Windows, macOS y Linux | ✅ |
| Agentes de la colonia (Exploradora, Guía, Cortadora, …) | ⏳ F2 |
| Integración con GitHub (leer issues, abrir PR, mergear a `develop`) | ⏳ F2 |
| Calidad avanzada (ratchet, ast-grep, alcance del diff) | ⏳ F3 |
| Ruteo de modelos por etapa y riesgo, Pi como segundo runtime | ⏳ F4-F5 |

Mientras no existan los agentes, los artefactos que ellos escribirían (`intake.md`, `spec.md`,
`build.md`, `review-*.md`, `decision.md`) se crean a mano o por script, y los comandos de este README
mueven el job por la máquina de estados exactamente como lo hará la fábrica completa.

---

## 2. Cómo funciona la fábrica

### 2.1 La idea

Una fábrica de software convierte una cola de pedidos en código mergeado con un pipeline fijo.
En Atta hay dos tipos de trabajo, y nunca se mezclan:

- **Lo mecánico es script** (la *espina*): mover estados, correr los checks, decidir rutas, mergear.
  Es determinista, rápido y no se puede convencer con un prompt.
- **El juicio es de los agentes**: si un issue vale la pena, qué construir, el código y si está bien.
  Cada agente lee archivos, escribe **un** artefacto y su primera línea es un veredicto que la espina
  valida (`DECISION: ACCEPT`, `GATE: PASS`, `VERDICT: CHANGES`…).

Todo el estado vive en archivos (`atta/jobs/<id>/`), así que cualquier job se puede reanudar.

### 2.2 La colonia

| Rol | Nombre técnico | Qué hace | Artefacto | Primera línea |
|---|---|---|---|---|
| **Exploradora** | `intake` | Decide si el issue vale la pena, lo acota y mide el riesgo | `intake.md` | `DECISION: ACCEPT\|REJECT\|NEEDS-INFO` |
| **Guía** | `spec-writer` | Escribe la especificación | `spec.md` | `STATUS: READY` |
| **Vigía** | `spec-review` | Revisa el spec contra el código real (solo riesgo medio y alto) | `spec-review.md` | `VERDICT: READY\|REVISE` |
| **Cortadora** | `builder` | Código, tests y commits en su rama | `build.md` | `STATUS: DONE\|BLOCKED` |
| **Filtro** | `gate` | Corre los checks del proyecto. **Es un script, no un agente** | `checks.md` | `GATE: PASS\|FAIL` |
| **Jardineras** | `reviewer-code`, `-ux`, `-ui`, `-resilience` | Revisan calidad con lentes | `review-<lente>.md` | `VERDICT: PASS\|CHANGES` |
| **Soldado** | `reviewer-security` | Revisa seguridad | `review-security.md` | `VERDICT: PASS\|CHANGES` |
| **Reina** | `approver` | Aprueba (merge a `develop`) o escala a un humano | `decision.md` | `APPROVE\|ESCALATE` |
| **Cultivadora** | `ratchet` | Convierte lecciones de las revisiones en memoria y reglas (F3) | `atta/memory/` | — |

Los tokens de contrato (`ACCEPT`, `PASS`, `CHANGES`…) van siempre en inglés: son la interfaz entre
máquinas. Lo que ve el humano va en español y firmado por rol: «La Exploradora aceptó el issue #12
(riesgo bajo).», «Tres rondas sin pasar el filtro. Te lo dejamos a ti.».

### 2.3 El pipeline

```text
issue (label atta)
   │
   ▼
intake ──► needs-info ──► (el autor responde) ──► intake
   │  └──► rejected
   ▼
spec ──► spec-review (riesgo medio/alto) ──► spec (si REVISE)
   │                 │
   ▼                 ▼
build ◄──────────────┘
   │
   ▼
gate ──FAIL──► build            (nueva ronda)
   │ PASS
   ▼
review ──CHANGES──► build       (nueva ronda)
   │ todo PASS
   ▼
approve ──► merging ──► merged  (PR contra develop)
   └──► needs-human ──► build (con feedback) | rejected
```

### 2.4 Etapas y transiciones legales

| Desde | Puede ir a |
|---|---|
| `intake` | `spec`, `needs-info`, `rejected` |
| `needs-info` | `intake`, `rejected` |
| `spec` | `spec-review`, `build` |
| `spec-review` | `spec`, `build` |
| `build` | `gate`, `needs-human` |
| `gate` | `review`, `build`, `needs-human` |
| `review` | `approve`, `build`, `needs-human` |
| `approve` | `merging`, `needs-human` |
| `merging` | `merged`, `needs-human` |
| `needs-human` | `build`, `rejected` |
| `merged`, `rejected` | — (terminales) |

Cualquier otra transición se rechaza con código de salida `2` y **no modifica** ni `job.json` ni
`events.jsonl`.

### 2.5 Rondas y topes

Cada vuelta `build → gate → review` es una ronda. Hay dos límites:

- **Por contador** (`max_rounds`, 3 por defecto): `gate_rounds` (volver a build por el Filtro),
  `review_rounds` (volver a build por las Jardineras) y `sync_attempts` (entradas a `merging`).
- **Global** (`max_total_rounds`, 6 por defecto): suma de rondas del job. El feedback humano **no lo
  reinicia**. Al llegar al tope, la única salida es `needs-human`.

### 2.6 Ruteo por riesgo

La Exploradora fija `RISK` y `SURFACES`; el router decide quién revisa (tabla en `atta/config.json`,
clave `routing`):

| Riesgo | Reviewers | Vigía |
|---|---|---|
| `low` | `code` (lente reliability, T1) | no |
| `medium` | `code` (reliability + readability, T2) | sí |
| `high` | `code` (T2) + `security` (T3) + `resilience` (T2) | sí |

Las superficies suman reviewers: `ui` agrega `ux` y `ui`; `security` agrega el Soldado. Los tiers
(`T0` local … `T3` frontera) indican la potencia de modelo que usará cada rol desde F4.

### 2.7 Política de ramas

- Atta **integra siempre en `develop`**: ramas de trabajo `atta/<job-id>`, PR contra `develop`.
- **`main` está prohibida**: Atta nunca crea PR contra `main`, nunca empuja ni mergea a `main` y
  nunca despliega. El release `develop → main` es una decisión humana.
- Se aplica en código: la configuración rechaza cualquier forma de escribir `main` como rama de
  integración (`Main`, `refs/heads/main`, `origin/main`), y los comandos `check-pr-base` y
  `check-push` validan cada PR y cada push.

---

## 3. Requisitos e instalación

- **Python 3.10 o superior.** Solo biblioteca estándar: no hay dependencias que instalar.
- **git** reciente (probado con 2.49; el Filtro usa `--end-of-options`).
- **Windows, macOS o Linux.** En Windows, el Filtro usa `pwsh`, si no `powershell`, si no `cmd`.

```bash
git clone https://github.com/esneyder/atta.git
cd atta
git switch develop
```

### Cómo invocar el CLI

| Sistema | Comando |
|---|---|
| Windows (PowerShell o cmd) | `.\atta <comando>` |
| macOS / Linux | `./atta.sh <comando>` |
| Cualquiera | `python atta/bin/atta.py <comando>` |

En PowerShell el prefijo `.\` es obligatorio: PowerShell no ejecuta programas de la carpeta actual
sin él. (El comando `atta` global llegará con el empaquetado en F2.)

---

## 4. Recorrido rápido

Un job de punta a punta, a mano (en F2 lo harán los agentes):

```powershell
# 1. Crear el job para el issue #12 (título y cuerpo se guardan citados como dato)
.\atta create 12 --title "Agregar exportación a CSV"
#   → 12-agregar-exportacion-a-csv

# 2. La Exploradora escribe atta/jobs/12-agregar-exportacion-a-csv/intake.md:
#      DECISION: ACCEPT
#      RISK: medium
#      SURFACES: ui
#
#      ## Summary
#      Exportar pedidos a CSV.

# 3. Rutear: decide reviewers y si corre la Vigía
.\atta route 12-agregar-exportacion-a-csv
#   → El router asignó 3 reviewers a 12-agregar-exportacion-a-csv (riesgo medio, spec-review sí): code T2, ux T2, ui T1.

# 4. Avanzar por el pipeline
.\atta advance 12-agregar-exportacion-a-csv spec
#   → La Exploradora aceptó el issue #12 (riesgo medio).
.\atta advance 12-agregar-exportacion-a-csv spec-review
.\atta advance 12-agregar-exportacion-a-csv build
.\atta advance 12-agregar-exportacion-a-csv gate

# 5. Correr el Filtro sobre el worktree de la rama del job
git worktree add -b atta/12-agregar-exportacion-a-csv .worktrees/12 origin/develop
#   (…la Cortadora commitea en .worktrees/12…)
.\atta gate 12-agregar-exportacion-a-csv --worktree .worktrees/12 --base origin/develop

# 6. Ver el estado
.\atta status
.\atta summary

# 7. Validar antes de publicar
.\atta check-push atta/12-agregar-exportacion-a-csv   # 0: se puede empujar
.\atta check-pr-base develop                          # 0: base correcta
.\atta check-pr-base main                             # 2: «La Reina no mergea a main: Atta solo integra en develop.»
```

Una transición ilegal se rechaza sin tocar nada:

```text
> .\atta advance 12-agregar-exportacion-a-csv merged
Transición ilegal en 12-agregar-exportacion-a-csv: build → merged. Desde build solo se puede ir a: gate, needs-human.
(código de salida 2)
```

---

## 5. Referencia de comandos

Opción global: `--jobs-dir <carpeta>` (por defecto `atta/jobs`). Va **antes** del comando:
`.\atta --jobs-dir otra/carpeta status`.

Códigos de salida: **`0`** ok · **`1`** veredicto negativo (por ejemplo, el Filtro no pasó) ·
**`2`** rechazado o error de uso.

### `create <issue> --title <título> [--body-file <archivo>]`

Crea el job y muestra su id (`<issue>-<hasta 4 palabras del título, sin tildes>`). Crea la carpeta
`atta/jobs/<id>/` con `job.json` e `issue.md`. Título y cuerpo se guardan **citados** (cada línea con
`> `) y con un aviso de que son datos, no instrucciones. `--body-file` lee el cuerpo desde un archivo
UTF-8.

### `advance <job> <etapa>`

Mueve el job a otra etapa. Antes de moverlo valida, en este orden:

1. Que la transición sea legal (tabla de la sección 2.4).
2. El tope global `max_total_rounds`.
3. El presupuesto por contador `max_rounds` al volver a `build` desde `gate` o `review`, y al entrar
   a `merging`.

Si todo pasa, escribe una línea en `events.jsonl`, actualiza `job.json` y muestra el mensaje del rol
(«La Exploradora aceptó el issue #12 (riesgo medio).»). Si no, sale con `2` y no cambia nada.

### `route <job>`

Lee la cabecera de `intake.md` (`DECISION`, `RISK`, `SURFACES` y, opcionales, `BLOCKED-BY` y
`MIGRATION`), calcula el plan de revisión y lo guarda en `job.json` (`intake` y `agents`). Una
cabecera mal formada o un riesgo o superficie desconocidos se rechazan con `2`.

### `gate <job> --worktree <ruta> --base <ref> [--kind checks|merge-checks]`

Corre el Filtro (sección 6) sobre el worktree del job y escribe `round-N/checks.md` (o
`merge-checks.md`) y su `.log`. Muestra el resumen del Filtro con la hormiga animada mientras corre.
Sale `0` si pasa, `1` si no pasa y `2` si la configuración o el `--base` son inválidos.

### `status [job]`

Tabla de jobs (o de uno): `JOB`, `ISSUE`, `ETAPA`, `RONDAS` (usadas / tope), `COSTO` (`-` si no hay
precio) y `TIEMPO`.

### `summary`

La misma tabla con todos los jobs y una línea de totales:
`Total: 1 job · 1 en curso · 0 merged · 0 rejected · costo -`.

### `check-pr-base <base>`

Sale `0` solo si `<base>` es exactamente la rama de integración (`develop`). Cualquier otra, con
`main` y `master` incluidas, sale `2`: «La Reina no mergea a main: Atta solo integra en develop.».

### `check-push <ref>`

Sale `0` solo para `atta/<job-id>` (o `refs/heads/atta/<job-id>`). Rechaza `develop`, `main`,
`master`, refspecs (`HEAD:main`, `+rama`), recorridos (`atta/../main`) y cualquier otra rama.

### `board.py` (el hormiguero)

```text
python atta/bin/board.py [--jobs-dir D] [--port 8765] [--host 127.0.0.1] [--quiet]
```

Ver sección 9.

---

## 6. El Filtro

El Filtro decide si una rama está en condiciones de ser revisada. Es un script: ningún agente lo
puede convencer.

### Qué hace, en orden

1. **Valida la base** (`--base`): rechaza refs vacías, que empiecen con `-`, que no existan, que sean
   el mismo commit que `HEAD`, que no compartan historia o que ya contengan a `HEAD`. Así nadie puede
   ocultar el diff.
2. **Rutas protegidas**: si el diff **agrega, modifica, renombra o borra** cualquier ruta bajo
   `protected_paths`, falla sin correr checks. La comparación no distingue mayúsculas
   (`ATTA/checks.json` cuenta como `atta/checks.json`) y en un renombre se revisan las dos rutas.
3. **Worktree limpio**: si hay cambios sin commitear, falla («El Filtro no pasó: el worktree tiene
   cambios sin commitear.»).
4. **Checks**: corre los de `atta/checks.json` **del checkout principal** (nunca la copia del
   worktree, para que una rama no cambie los checks con los que se la juzga), en orden y cortando en
   el primero que falla. Cada check tiene tiempo límite; al vencer se mata todo su árbol de procesos.

Ignora los objetos de `git replace` y las variables `GIT_DIR`/`GIT_INDEX_FILE` del entorno.

### `atta/checks.json`

```json
{
  "checks": [
    {"name": "lint",  "run": "ruff check .",                       "shell": {"windows": "pwsh", "default": "bash"}},
    {"name": "tests", "run": "python -m unittest discover -s tests", "timeout_s": 600}
  ],
  "timeout_s": 900
}
```

| Campo | Significado |
|---|---|
| `checks[].name` | Nombre que aparece en el reporte |
| `checks[].run` | Comando; se pasa como un solo argumento al shell |
| `checks[].shell` | Opcional. Shell por sistema (`windows`, `default`). Sin él: `pwsh` → `powershell` → `cmd` en Windows, `bash` → `sh` en el resto |
| `checks[].timeout_s` | Opcional. Tiempo límite de ese check |
| `timeout_s` | Tiempo límite por defecto de cada check |

El archivo viene **vacío a propósito**: sin checks reales el Filtro **falla**, nunca pasa en
silencio. Ordena los checks de rápido a lento (formato → lint → tipos → tests → build).

### `checks.md`

```text
GATE: PASS
COMMIT: <sha completo del worktree>

## Summary
El Filtro pasó: 3 checks en 18 s.

## Protected paths
Base: `<sha de la base>`
…

## Worktree
…

## Checks
…
```

Si falla, `## Checks` incluye el código de salida, la duración y las últimas 80 líneas del check que
falló.

---

## 7. Configuración

`atta/config.json` (las claves que faltan toman el valor por defecto):

| Clave | Por defecto | Para qué |
|---|---|---|
| `integration_branch` | `"develop"` | Única rama a la que Atta integra |
| `forbidden_branches` | `["main", "master"]` | Ramas que Atta nunca toca. No puede estar vacía |
| `branch_prefix` | `"atta/"` | Prefijo de las ramas de trabajo. Debe terminar en `/` y no empezar con `refs/` |
| `max_rounds` | `3` | Tope por contador de rondas |
| `max_total_rounds` | `6` | Tope global de rondas por job |
| `concurrency` | `3` | Jobs en paralelo (lo usa el orquestador desde F2) |
| `labels` | `atta`, `atta:needs-info`, `atta:needs-human` | Labels de GitHub de la cola y los estados de espera |
| `protected_paths` | `atta/**`, `.claude/**`, `.pi/**`, `.github/workflows/**` | Rutas que ningún job puede tocar. No puede estar vacía |
| `prices_usd_per_mtok` | `{}` | Precio por millón de tokens por modelo. Sin precio, el costo queda `null` con un aviso (nunca corta) |
| `routing` | tabla de la sección 2.6 | Reviewers, lentes y tier por riesgo y por superficie |

La configuración se valida al cargar y los errores salen en español, por ejemplo:
`config.json: integration_branch «main» está en forbidden_branches; Atta nunca integra en una rama prohibida.`

---

## 8. Anatomía de un job

```text
atta/jobs/36-cifrar-tokens-de-sesion/
├── job.json            estado (solo la espina lo escribe, de forma atómica)
├── events.jsonl        una línea por transición
├── issue.md            el issue citado como dato
├── intake.md           Exploradora
├── spec.md             Guía
├── spec-review.md      Vigía
├── round-1/
│   ├── build.md        Cortadora
│   ├── checks.md       Filtro (+ checks.log)
│   ├── review-code.md  Jardinera
│   └── review-security.md
├── round-2/ …
└── decision.md         Reina
```

Cada artefacto empieza con su token de contrato y tiene una sección `## Summary` corta: es lo que
muestran el dashboard y el PR.

### `job.json`

| Campo | Contenido |
|---|---|
| `id`, `issue`, `title`, `issue_url` | Identidad del job y su issue |
| `pr`, `pr_url` | PR asociado (desde F2) |
| `branch`, `worktree` | `atta/<id>` y su carpeta de trabajo |
| `stage`, `stage_since` | Etapa actual y desde cuándo |
| `review_rounds`, `gate_rounds`, `sync_attempts` | Contadores por motivo |
| `total_rounds`, `max_total_rounds` | Rondas usadas y tope global (copiado al crear el job) |
| `intake` | `decision`, `risk`, `surfaces` |
| `agents` | Plan del router: reviewers, lentes, tier, Vigía |
| `started`, `ended` | Inicio y fin (UTC) |
| `cost_usd` | Suma de costos conocidos; `null` si ninguno tiene precio |

### `events.jsonl`

```json
{"ts": "2025-06-05T08:01:05Z", "job": "36-cifrar-tokens-de-sesion", "from": "intake", "to": "spec",
 "duration_s": 65, "model": "google/gemini-2.5-flash", "tier": "T1", "tokens_in": 2200,
 "tokens_out": 400, "cost_usd": 0.006}
```

`duration_s` es el tiempo que el job pasó en la etapa que deja. `model`, `tier`, `tokens_*` y
`cost_usd` son opcionales. Con esto el dashboard calcula tiempos por etapa y costo.

---

## 9. El hormiguero (dashboard)

```powershell
python atta/bin/board.py                                  # jobs de atta/jobs
python atta/bin/board.py --jobs-dir tests/fixtures/jobs   # jobs de ejemplo
```

Abre <http://127.0.0.1:8765/>.

- **Camino de hormigas**: un tablero kanban por etapa. La hormiga camina en la columna donde hay
  trabajo corriendo.
- **Estados**: en curso ◐ (ocre chicamocha), aprobado ✓ (verde hoja), te necesita ⚑ (rojizo culona).
  Siempre con ícono y palabra, nunca solo color.
- **Detalle del job**: clic en una tarjeta (o `#job=<id>` en la URL) muestra rondas, veredictos,
  modelos, tiempo por etapa, costo y los artefactos con su `## Summary`.
- **Estadísticas**: jobs activos, los que te necesitan, tasa de merge, costo total y mediana de
  tiempo por etapa.
- **Tema** claro u oscuro con el botón de arriba; respeta *reducir movimiento*.
- Se actualiza cada 5 s y se pausa cuando la pestaña no está visible.

### API (solo lectura)

| Ruta | Devuelve |
|---|---|
| `GET /api/jobs` | Todos los jobs con etapa, rondas, veredictos, tiempos y estadísticas globales |
| `GET /api/jobs/<id>/files` | Artefactos `.md` del job con su primera línea y su `## Summary` |
| `GET /api/jobs/<id>/file?path=<ruta>` | Contenido de un artefacto `.md` del job, como texto plano |

### Seguridad del dashboard

- Escucha solo en `127.0.0.1` por defecto. Con `--host 0.0.0.0` avisa: «escucha fuera de esta
  máquina; el tablero no tiene autenticación.»
- Solo lectura: cualquier método de escritura responde `405`.
- Cada lectura queda confinada a la carpeta de su job (se rechazan `..`, rutas absolutas, symlinks,
  junctions de Windows y hardlinks) y solo se sirven archivos `.md`.
- Valida el header `Host` (contra *DNS rebinding*), usa Content-Security-Policy estricta y muestra
  los datos de los jobs como texto, nunca como HTML.

---

## 10. La hormiga en la terminal

Mientras corre una etapa (por ejemplo `gate`), una hormiga que carga una hoja camina por la línea.
Cuando la etapa termina, la animación se borra y queda la línea del rol. **No agrega espera**: solo
se ve mientras hay trabajo real.

| Variable | Efecto |
|---|---|
| `ATTA_THEME=light` | Paleta para terminales de fondo claro (por defecto: oscura) |
| `ATTA_ASCII=1` | Fuerza la versión ASCII (terminales sin Unicode) |
| `ATTA_NO_ANIM=1` | Línea estática, sin animación ni color |
| `NO_COLOR=1` | Igual que la anterior (estándar no-color.org) |
| `TERM=dumb` | Igual que la anterior |

Sin TTY (CI, `| tee`, redirección a archivo) la salida es siempre estática y sin códigos ANSI. El
color se adapta a la terminal: truecolor, 256 o 16 colores.

---

## 11. Seguridad y guardas

| Riesgo | Guarda |
|---|---|
| Un issue con instrucciones maliciosas | Título y cuerpo se guardan citados como dato; nunca entran como instrucción |
| Un agente que cambia los checks con los que se lo juzga | El Filtro lee `checks.json` del checkout principal y falla ante cualquier cambio en rutas protegidas |
| Llegar a `main` | Política de ramas en código: PR solo contra `develop`, push solo a `atta/*`, configuración que rechaza `main` en cualquier forma |
| Ocultar el diff | Validación estricta de `--base`, sin objetos `git replace`, worktree limpio |
| Bucles infinitos de rondas | Tope por contador y tope global que el feedback no reinicia |
| Ids que escapan de su carpeta | Ids sanitizados (`[0-9a-z-]`, sin `..` ni separadores) |
| Fugas por el dashboard | Confinamiento por ruta resuelta, solo lectura, solo `127.0.0.1` |

---

## 12. Desarrollo

```text
atta/
├── bin/atta.py        entrada del CLI
├── bin/board.py       entrada del dashboard
├── spine/             la espina (biblioteca)
│   ├── config.py      carga y valida config.json
│   ├── machine.py     etapas, transiciones y contadores
│   ├── store.py       job.json atómico (único escritor)
│   ├── events.py      events.jsonl y costo
│   ├── issue.py       issue.md citado
│   ├── ids.py         ids de job sanitizados
│   ├── lock.py        lock por job (Windows y Unix)
│   ├── intake.py      parser de intake.md
│   ├── router.py      plan de revisión por riesgo
│   ├── branches.py    política de ramas y presupuesto de rondas
│   ├── gate.py        el Filtro
│   ├── cli.py         comandos
│   ├── term.py        la hormiga animada
│   ├── voice.py       mensajes en la voz de marca
│   └── board.py       servidor del dashboard
├── board/             HTML, CSS, JS y logos del dashboard
├── config.json
└── checks.json
tests/                 suite unittest + fixtures/jobs (jobs ficticios en español)
design/                sistema de diseño y direcciones del hormiguero
brand/                 identidad de marca
docs/                  plan y arquitectura
odd/tasks/             seguimiento de cada fase
```

### Tests

```text
python -m unittest discover -s tests -v
```

Más de 270 tests, sin red, con estado en carpetas temporales. El CI
(`.github/workflows/ci.yml`) corre la suite en Ubuntu, macOS y Windows con Python 3.10 y 3.12 en cada
push a `develop`, `feat/**` y `atta/**`, y en cada PR contra `develop`.

### Convenciones

- Python 3.10+ solo con biblioteca estándar, `pathlib`, nada exclusivo de Unix.
- Test primero: cada regla con su test, y cada rechazo verifica que no cambió ningún archivo.
- Código y commits en inglés (Conventional Commits); lo que ve el usuario, en español.
- Ramas de feature desde `develop`; PR contra `develop`. Nunca contra `main`.

---

## 13. Solución de problemas

| Síntoma | Causa y solución |
|---|---|
| PowerShell: «El término 'atta' no se reconoce…» | Falta el prefijo: usa `.\atta <comando>` |
| PowerShell: «Falta una expresión después del operador unario '-'» | Se copió el guion de una lista: el comando empieza en `.\atta` |
| `./atta.sh: Permission denied` | `chmod +x atta.sh` |
| El Filtro falla con «no tiene checks» | `atta/checks.json` está vacío a propósito: agrega los checks del proyecto |
| El Filtro rechaza `--base` | La base no existe, es igual a `HEAD` o no comparte historia: usa `origin/develop` tras un `git fetch` |
| «el worktree tiene cambios sin commitear» | Commitea o descarta los cambios del worktree antes de correr el Filtro |
| La hormiga se ve con símbolos raros | Usa `ATTA_ASCII=1` o una terminal con Unicode (Windows Terminal) |
| No quiero animación | `ATTA_NO_ANIM=1` o `NO_COLOR=1` |
| El dashboard muestra «Carpeta ignorada: … es un enlace» | Hay un symlink, junction o hardlink en `atta/jobs`; el tablero no lo sigue a propósito |

---

## 14. Hoja de ruta y documentos

| Fase | Contenido |
|---|---|
| **F1** ✅ | Espina, Filtro, router, ramas, CLI, dashboard, CI |
| **F2** | La colonia en Claude Code: definición de cada rol, comando `/atta`, guard de herramientas, integración con GitHub, empaquetado del CLI |
| **F3** | Calidad determinista: ratchet y memoria, ast-grep en el Filtro, alcance del diff, merge seguro |
| **F4** | Ruteo de modelos por etapa, riesgo e intento; Reina determinista en riesgo bajo |
| **F5** | Pi como segundo runtime para etapas baratas |
| **F6-F7** | Benchmark y conductor sin orquestador LLM |

- Plan de construcción: [`docs/PLAN.md`](docs/PLAN.md)
- Arquitectura y diseño: [`docs/design/arquitectura.md`](docs/design/arquitectura.md)
- Identidad de marca: [`brand/README.md`](brand/README.md)
- Diseños del hormiguero: [`design/hormiguero/`](design/hormiguero/)
- Seguimiento de F1: [`odd/tasks/f1-espina.md`](odd/tasks/f1-espina.md)

Licencia: por definir.
