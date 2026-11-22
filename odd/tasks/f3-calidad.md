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
| T1 | S3 | `atta doctor` + `atta init --check` | pendiente | — |
| T2 | S4, S2 | Riesgo por reglas sobre el diff (categorías de alto riesgo) → riesgo efectivo | pendiente | — |
| T3 | S5, S2 | Refutador + corrección acotada | pendiente | — |
| T4 | S6 | Ratchet `PREVENTION:` + Cultivadora + `atta/memory/` | pendiente | — |
| T5 | S7 | Filtro: ast-grep, alcance `FILES:`, frescura | pendiente | — |
| T6 | S8 | Rastreador en riesgo alto | pendiente | — |
| T7 | S9 | Plantillas con contrato estructurado + tope Vigía | pendiente | — |
| T8 | S1-S10 | Corrida en el sandbox + docs + CI | pendiente | — |

## Log

- **L1** (usuario): «ya hice /atta run, continuemos con lo que falta para implementar toda la fabrica»
- **L2** Corrida 4 cerrada: issue #5 → handoff → merge humano del PR #6 → `/atta run` cerró el issue (CLOSED/COMPLETED, labels quitadas, comentario «El PR #6 quedó mergeado en develop. El issue #5 queda resuelto.», rama borrada). PR de F2: #9 (`feat/f2-colonia → develop`). PR #3 del README ya estaba mergeado.
- **L3** Orden: T1 y la revisión de la DoD de Kernia (`Kernia/app/CLAUDE.md`, `rules/`) en paralelo; luego T2 → T3 → T4/T5 → T6/T7 → T8.
