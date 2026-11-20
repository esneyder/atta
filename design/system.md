# Sistema de diseño · Atta

Sincronizado desde las fuentes de marca (solo lectura): `brand/tokens.css`, `brand/api/tokens.md`,
`brand/README.md`, `brand/assets/Logos/*.svg`. Solo se registran valores presentes en esas fuentes.
No hay tokens de sombra en la marca: las superficies se separan con `--linea` y `--fondo-elevado`, sin sombras.

Fuentes: las familias de marca van primero en cada pila, sin URLs ni CDNs; si no están instaladas, cae a la
fuente del sistema (`system-ui`, `ui-monospace`).

## Color · tema claro (por defecto)

Selector: `:root, [data-theme="light"]`.

| token | valor | uso | fuente |
| --- | --- | --- | --- |
| `--fondo` | `#f7f1e6` | Fondo de página. Crema de fique. | `brand/tokens.css` |
| `--fondo-elevado` | `#efe5d3` | Tarjetas, paneles y bloques de código sobre fondo. | `brand/tokens.css` |
| `--tinta` | `#24170f` | Texto principal (≥13.9:1). | `brand/tokens.css` |
| `--tinta-suave` | `#6b5444` | Texto secundario, metadatos, bordes de inputs y botones secundarios (≥5.6:1). | `brand/tokens.css` |
| `--culona` | `#a0381a` | Color de marca: botón principal, enlaces, cuerpo de la hormiga. Estado «necesita humano». Texto sobre fondo ≥5.9:1. | `brand/tokens.css` |
| `--sobre-culona` | `#fff7ee` | Texto e íconos encima de culona (≥6.4:1). | `brand/tokens.css` |
| `--hoja` | `#2f6b35` | Acento y éxito: PR aprobado, merge hecho, filtro pasado; la hoja del logo. Texto sobre fondo ≥5.1:1. | `brand/tokens.css` |
| `--chicamocha` | `#d39b2a` | Trabajo en curso y bloques gráficos. En claro NO es texto (2.2:1): fondo con tinta oscura encima (7:1). | `brand/tokens.css` |
| `--linea` | `#dccdb5` | Separadores decorativos de 1px. No es borde de controles. | `brand/tokens.css` |

## Color · tema oscuro

Selector: `[data-theme="dark"]` (columna `dark` de `brand/api/tokens.md`). `--chicamocha` no cambia.

| token | valor | uso | fuente |
| --- | --- | --- | --- |
| `--fondo` | `#17110d` | Fondo de página. Tierra de noche. | `brand/tokens.css` |
| `--fondo-elevado` | `#221913` | Tarjetas y paneles. | `brand/tokens.css` |
| `--tinta` | `#f4eadb` | Texto principal. | `brand/tokens.css` |
| `--tinta-suave` | `#bba894` | Texto secundario y bordes de controles. | `brand/tokens.css` |
| `--culona` | `#e57a52` | Marca, enlaces, «necesita humano». | `brand/tokens.css` |
| `--sobre-culona` | `#1a0f09` | Texto encima de culona. | `brand/tokens.css` |
| `--hoja` | `#7cc283` | Éxito y acento. | `brand/tokens.css` |
| `--chicamocha` | `#d39b2a` | Igual que en claro. | `brand/api/tokens.md` |
| `--linea` | `#3a2c22` | Separadores decorativos. | `brand/tokens.css` |

## Tipografía · familias

| token | valor | uso | fuente |
| --- | --- | --- | --- |
| `--font-display` | `"Bricolage Grotesque", system-ui, sans-serif` | Solo en `display` y `titulo-1`. | `brand/tokens.css` |
| `--font-sans` | `"IBM Plex Sans", system-ui, sans-serif` | Interfaz y lectura. | `brand/tokens.css` |
| `--font-mono` | `"IBM Plex Mono", ui-monospace, monospace` | Comandos, ramas, rutas, ids, costos alineados. | `brand/tokens.css` |

## Tipografía · estilos

| token | valor | uso | fuente |
| --- | --- | --- | --- |
| `--text-display` | `700 64px/64px var(--font-display)` | Titulares de portada; letter-spacing -0.02em. | `brand/tokens.css` |
| `--text-titulo-1` | `700 36px/42px var(--font-display)` | Título de página o cifra principal; letter-spacing -0.01em. | `brand/tokens.css` |
| `--text-titulo-2` | `600 22px/30px var(--font-sans)` | Subtítulos y encabezados de tarjeta. | `brand/tokens.css` |
| `--text-cuerpo` | `400 16px/24px var(--font-sans)` | Texto corrido. | `brand/tokens.css` |
| `--text-pequeno` | `500 13px/18px var(--font-sans)` | Etiquetas, chips, metadatos. | `brand/tokens.css` |
| `--text-codigo` | `400 14px/22px var(--font-mono)` | Comandos, rutas, ramas, código. | `brand/tokens.css` |

## Espaciado

| token | valor | uso | fuente |
| --- | --- | --- | --- |
| `--space-1` | `4px` | Separación entre ícono y texto. | `brand/tokens.css` |
| `--space-2` | `8px` | Padding de chips y etiquetas; gap en listas compactas. | `brand/tokens.css` |
| `--space-4` | `16px` | Padding de botones y tarjetas. | `brand/tokens.css` |
| `--space-6` | `24px` | Gap entre tarjetas; tamaño de la hoja en patrones. | `brand/tokens.css` |
| `--space-8` | `32px` | Separación entre secciones. | `brand/tokens.css` |

## Radio

| token | valor | uso | fuente |
| --- | --- | --- | --- |
| `--radius-sm` | `4px` | Chips, etiquetas, inputs. | `brand/tokens.css` |
| `--radius-md` | `10px` | Botones y tarjetas. | `brand/tokens.css` |
| `--radius-hoja` | `24px` | Forma de hoja: dos esquinas opuestas a 24px y dos rectas (`border-radius: 24px 0`). Avatares de agentes, badges, patrones. | `brand/tokens.css` |

## Sombras

Sin tokens de sombra en `brand/`. No usar sombras; elevar con `--fondo-elevado` y separar con `--linea`.

## Logo y hormiga

Fuente: `brand/assets/Logos/atta-logo.svg`, `atta-logo-oscuro.svg`, `atta-marca.svg` (viewBox `0 0 120 100` para la hormiga).

- Se inyecta el SVG en línea. Sus colores se mapean a tokens, lo que reproduce exactamente las dos versiones
  oficiales: cuerpo y patas `--culona`, hoja `--hoja`, nervaduras `--fondo`, wordmark `--tinta`.
- Margen mínimo: la altura de la cabeza de la hormiga. No se rota (al caminar solo se traslada), no se pone
  sobre `--chicamocha`, no lleva cara: es una silueta.
- Usos con sentido: marcar la etapa activa, indicar carga mientras corre trabajo real, estado vacío. Nunca como relleno.

## Reglas de componentes

Fuente: `brand/README.md` («Color», «Forma y motivo», «Qué evitar»).

- **Estados del pipeline** (siempre color + ícono + palabra, nunca solo color):
  - en curso: fondo `--chicamocha`, texto `#24170f` (= `--tinta` claro, la «tinta oscura» que pide la marca; se fija en ambos temas porque `--chicamocha` no cambia en oscuro: 7:1). Ícono `◐`.
  - aprobado / merge / filtro pasado: fondo `--hoja`, texto `--fondo` (5.7:1 claro, 8.8:1 oscuro). Ícono `✓`.
  - necesita humano: fondo `--culona`, texto `--sobre-culona` (≥6.4:1). Ícono `⚑`.
  - rechazado / cerrado sin merge (estado neutro derivado, sin color de marca): fondo `--fondo-elevado`, texto y borde `--tinta-suave`. Ícono `✕`.
  - ronda fallida (FAIL / CHANGES, vuelve a la Cortadora): borde `--tinta-suave`, texto `--tinta`. Ícono `↻`. `--culona` queda reservado para «necesita humano».
- **Avatares de agentes**: forma de hoja (`border-radius: 24px 0`), fondo `--fondo-elevado`, borde 1px `--tinta-suave`, iniciales del rol en `--tinta`. El agente trabajando ahora: fondo `--chicamocha`, texto `#24170f`.
- **Botón principal**: fondo `--culona`, texto `--sobre-culona`, `--radius-md`. **Secundario**: borde 1px `--tinta-suave`, texto `--tinta`.
- **Chips** e inputs: `--radius-sm`, padding `--space-1`/`--space-2`, estilo `pequeno`. Bordes de controles en `--tinta-suave`, nunca `--linea`.
- **Tarjetas** y paneles: `--fondo-elevado`, `--radius-md`, padding `--space-4`.
- **Foco visible**: `outline: 2px solid var(--tinta); outline-offset: 2px`. Distinto de la selección: un elemento seleccionado lleva borde izquierdo 4px `--tinta` y fondo `--fondo-elevado`.
- **Movimiento**: solo mientras corre trabajo real; con `prefers-reduced-motion: reduce` la hormiga queda quieta y el estado se lee igual por texto.
- **Máximo dos colores de marca por componente.** Sin gradientes azul-morado, brillos «de IA» ni robots.
- **Terminal**: mapeo ANSI de tokens en `design/hormiguero/terminal-frames.json`.
