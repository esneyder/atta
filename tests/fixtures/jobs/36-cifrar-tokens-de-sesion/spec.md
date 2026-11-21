STATUS: READY
FILES: src/sesiones/almacen.py, src/sesiones/cifrado.py, tests/test_sesiones.py

## Summary

Cifrado autenticado de los tokens con clave leída del entorno; migración en dos pasos sin cerrar sesiones.

## Failure paths

- Falta la clave en el entorno: el servicio no arranca y lo dice.
- Token viejo sin cifrar: se cifra al primer uso.
