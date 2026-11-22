<!--
Plantilla del cuerpo del PR de Atta (K16). La espina reemplaza cada placeholder y quita este
comentario antes de abrir el PR.

Placeholders:
- {{summary}}: el `## Summary` del último `build.md`, en español.
- {{changes}}: una línea por archivo, `path — qué cambió`, sacada de `## Changes` del último
  `build.md`. La lista de archivos debe coincidir exactamente con `git diff --stat <base>...HEAD`
  (ni más ni menos).
- {{tests}}: los comandos corridos y sus líneas finales reales, de `## Tests` del `build.md` y del
  `checks.md` del Filtro.
- {{acceptance}}: cada criterio de aceptación del spec citado textualmente, con ✓/✗ y dónde se
  cubre, de `## Acceptance` del `build.md`.
- {{issue}}: el número del issue, sin «#».
-->
## Resumen

{{summary}}

## Cambios

{{changes}}

## Pruebas

{{tests}}

## Criterios de aceptación

{{acceptance}}

Closes #{{issue}}
