# Atta

**Tu colonia de agentes de software.** Atta es una fábrica de software con IA: etiquetas un issue y una colonia de agentes lo evalúa, lo especifica, lo construye, lo prueba, lo revisa y abre el pull request. El nombre viene de *Atta laevigata*, la hormiga culona de Santander, y de las hormigas cortadoras en general: no se comen las hojas, las cortan, las llevan al hormiguero y con ellas cultivan su alimento. Son una fábrica natural con división del trabajo, y así funciona Atta.

> Esta es la primera versión de la identidad, creada desde cero (sin manual previo). Los colores, la tipografía y el logo son una propuesta para iterar.

## La metáfora

| En la colonia | En Atta |
|---|---|
| El hormiguero | El repositorio |
| Las hojas que llegan | Los issues |
| El jardín que se cultiva | El código |
| Los caminos de feromona | El pipeline |

## La colonia: roles de los agentes

| Agente | Rol | Qué hace |
|---|---|---|
| intake | **Exploradora** | Evalúa si el issue vale la pena, lo acota y mide el riesgo |
| spec-writer | **Guía** | Marca el camino: escribe la especificación |
| builder | **Cortadora** | Hace el trabajo pesado: código, commits y PR |
| gate | **Filtro** | Las hojas contaminadas no entran: corre tests, lint y build |
| reviewers (código, UX, UI) | **Jardineras** | Cuidan el detalle y la calidad |
| reviewer de seguridad | **Soldado** | Defiende el hormiguero |
| approver | **Reina** | Decide qué entra a la colonia: aprueba o escala a un humano |

Usa estos nombres en la interfaz, en los mensajes del bot y en la documentación. En código y comandos, el nombre técnico (`intake`, `builder`) sigue siendo válido.

## Voz y tono

Atta habla como un equipo santandereano que trabaja duro y sin rodeos: **directo, cálido y con orgullo de oficio**. Frases cortas, verbos activos, nada de jerga inflada.

- Sí: «La exploradora rechazó el issue #5: pide un minero de criptomonedas.»
- Sí: «Tres rondas sin pasar el filtro. Te lo dejamos a ti.»
- No: «¡Nuestra revolucionaria plataforma de IA potencia tu flujo de trabajo!»

El español es el idioma principal. Los guiños regionales (la hormiga culona, el Chicamocha, el fique) van en la marca y en los nombres, con moderación; el texto funcional se mantiene claro para cualquier hispanohablante.

Slogans aprobados para probar: «Tu colonia de agentes de software», «Software hecho en colonia», «Del issue al merge, como hormiguita». En inglés: «Your AI colony for shipping code».

## Color

La paleta sale de Santander: el rojizo de la hormiga culona, el verde de la hoja que carga, el ocre del cañón del Chicamocha y el crema natural del fique.

- `culona` es el color de marca: botón principal, enlaces, el cuerpo de la hormiga. Encima de él, siempre `sobre-culona`.
- `hoja` es el acento y el color del éxito: PR aprobado, merge hecho, filtro pasado.
- `chicamocha` marca lo que está en curso y sirve para bloques gráficos. En tema claro no es color de texto; úsalo de fondo con `tinta` encima.
- `fondo` y `fondo-elevado` son las superficies; `tinta` y `tinta-suave` el texto.
- `linea` solo para separadores decorativos. Los bordes de inputs y botones secundarios van en `tinta-suave`.

Estados del pipeline: en curso → `chicamocha`; aprobado → `hoja`; necesita humano → `culona`. Acompaña siempre el color con un ícono o una palabra, nunca solo el color.

## Tipografía

- **Bricolage Grotesque** (display): titulares con carácter, un poco artesanal. Solo en `display` y `titulo-1`.
- **IBM Plex Sans** (texto): interfaz y lectura.
- **IBM Plex Mono** (código): comandos, ramas, rutas. Ej.: `/atta run 42`.

Las tres están en Google Fonts.

## Logo

El logo es una hormiga culona cargando una hoja, más el wordmark «atta» en minúsculas, de trazo redondeado y grueso.

- `atta-logo.svg`: versión principal, para fondos claros (`fondo`).
- `atta-logo-oscuro.svg`: para fondos oscuros.
- `atta-marca.svg`: solo la hormiga, para avatares, favicon y espacios pequeños.

Deja alrededor del logo un margen mínimo igual a la altura de la cabeza de la hormiga. No lo rotes, no cambies sus colores fuera de las dos versiones y no lo pongas sobre `chicamocha` ni sobre fotos con mucho detalle.

## Forma y motivo

La forma propia de Atta es **la hoja**: un cuadrado con dos esquinas opuestas redondeadas a `radius-hoja` (24px) y dos rectas. Se usa para avatares de los agentes, badges de estado y patrones gráficos, por ejemplo una fila de hojas en diagonal, como un camino de hormigas. En botones y tarjetas se usa `radius-md`; en chips e inputs, `radius-sm`.

## Qué evitar

- Gradientes azul-morado, brillos «de IA» y robots.
- Hormigas caricaturescas con caras: la hormiga de Atta es una silueta.
- Mezclar más de dos colores de marca en un mismo componente.
