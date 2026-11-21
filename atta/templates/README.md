# Plantillas de `atta init`

Esta carpeta viaja dentro del paquete `atta` (no vive en tus proyectos). `atta init` copia desde aquí
los archivos que cada proyecto necesita:

- `atta/config.json` y `atta/checks.json` del proyecto (hoy, los valores por defecto del paquete:
  `atta/config.json` y el placeholder `atta/checks.json`, que hace fallar el Filtro hasta tener
  checks reales).
- `claude/agents/atta-*.md`: los agentes de la colonia para `.claude/agents/` (con `claude/models.json`
  y su verificador `claude/check_templates.py`).
- El skill `/atta` (`.claude/skills/atta/SKILL.md`) y el resto de lo que `atta init` instala llegan
  con las tareas siguientes de F2.

Las rutas de esta carpeta se resuelven siempre relativas al paquete instalado
(`atta.spine.project.TEMPLATES_DIR`), nunca al proyecto.
