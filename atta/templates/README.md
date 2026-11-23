# Plantillas de `atta init`

Esta carpeta viaja dentro del paquete `atta` (no vive en tus proyectos). Sus rutas se resuelven
siempre relativas al paquete instalado (`atta.spine.project.TEMPLATES_DIR`), nunca al proyecto.

## Qué hay

| Archivo | Qué es |
|---|---|
| `claude/agents/atta-*.md` | Los 12 agentes de la colonia para Claude Code: Exploradora, Guía, Vigía, Cortadora, las cuatro Jardineras (`code`, `resilience`, `ui`, `ux`), Soldado, Rastreador (revisor adversarial, solo riesgo alto, T3), Refutador (comprueba los hallazgos graves de un `CHANGES` antes de otra ronda) y Reina. El modelo va como `{{model:Tn}}`. Cada uno tiene contrato estructurado y presupuesto de contexto |
| `claude/models.json` | Tier → modelo por runtime. Para `claude-code`: T0 y T1 `haiku`, T2 `sonnet`, T3 `opus` |
| `claude/skills/atta/SKILL.md` | El comando `/atta` (skill de Claude Code): el bucle delgado que ejecuta lo que imprime `atta next`. Corre en el tier barato: `model: {{model:T0}}` (`haiku` por defecto), porque `atta next` ya decide el pipeline |
| `claude/commands/atta.md` | El comando `/atta` para versiones de Claude Code que no registran skills como comandos: apunta al skill. También `model: {{model:T0}}` |
| `claude/settings.json` | El hook PreToolUse `atta guard` para `Write\|Edit\|MultiEdit\|NotebookEdit\|Bash\|PowerShell` (timeout 30 s) |
| `claude/check_templates.py` | Verificador de las plantillas de agentes, solo biblioteca estándar: el juego exacto de agentes, frontmatter (`name`, `description`, `tools`, `model`), `model` = `{{model:Tn}}` con un tier de `models.json`, las herramientas de cada rol, los tokens de su contrato, `## Summary`, `issue.md`, la regla de responder una línea y el presupuesto de contexto (`## Context budget`); las secciones fijas y en orden del `build.md` de la Cortadora y del `spec.md` de la Guía (criterios del issue citados textualmente, `FILES:` obligatorio); el tope de 2 `REVISE` de la Vigía; el alcance acotado y el `## Core checklist` (1 a 12 ítems) de los reviewers; el Rastreador solo en T3 con su contrato adversarial; sin URLs ni referencias a otros proyectos. También exige `model: {{model:T0}}` en el comando y el skill `/atta`. Uso: `python atta/templates/claude/check_templates.py` → «OK: 12 plantillas válidas.» |
| `pr-body.md` | Plantilla del cuerpo del PR (K16). `atta publish` la llena con el `build.md` de la ronda: `{{summary}}` (`## Summary`), `{{changes}}` (`## Changes`, contrastado con `git diff --stat origin/<integración>...HEAD`: lo que falta de un lado o del otro se lista bajo una línea de aviso), `{{tests}}` (`## Tests`), `{{acceptance}}` (`## Acceptance`) e `{{issue}}`; quita el comentario de cabecera. Sin esas secciones usa el texto corto de siempre. `atta init` no la copia: se lee del paquete |

`atta/config.json` y `atta/checks.json` no viven aquí: salen de los valores por defecto del paquete
(`atta/config.json` y el placeholder `atta/checks.json`, que hace fallar el Filtro hasta tener checks
reales).

## Cómo los usa `atta init`

En la raíz del proyecto (`atta/spine/initcmd.py`):

1. Valida todo antes de escribir: si algo es inválido (por ejemplo un `.claude/settings.json` que no
   es JSON), sale con `2` y no cambia nada.
2. Copia cada `claude/agents/atta-*.md` a `.claude/agents/`, resolviendo `{{model:Tn}}` con
   `claude/models.json` para el runtime (`--runtime`, hoy solo `claude-code`). Un tier sin modelo se
   rechaza.
3. Copia `claude/skills/atta/SKILL.md` a `.claude/skills/atta/SKILL.md` y `claude/commands/atta.md`
   a `.claude/commands/atta.md`, resolviendo su `{{model:T0}}` con el T0 del proyecto: el
   orquestador `/atta` corre en el modelo barato. `atta models set T0 <modelo>` (y `reset`) reescribe
   esa línea, igual que la de los agentes; `atta init --check` la reporta si no coincide.
4. `claude/settings.json`: si el proyecto no tiene `.claude/settings.json`, lo crea; si ya existe,
   agrega al final los hooks que falten, sin borrar ni reordenar los del usuario.
5. Crea `atta/config.json` y `atta/checks.json` solo si faltan, y agrega `/atta/jobs/` y
   `/.worktrees/` al `.gitignore`.

Un archivo existente y distinto se deja igual («ya existe, la dejé igual»); `--force` reemplaza solo
los agentes, el skill y el comando, nunca `config.json`, `checks.json` ni `settings.json`. Correrlo dos veces no
cambia nada.

Al editar un agente, corre `check_templates.py` y la suite (`tests/test_templates.py`,
`tests/test_init.py`).
