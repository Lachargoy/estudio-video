# Producciones

Una producción organiza un corto o cualquier pieza de varias tomas: guion, personajes, secuencias, tomas, imágenes de cada toma, variantes de video A/B, clips y animática. Sirve para cualquier idea. El tablero (`estudio panel`) lo muestra todo y es donde una persona aprueba y autoriza cada gasto.

```bash
estudio produccion nueva mi-corto --titulo "Mi corto"   # plantilla para editar
estudio produccion estado mi-corto                      # qué falta, con los prompts listos
estudio panel .                                         # tablero en http://127.0.0.1:4300/
```

## Flujo

1. **Idea → estructura.** Escribe `guion.md` y llena `produccion.json`: estilo, personajes, secuencias y tomas. Un agente puede hacerlo a partir de tu idea.
2. **Personajes.** Sube sus vistas (oficial, frontal, lateral…) y apruébalos. Su descripción se agrega a los prompts de cada toma donde aparecen, para mantener su aspecto.
3. **Imágenes de toma.** Cada toma define sus encuadres con un prompt. Haz las imágenes con la herramienta que prefieras y súbelas en el tablero, o guárdalas como `imagenes/<toma>-<imagen>.png`. Entran como «por revisar»; apruébalas o descártalas con una nota.
4. **Video.** Cada variante parte de una imagen aprobada (`primer`) y, si el modelo lo admite, termina en otra (`ultimo`). En el tablero, **Generar…** muestra el estimado, el tope y el saldo, y solo envía si marcas la aprobación. Cada variante queda como receta normal en `recetas/` y se genera con las mismas reglas que `estudio generar`.
5. **Revisión.** Aprueba o descarta cada clip. Descartar pide una nota, que se agrega al prompt de la siguiente versión como `DIRECTOR NOTE`. Elige qué variante va al corte.
6. **Animática.** Corte preliminar en el orden del guion. No gasta saldo.

## Carpeta

```
mi-corto/
  produccion.json        estructura (la editan personas o agentes)
  guion.md               guion literario, opcional
  estado.json            aprobaciones, notas, costos e historial (lo escribe el tablero)
  personajes/<ID>/<vista>.png
  imagenes/<toma>-<imagen>.png      imagenes/anteriores/   versiones previas
  clips/<toma>-<variante>.mp4       clips/anteriores/      clips/registro.jsonl (costos)
  recetas/<toma>-<variante>.json    receta exacta de cada generación
  animatica/animatica.mp4
```

Nada se borra: descartar o reemplazar mueve la versión previa a `anteriores/` y la anota en el historial.

## produccion.json

```json
{
  "titulo": "Mi corto",
  "descripcion": "De qué trata.",
  "estilo": "Técnica, luz, paleta y cámara comunes a todas las tomas.",
  "negativo": "text, watermark",
  "video": {"modelo": "minimax/hailuo-3", "resolucion": "2K", "formato": "16:9", "audio": true, "max_usd_clip": 2.0},
  "personajes": [
    {"id": "PROTA", "nombre": "Protagonista", "descripcion": "Aspecto fijo.", "vistas": ["oficial", "frontal", "lateral"]}
  ],
  "secuencias": [
    {"id": "01", "titulo": "Inicio", "descripcion": "Qué pasa.", "tomas": [
      {"id": "t01", "titulo": "Presentación", "descripcion": "Qué se ve y qué ocurre.", "duracion": 5,
       "personajes": ["PROTA"],
       "imagenes": [{"id": "1", "prompt": "Encuadre de inicio."}, {"id": "2", "prompt": "Encuadre final."}],
       "variantes": {
         "A": {"primer": "1", "ultimo": null, "movimiento": "Movimiento de cámara y acción."},
         "B": {"primer": "1", "ultimo": "2", "movimiento": "De la imagen 1 a la 2."}
       }}
    ]}
  ]
}
```

| Campo | Uso |
|---|---|
| `estilo`, `negativo` | Se agregan a todos los prompts de imagen y de video |
| `video.modelo` | H3 por defecto. Cualquier modelo de video del catálogo (`estudio modelos`) |
| `video.max_usd_clip` | Tope por clip. Si el estimado lo supera, el tablero no deja generar |
| `tomas[].duracion` | Duración en el corte. Se genera la duración admitida por el modelo más cercana por arriba, y la animática recorta |
| `tomas[].personajes` | Elenco de la toma. Sus descripciones entran en el prompt; el elenco no se deduce del texto |
| `variantes.<id>.primer` / `ultimo` | Ids de imágenes de la misma toma. Solo se generan si están aprobadas |

Los ids usan letras, números, `-` o `_`. Las tomas tienen ids únicos en toda la producción.

## Aprobaciones y gasto

- Solo una persona aprueba. El tablero pide marcar «Apruebo gastar hasta $X en esta toma», y un agente no debe usar esas rutas por su cuenta.
- Si la toma, el estilo o la imagen cambian después de que viste el estimado, el envío se rechaza y no se gasta nada.
- Hay una generación a la vez. El costo real de cada clip queda en `clips/registro.jsonl` y se suma en «Gastado en video».

## Limitaciones

- Las imágenes se hacen fuera del tablero (agente, otra herramienta o a mano). El tablero da el prompt completo de cada una.
- No genera audio aparte: el sonido es el que produce el modelo de video (H3 genera ambiente). La animática conserva el audio de cada clip y pone silencio en imágenes fijas.
- Editar guion, tomas y personajes se hace en `produccion.json`. En el tablero solo se cambian el primer y el último cuadro y el movimiento de cada variante.
