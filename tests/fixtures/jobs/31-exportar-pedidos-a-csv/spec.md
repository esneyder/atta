STATUS: READY
FILES: src/pedidos/exportar.py, src/pedidos/vista.py, tests/test_exportar.py

## Summary

Tres archivos, cuatro criterios de aceptación y dos caminos de falla: sin pedidos y más de 50 000 filas.

## Failure paths

- Sin pedidos: el CSV sale solo con encabezados.
- Más de 50 000 filas: se exporta por lotes de 500.
