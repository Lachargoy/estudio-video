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
6. **Montaje.** En **Video final**, revisa la línea de tiempo en el orden del guion. Incluye una variante aprobada por toma, ajusta los tiempos y arma un único MP4 con transiciones suaves. No gasta saldo.
7. **Correcciones.** Recorta o quita fragmentos, excluye tomas y silencia audio desde el editor local. Para cambiar el contenido de una toma, usa **Pedir corrección** y después revisa el estimado antes de generar otra versión. La solicitud conserva el clip actual.

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
  animatica/video-final.mp4
```

Nada se borra: descartar o reemplazar mueve la versión previa a `anteriores/` y la anota en el historial.

## Un video continuo y sus correcciones

El panel muestra primero el montaje de la historia. **Armar video final** exporta todas las tomas incluidas, en el orden de `secuencias[].tomas[]`, a `animatica/video-final.mp4`. También se puede ejecutar:

```bash
estudio produccion montar mi-corto
```

El fundido predeterminado de 0.35 segundos mezcla imagen y audio entre tomas. Se puede ajustar, o poner en cero para usar cortes directos. Los fundidos se solapan, así que reducen la duración total. No sustituyen la continuidad de personajes, movimiento o diálogo de los clips originales: hay que revisar los empalmes y escuchar el audio.

Cada toma tiene un inicio y un final en segundos del video original. **Quitar fragmento** elimina uno o varios intervalos dentro de ese tramo; **Excluir toma** la saca del montaje, y **Silenciar** elimina su audio. Guardar estos cambios conserva los archivos originales y no genera nada en OpenRouter. Si cambias la selección, los tiempos o un archivo fuente después de exportar, el panel marca el video como desactualizado hasta volver a montarlo.

El final necesita un clip aprobado por cada toma incluida. Una variante elegida para el corte se respeta: si no está aprobada, hay que revisarla o elegir otra. No se rellenan huecos con imágenes fijas ni negro en el video final. **Animática** sigue siendo un corte preliminar que sí admite imágenes y huecos para planear la historia.

**Crear revisión continua** permite ver la historia con los clips pendientes, sin aprobarlos. Si falta un clip, usa la imagen de la toma como pausa fija e indica esa diferencia. Guarda `animatica/video-revision.mp4`, separado del final aprobado. También se ejecuta con `estudio produccion revision mi-corto`.

**Pedir corrección** permite escribir instrucciones como «quita el objeto del fondo y conserva el movimiento». La nota se incorpora al prompt de la siguiente versión; no modifica el video actual ni inicia un cobro. Generar esa versión requiere revisar de nuevo modelo, duración, estimado y aprobación. Para eliminar solo los segundos donde aparece algo, usa el recorte local.

## Continuidad por acciones

En **Historia y acciones de esta toma** define el estado inicial, una acción por línea, el estado final y las reglas que deben mantenerse. También puedes escribirlo en `produccion.json`:

```json
"continuidad": {
  "inicio": "Elena sostiene una bolsa opaca cerrada. Amir no ha visto su premio.",
  "acciones": [
    "Amir pregunta dónde está el jaguar; el premio permanece completamente oculto.",
    "Después de la pregunta, Elena abre la bolsa y saca el único peluche.",
    "Elena muestra el premio y responde; Amir reacciona entonces."
  ],
  "fin": "Elena sostiene el peluche visible. Amir ya lo ha visto.",
  "reglas": "Sin peluche visible al inicio. Una sola pregunta y una sola revelación."
}
```

Los prompts incluyen estos estados y pasos en orden, junto al estado final anterior y el inicio siguiente cuando están definidos. No trasladan diálogos ni acciones de otras tomas. El campo `continuidad` en la raíz de la producción admite reglas generales en texto. El primer fotograma debe corresponder al inicio descrito: si ya muestra el premio, prepara otra imagen inicial y apruébala antes de generar. Un prompt nuevo por sí solo no corrige esa imagen ni el clip grabado.

Guardar continuidad conserva las aprobaciones y los originales, pero señala los clips cuyo prompt cambió. **Generar otra versión…** sigue mostrando modelo, duración, estimado y aprobación. Usa **Copiar instrucciones para Codex** para llevar el contexto, los tiempos y las notas a un agente, o edita los mismos JSON con su ayuda.

## Descargar el conjunto o una toma

**Descargar todo editado · gratis** prepara un ZIP local. Cuando termina aparece **Descargar ZIP listo**. Contiene:

- `video-completo.mp4`: historia unida con las transiciones guardadas.
- `clips-editados/`: cada toma incluida con recortes, fragmentos quitados y silencio ya aplicados.
- `proyecto.json`: prompts actuales, configuración, notas y marcas de clips anteriores a cambios de historia.
- `guion.md` y `LEEME.txt`: guion y explicación del contenido, cuando hay guion.

Los tiempos guardados corresponden a las fuentes originales; no vuelvas a aplicarlos sobre los clips ya editados. Los originales no se modifican. Un ZIP de revisión admite pendientes e imágenes fijas y se identifica como revisión; el final requiere clips aprobados. Cuando cambian tiempos, fuentes, historia o notas, el panel avisa que el ZIP anterior necesita actualizarse. Para una sola toma, pulsa **Exportar clip editado · gratis** y descarga el resultado.

```bash
estudio produccion paquete mi-corto --revision   # admite pendientes sin aprobarlos
estudio produccion paquete mi-corto              # exige clips aprobados
```

Exportar y descargar no usan OpenRouter ni gastan saldo. Generar material nuevo conserva su aprobación individual. El ZIP incluye solo los archivos de trabajo indicados y los resultados; no empaqueta credenciales ni otros archivos de la carpeta.

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
- No genera audio aparte: el sonido es el que produce el modelo de video (H3 genera ambiente). El montaje funde el audio entre clips; la animática pone silencio en imágenes fijas. Una persona debe escuchar y revisar el resultado.
- El guion literario y la estructura de secuencias se editan en `guion.md` y `produccion.json`. En el tablero se editan la continuidad por toma, el primer y el último cuadro, el movimiento, las notas y el montaje.
