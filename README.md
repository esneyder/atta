# Atta

**Tu colonia de agentes de software.** Etiquetas un issue y una colonia de agentes lo evalúa, lo
especifica, lo construye, lo prueba, lo revisa y abre el pull request contra `develop`.

> Estado: F1 en construcción. Ya existe la espina determinista (estados, Filtro, router, política
> de ramas), el CLI `atta` con la hormiga animada y el tablero «el hormiguero». Todavía no habla con
> GitHub ni lanza agentes.

## Requisitos

- Python 3.10 o superior (solo biblioteca estándar).
- `git` reciente (probado con 2.49).
- Windows, macOS o Linux.

## Usar el CLI

Desde la raíz del repo:

| Sistema | Comando |
|---|---|
| Windows (PowerShell o cmd) | `.\atta <comando>` |
| macOS / Linux | `./atta.sh <comando>` |
| Cualquiera | `python atta/bin/atta.py <comando>` |

```text
.\atta create 12 --title "Agregar exportación a CSV"   crea el job y devuelve su id
.\atta status                                          tabla de jobs
.\atta advance <job> <etapa>                           mueve un job (solo transiciones legales)
.\atta route <job>                                     lee intake.md y decide reviewers por riesgo
.\atta gate <job> --worktree <ruta> --base develop     corre el Filtro
.\atta check-pr-base main                              «La Reina no mergea a main: Atta solo integra en develop.»
.\atta --help                                          todos los comandos
```

Códigos de salida: `0` ok, `1` veredicto negativo, `2` rechazado o error de uso.
Los jobs se guardan en `atta/jobs/` (ignorado por git); `--jobs-dir` usa otra carpeta.

### La hormiga en la terminal

Mientras corre una etapa, una hormiga cargando una hoja camina por la línea. No agrega espera.

| Variable | Efecto |
|---|---|
| `ATTA_THEME=light` | paleta para terminales de fondo claro (por defecto: oscura) |
| `ATTA_ASCII=1` | fuerza la versión ASCII |
| `ATTA_NO_ANIM=1` o `NO_COLOR=1` | línea estática, sin animación ni color |

Sin TTY (por ejemplo en CI o con `| tee`) la salida es siempre estática.

## El hormiguero (dashboard)

```text
python atta/bin/board.py                                  jobs de atta/jobs
python atta/bin/board.py --jobs-dir tests/fixtures/jobs   jobs de ejemplo
```

Abre <http://127.0.0.1:8765/>. Es de solo lectura y escucha solo en `127.0.0.1`.

## El Filtro

`atta/checks.json` define los checks del proyecto (en orden, se corta en el primero que falla).
Viene vacío a propósito: sin checks reales el Filtro **falla**. Ejemplo:

```json
{
  "checks": [
    {"name": "tests", "run": "python -m unittest discover -s tests", "shell": {"windows": "pwsh", "default": "bash"}}
  ],
  "timeout_s": 900
}
```

El Filtro también falla si el diff toca rutas protegidas (`atta/**`, `.claude/**`, `.pi/**`,
`.github/workflows/**`) o si el worktree tiene cambios sin commitear.

## Política de ramas

Atta integra siempre en `develop`. `main` está prohibida: nunca crea PR contra `main`, nunca empuja
ni mergea a `main` y nunca despliega. El release `develop → main` es una decisión humana.

## Desarrollo

```text
python -m unittest discover -s tests -v
```

El CI corre la suite en Windows, macOS y Linux con Python 3.10 y 3.12.

## Documentos

- Plan de construcción: [`docs/PLAN.md`](docs/PLAN.md)
- Arquitectura y diseño: [`docs/design/arquitectura.md`](docs/design/arquitectura.md)
- Identidad de marca: [`brand/README.md`](brand/README.md)
- Diseños del hormiguero: [`design/hormiguero/`](design/hormiguero/)

Licencia: por definir.
