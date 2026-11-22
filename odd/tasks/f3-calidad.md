# F3: calidad determinista (+ F2.x operación)

Fases F2.x y F3 de `docs/PLAN.md` §6 y §6.1: `atta doctor`, riesgo por reglas, Refutador, corrección acotada, ratchet y memoria, Filtro ampliado, Rastreador y plantillas con contrato estructurado.
Rama: `feat/f3-calidad` (apilada sobre `feat/f2-colonia`, PR #9). Diseño: `docs/design/arquitectura.md` §8, `docs/PLAN.md` §6.1 (G1-G13), `odd/tasks/f2-colonia.md`.

## Specs

- **S1** Objetivo. Usuario: «continuemos con lo que falta para implementar toda la fabrica». Prioridad que el usuario aceptó: «Cerrar F2 con la corrida 4 y el PR. Después `atta doctor` … Después F3, empezando por el riesgo por reglas, el Refutador y la corrección acotada, que son los que más rondas ahorran.»
- **S2** Velocidad. Usuario: «no pase lo que esta pasando ahora mismo con el flujo de kerni, es que es muy lento, debemos mantener un equilibrio entre la calidad y buen uso del tiempo para ejecutar las tareas».
- **S3** `atta doctor` (G1): `atta` en PATH, Python con pip, cuenta de `gh` con acceso al repo, `origin/develop`, labels de Atta (incluida `atta:needs-review`), hook instalado, `checks.json` no vacío, versión de Claude Code y `/atta` registrado. `atta init --check` (G2): avisa si agentes, skill o comando del proyecto quedaron atrás de la versión instalada.
- **S4** Riesgo por reglas (G3): lista de alto riesgo (datos irreversibles, seguridad, contratos consumidos, concurrencia, entrega/CI, cambios sin test que los detecte); si el diff toca esas categorías, el riesgo del job sube aunque la Exploradora diga menos; el riesgo efectivo decide reviewers y entrega (`auto_merge_up_to`).
- **S5** Refutador (G4): un `CHANGES` pasa por un refutador barato de solo lectura antes de abrir otra ronda; si lo refuta, no hay ronda. Corrección acotada (G5): la ronda de corrección solo toca lo señalado por los hallazgos; si no alcanza → `needs-human`.
- **S6** Ratchet (K1) + memoria como tema que evoluciona (G9): cada `PREVENTION:` actualiza una lección existente parecida o crea una nueva en `atta/memory/lessons.md`; la Cultivadora la ejecuta la espina al cerrar el job.
- **S7** Filtro ampliado: ast-grep bloqueante si hay reglas (K4), alcance del diff contra `FILES:` del spec (K8), preflight de frescura del checkout principal (K14).
- **S8** Rastreador (G6): rol adversarial solo en riesgo alto.
- **S9** Plantillas: reporte estructurado de la Cortadora (G10), criterios citados textualmente por la Guía (G11), checklist núcleo corto (K2), plantilla de PR (K16), presupuesto de contexto (G12), tope del ciclo Vigía `REVISE`.
- **S10** Regla vigente: multiplataforma (D2), Atta solo integra en `develop` (D3), configuración solo por humanos (L29).

## Tasks

| ID | Specs | Route | Estado | Commit |
|---|---|---|---|---|
| T1 | S3 | `atta doctor` + `atta init --check` | hecho (765 tests) | ver commit feat(doctor) |
| T2 | S4, S2 | Riesgo por reglas sobre el diff (categorías de alto riesgo) → riesgo efectivo | hecho (766 tests) | ver commit feat(risk) |
| T3 | S5, S2 | Refutador + corrección acotada | hecho (Refutador; corrección acotada en T5) | ver commits feat(refute), feat(gate) |
| T4 | S6 | Ratchet `PREVENTION:` + Cultivadora + `atta/memory/` | pendiente | — |
| T5 | S7 | Filtro: ast-grep, alcance `FILES:`, frescura, tests por riesgo | hecho | ver commit feat(gate) |
| T6 | S8 | Rastreador en riesgo alto | pendiente | — |
| T7 | S9 | Plantillas con contrato estructurado + tope Vigía | pendiente | — |
| T9 | S2, S10 | Falla de configuración del Filtro → needs-human sin quemar rondas + reintento del Filtro en la misma ronda; checkout principal en `integration_branch` (Filtro y doctor) | pendiente (tras T3/T5) | — |
| T8 | S1-S10 | Corrida en el sandbox + docs + CI | pendiente | — |

## Log

- **L1** (usuario): «ya hice /atta run, continuemos con lo que falta para implementar toda la fabrica»
- **L2** Corrida 4 cerrada: issue #5 → handoff → merge humano del PR #6 → `/atta run` cerró el issue (CLOSED/COMPLETED, labels quitadas, comentario «El PR #6 quedó mergeado en develop. El issue #5 queda resuelto.», rama borrada). PR de F2: #9 (`feat/f2-colonia → develop`). PR #3 del README ya estaba mergeado.
- **L3** Orden: T1 y la revisión de la DoD de Kernia (`Kernia/app/CLAUDE.md`, `rules/`) en paralelo; luego T2 → T3 → T4/T5 → T6/T7 → T8.
- **L4** Revisión de Kernia (`CLAUDE.md` DoD :427-437, `sgconfig.yml`, 10 reglas ast-grep, `architecture_test.rb`). Hallazgo principal: el CI de Kernia nunca corre ast-grep (`config/ci.rb:3-19`); las reglas que de verdad se aplican son copias en `architecture_test.rb`. Decisiones para F3:
  - T5 ast-grep: sin `sgconfig.yml` o `rules/` vacío → paso `skipped`; con reglas y sin binario → falla (y `atta doctor` lo avisa); validar reglas con `ast-grep test --skip-snapshot-tests` si hay `testConfigs`; `ast-grep scan --json=stream` solo sobre archivos del diff; bloquear solo `severity: error`, warnings informan; parsear JSON, no confiar en el exit code.
  - T5 tests por riesgo (DoD :434): `checks.json` admite checks marcados `scope: touched|full`; riesgo efectivo low corre los `touched` y medium/high corre todo (S2 velocidad).
  - T5 «bugfix empieza en rojo» (:410): para issues tipo bug, el Filtro corre los tests nuevos contra la base y espera que fallen (opcional por config).
  - T5 tamaño del diff (:520): aviso cuando el diff supera N archivos (config), sin bloquear.
  - T7 revisores con alcance acotado (:433): «correctness y requisitos declarados, sin inventar trabajo»; núcleo corto del checklist con «grep antes de afirmar» y «no trabajar de memoria con APIs de terceros».
  - T7 plantilla de PR (K16) verificada contra el diff real (`git diff --stat` vs tabla de cambios).
  - T4 ratchet: una lección termina como regla ast-grep **con su test** (`valid`/`invalid`) o como test de arquitectura cuando un patrón no alcanza; la lección se guarda donde el próximo la ve.
  - T6 Rastreador: prompt «grep al llamado, no al loop que tocaste»: ante una validación/raise/escritura nueva, revisar todos los callers/writers.
  - Pendiente, no se adopta ahora: runner genérico de tests de arquitectura (son tests normales del proyecto); ratchet de allowlists «solo encoge» queda como idea para F3.x.
- **L5** T1 hecho: `atta doctor` (19 checks con ✓/⚠/✗, pista por falla, exit 1 solo con ✗, tolera config inválida para poder reportarla) y `atta init --check` (solo lectura, reutilizado por doctor). Corrida real en un proyecto temporal: detectó `checks.json` vacío y la label `atta:needs-review` faltante (el caso de L31 de F2) y el `python` sin pip (L13 de F2). El guard sigue negando `atta init --check` a agentes; `atta doctor` es la entrada segura para ellos.
- **L6** T2 hecho: `risk.py` clasifica el diff en data/security/contracts/concurrency/delivery/untested con reglas en `risk_rules` (defaults en código, el proyecto las reemplaza); al pasar el Filtro (`checks`) el riesgo efectivo = max(intake, reglas, previo), nunca baja; `## Riesgo` en checks.md, re-ruteo (p. ej. suma Soldado en high), evento `risk`, la entrega y las escaladas a T3 usan el riesgo efectivo. Pendiente para T8: README §2.6/§8.2 y ayuda de `atta gate`. Riesgo aceptado: heurísticas por defecto pueden subir a high más de lo deseado; el proyecto ajusta `risk_rules`.
- **L7** (usuario, corrida en otra Mac sobre `atta-sandbox`) job #8 (pow): el Filtro falló dos rondas con «atta/checks.json no tiene checks» y la spine pidió ronda 3; job #1 duplicado (issue #1 seguía abierto con label `atta`, `subtract` ya estaba en develop, `publish` sin commits). Diagnóstico del padre: en `origin/develop` `atta/checks.json` sí tiene el check; la Mac casi seguro tiene el checkout principal en `main` (rama por defecto del repo), donde no hay archivos de Atta, y `atta init` creó ahí un placeholder vacío. Fallas de Atta: (a) una falla del Filtro por **configuración** (sin checks, binario o shell faltante) se trata como falla de la Cortadora y quema rondas; (b) nada verifica que el checkout principal esté en la rama de integración. Decisión: T9 — falla de configuración → `needs-human` inmediato sin consumir ronda; al corregirla, `atta next` vuelve a correr el Filtro en la misma ronda; el Filtro y `atta doctor` fallan si el checkout principal no está en `integration_branch`. Issue #1 cerrado por el padre con comentario (residuo de la corrida 1).
- **L8** T5 hecho (ast-grep con reglas del checkout principal, solo `severity: error` bloquea; checks `when: risk>=medium` se saltean en riesgo bajo; `## Alcance` con `FILES:`, tamaño y corrección acotada desde la ronda 2, `scope.enforce`/`scope.max_files`; `## Frescura` sin bloquear; doctor avisa si hay `sgconfig.yml` sin binario). T3 Refutador hecho (REFUTED → approve sin ronda; UPHELD/PARTIAL → build; solo minors → approve; reintento único si el archivo viene mal).
- **L9** (usuario) «igual es las configuraciones del flujo no solo debemos pensar en que sea barato, en los pasos en donde se requiere un modelo potente para garantizar calidad, se debe usar». Principio de diseño adoptado: **barato donde el error es barato, potente donde un error es caro**. Aplicado ya al Refutador (puede anular un hallazgo): nunca corre con un modelo más débil que el revisor más fuerte que juzga (`floor`), y en riesgo alto va a T3; si no hay un modelo distinto y no más débil, repite modelo con contexto limpio. Se mantiene: Soldado, Reina y Cortadora escalan a T3 en riesgo alto o desde la ronda 2; Exploradora y Jardinera UI en T1. A revisar en T6/T7 y F4 con este principio.
- **L10** (usuario) «todo el tema de configuraciones es completo y poco practico con comandos "atta models set --role guia opus" , necesitamos incluir un menu seleccionable … así como lo hace gentle-ai: gentle:models, gentle:profiles, etc … agregar eso en el plan para luego mejorarlo». Agregado a `docs/PLAN.md` como F2.y «Configuración interactiva» (§6.2): hub `/atta:config`, `/atta:models`, `/atta:profiles`, `/atta:delivery`, `/atta:rounds`, `/atta:risk`, `/atta:labels`; decisiones abiertas: dónde vive el menú (Claude Code, terminal, hormiguero), confirmación humana compatible con el guard (L29 de F2), hormiguero editable, formato de perfiles.
