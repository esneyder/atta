# Plantillas de `atta init`

Esta carpeta viaja dentro del paquete `atta` (no vive en tus proyectos). Sus rutas se resuelven
siempre relativas al paquete instalado (`atta.spine.project.TEMPLATES_DIR`), nunca al proyecto.

## Qué hay

| Archivo | Qué es |
|---|---|
| `claude/agents/atta-*.md` | Los 10 agentes de la colonia para Claude Code: Exploradora, Guía, Vigía, Cortadora, las cuatro Jardineras (`code`, `resilience`, `ui`, `ux`), Soldado y Reina. El modelo va como `{{model:Tn}}` |
| `claude/models.json` | Tier → modelo por runtime. Para `claude-code`: T0 y T1 `haiku`, T2 `sonnet`, T3 `opus` |
| `claude/skills/atta/SKILL.md` | El comando `/atta` (skill de Claude Code): el bucle delgado que ejecuta lo que imprime `atta next` |
| `claude/settings.json` | El hook PreToolUse `atta guard` para `Write\|Edit\|MultiEdit\|NotebookEdit\|Bash\|PowerShell` (timeout 30 s) |
| `claude/check_templates.py` | Verificador de las plantillas de agentes, solo biblioteca estándar: el juego exacto de agentes, frontmatter (`name`, `description`, `tools`, `model`), `model` = `{{model:Tn}}` con un tier de `models.json`, las herramientas de cada rol, los tokens de su contrato, `## Summary`, `issue.md` y la regla de responder una línea. Uso: `python atta/templates/claude/check_templates.py` → «OK: 10 plantillas válidas.» |

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
3. Copia `claude/skills/atta/SKILL.md` a `.claude/skills/atta/SKILL.md`.
4. `claude/settings.json`: si el proyecto no tiene `.claude/settings.json`, lo crea; si ya existe,
   agrega al final los hooks que falten, sin borrar ni reordenar los del usuario.
5. Crea `atta/config.json` y `atta/checks.json` solo si faltan, y agrega `/atta/jobs/` y
   `/.worktrees/` al `.gitignore`.

Un archivo existente y distinto se deja igual («ya existe, la dejé igual»); `--force` reemplaza solo
los agentes y el skill, nunca `config.json`, `checks.json` ni `settings.json`. Correrlo dos veces no
cambia nada.

Al editar un agente, corre `check_templates.py` y la suite (`tests/test_templates.py`,
`tests/test_init.py`).
