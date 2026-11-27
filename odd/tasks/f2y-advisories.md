# F2.y y F4.x: advisories pendientes

Advisories de `odd/tasks/f2y-config.md` (L6, L10, L11) y de `odd/tasks/f4x-pendientes.md` (L9).
Rama: `feat/f2y-advisories` (apilada sobre `feat/f4x-pendientes` en f0b8032, sin push). Suite: `/opt/homebrew/bin/python3.12 -m unittest discover -s .` desde `tests/`.

## Specs

- **S1** A1, textual (F2.y L10): «un `profile` desconocido en config.json bloquea todos los comandos (falla cerrado) y ni `profiles clear` ni doctor dicen cómo salir (hay que editar el archivo)». Decisión del usuario: «Seguir fallando cerrado, pero con salida (Recomendado)»: «Los comandos siguen rechazando el perfil desconocido, pero `atta profiles clear` funciona y el mensaje y `atta doctor` dicen cómo salir.»
- **S2** A2, textual (F2.y L10): «si después de un perfil cambiás la entrega con `atta settings`, `atta profiles` sigue mostrando la del perfil (solo `--json` y `show` muestran la real)». Y R3-origin, textual (F2.y L11): «un override de proyecto igual al valor del perfil se muestra como `perfil` y no como `proyecto`». `atta profiles` muestra los valores vigentes y su origen real.
- **S3** A3, textual (F2.y L10): «`"profile": null` se rechaza». Decisión del usuario: «Aceptar null como «sin perfil» (Recomendado)»: «Igual que no tener la clave.»
- **S4** A4, textual (F2.y L10): «`profiles use` en una carpeta sin init crea config.json (igual que `models set`)». Decisión del usuario: «Rechazar y pedir `atta init` (Recomendado)»: «Sale con código 2 y el mensaje «corre atta init primero»; no crea nada.»
- **S5** A5, textual (F2.y L10): «`atta PROFILES USE` recibe ask pero el CLI lo rechaza». El guard da `ask` solo a la forma que el CLI acepta.
- **S6** Legibilidad del guard, textual (F2.y L6): «docstring del guard largo, `_NOT_PLAIN_*` casi duplicados, `MAX_SHOWN_COMMAND` sin explicar (explicado ahora), `evaluate()` frente a `decide()` (un resultado no-`None` de `evaluate` no es un allow), helpers de test repetidos». Decisión del usuario: «Refactor sin cambio de comportamiento».
- **S7** Advisories de F4.x L9, textual: «expansiones de shell multilínea sin comillas, `//sys` de Go». Decisión del usuario: «Expansiones de shell multilínea y `//sys` de Go cuentan como código para `untested`.»
- **S8** Reglas vigentes: multiplataforma; solo stdlib; configuración solo por humanos (el guard); mensajes en español con la voz de Atta; README, ayuda y docs al día.

## Tasks

| ID | Specs | Route | Estado | Commit |
|---|---|---|---|---|
| T1 | S1, S2, S3, S4, S8 | Perfiles y modelos: salida de un perfil desconocido, `atta profiles` con valores y origen vigentes, `null`, rechazo sin init (worker A) | pendiente | — |
| T2 | S5, S6, S8 | Guard: `ask` solo para la forma aceptada y refactor de legibilidad sin cambio de comportamiento (worker B); Risk: item 2 → verify | pendiente | — |
| T3 | S7, S8 | `risk.py`: expansiones de shell multilínea y `//sys` de Go cuentan como código (worker C) | pendiente | — |
| T4 | S1-S8 | README y docs | pendiente | — |

## Log

- **L1** (usuario) «implemente los pendientes». Respuestas: A1 «Seguir fallando cerrado, pero con salida (Recomendado)»; A3 «Aceptar null como «sin perfil» (Recomendado)»; A4 «Rechazar y pedir `atta init` (Recomendado)»; alcance «Legibilidad del guard (F2.y L6)» y «Advisories de riesgo de F4.x». Los pendientes 1-5 (revisión, push/PR, sandbox, `develop`, tests rojos de `main`) son del usuario o llegan con el merge de F4.x; F3 T6, F5-F7 quedan fuera.
