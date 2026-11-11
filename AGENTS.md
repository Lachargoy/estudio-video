# Instrucciones para agentes

Este repo contiene `estudio`, la herramienta para generar video e imagen con OpenRouter por ComfyUI. Cuando te pidan crear, animar o rehacer una pieza, sigue esto.

## Reglas

1. **Nunca gastes sin aprobación.** Muestra el modelo, la duración y el costo de `estudio estimar`, y espera un sí explícito de la persona para esa pieza. Solo entonces usa `estudio generar receta.json --si`. Aprobar una toma no aprueba las siguientes ni los reintentos que cuesten.
2. **Respeta el presupuesto** que dé la persona y consulta `estudio saldo` antes de empezar. Tras un cobro, el saldo puede tardar minutos en actualizarse.
3. **Nunca leas ni muestres** `credentials.json` ni otra credencial. No escribas claves en recetas ni prompts.
4. **No subas material sensible** de clientes sin confirmar que se puede enviar a terceros.
5. **Sé honesto con la revisión:** no puedes oír el audio; di que solo mediste que hay sonido y pide a una persona que lo escuche.

6. **El panel (`estudio panel`) es para personas.** No llames a sus rutas `/api/generar` ni marques su aprobación por tu cuenta: la aprobación del panel la da quien está frente a la pantalla. Puedes abrirlo y explicar qué muestra.

## Pasos

1. Pregunta lo que falte: historia, duración, formato, audio, dónde se usará y presupuesto.
2. Escribe una receta (ver `README.md` y `ejemplos/`). Pon `max_usd` igual al gasto aprobado para esa toma.
3. Ejecuta `estudio estimar receta.json` y pide aprobación.
4. Ejecuta `estudio generar receta.json --si`. Puede tardar varios minutos: si tu entorno lo permite, córrelo en segundo plano.
5. Ejecuta `estudio revisar <video>` y mira la hoja de cuadros generada. Compárala con lo pedido y reporta las diferencias, aunque el resultado se vea bien.
6. Si algo falla, intenta primero la corrección local gratuita (`estudio blanquear`, retoque de imágenes) y propón otra toma solo con su costo.
7. Para web, ejecuta `estudio web <video> --destino <carpeta> --nombre <nombre>`, y agrega `--blanco` si va sobre una página blanca.
8. Reporta los archivos finales, el costo real y lo que falte revisar.

## Decisiones de receta

- **Usa H3 (`minimax/hailuo-3`) para video:** es el modelo por defecto si la receta no indica otro. Propón otro solo si la persona lo pide o si H3 no sirve para la pieza, por ejemplo cuando el presupuesto no alcanza, y explica por qué.
- Para imágenes indica siempre el modelo. `google/gemini-2.5-flash-image` funcionó bien para editar y crear estados iniciales.
- Si el video debe mostrar una transformación o terminar en una imagen exacta, usa `primer_cuadro` y `ultimo_cuadro`, no `referencias`. Lee `docs/FLUJO.md`.
- Para pasar de 15 s, encadena con `continuar_de` y une con `estudio unir --recortar-inicio 1`. Muestra el costo total de todas las tomas antes de pedir aprobación y revisa cada toma antes de generar la siguiente.
- Los videos de referencia solo funcionan con un enlace HTTPS público (`estudio cola` prepara el fragmento). Nunca subas un fragmento a un sitio público sin aprobación explícita de la persona para ese material.
- Mantén el mismo personaje entre tomas con `referencias` o con el fotograma de la toma anterior.
- Para modelos sin estimado o para imágenes, revisa el precio con `estudio modelos`, avisa a la persona que no hay estimado y usa `--sin-estimado` solo con su aprobación.

## Producciones

- Si la persona trae una idea de varias tomas, propón crear una producción (`estudio produccion nueva`) y ayúdale a llenar `produccion.json` y `guion.md`. Lee `docs/PRODUCCIONES.md`.
- `estudio produccion estado <carpeta>` lista las imágenes que faltan con su prompt completo. Si te piden hacerlas, guárdalas como `imagenes/<toma>-<imagen>.png`; entran como pendientes de revisión.
- No apruebes imágenes, clips ni personajes, y no generes clips desde el tablero: eso lo hace la persona en el panel.

## Si algo falla

- `ComfyUI no responde`: pide a la persona que lo inicie, o inícialo con su script si te lo autorizó.
- `maximum cost` e `in-flight`: lee la tabla en `docs/FLUJO.md`; ninguno cobra.
- Nunca reintentes una toma que ya cobró sin una nueva aprobación.

## Para modificar el código

Python con biblioteca estándar; `numpy` y `scipy` solo en `blanquear`. Pruebas: `PYTHONPATH=src python3 -m unittest discover -s tests`. Las pruebas no deben gastar saldo.
