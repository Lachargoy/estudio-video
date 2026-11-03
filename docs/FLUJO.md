# El flujo y lo aprendido

Viene de producir dos animaciones de plastilina con voces para la landing de un restaurante.

## Pasos

1. **Definir la pieza:** historia, duración, formato, audio y dónde se va a usar.
2. **Reunir imágenes:** personajes, logo y primer o último cuadro si hacen falta.
3. **Escribir la receta y estimar** con `estudio estimar`.
4. **Pedir aprobación del costo** y generar.
5. **Revisar:** `estudio revisar`, ver la hoja de cuadros y que una persona escuche el audio.
6. **Corregir:** con post-proceso local si se puede, porque es gratis, antes de pagar otra toma.
7. **Preparar para web** con `estudio web`.

## Lecciones

**Referencias contra fotogramas.** Con el logo terminado como referencia, H3 lo mostró armado desde el primer cuadro y nunca se vio la construcción. Para mostrar una transformación, genera una imagen del estado inicial (costó unos $0.04), y usa esa imagen como `primer_cuadro` y la final como `ultimo_cuadro`.

**El fondo blanco no siempre sale blanco.** La escena de la mesa salió gris lavanda aunque el prompt pedía blanco puro. Si la cámara es fija, `estudio blanquear` lo corrige sin costo. Luego `estudio web --blanco` asegura que se funda con una página blanca.

**Retoca primero la imagen final.** Si el video debe terminar en una imagen, sus defectos aparecerán al cierre.

**Los prompts en inglés con estilo y sonido explícitos funcionaron bien.** Para voces: `made-up gibberish language, with no real words`. Evita pedir imitar voces o personajes con marca registrada.

**Una toma cuesta lo mismo aunque salga mal.** No hay seed en H3, así que no se puede repetir exactamente un resultado. Empieza por lo más corto que sirva.

## Errores de OpenRouter

| Mensaje | Qué significa | Qué hacer |
|---|---|---|
| `in-flight requests` | Aún descuenta un cobro anterior. No cobra | La herramienta reintenta cada 60 s |
| `maximum cost exceeds` | Reserva un costo máximo mayor que el precio real. Con H3, 10 s se rechazaron con $2.17 de saldo y 8 s pasaron con el mismo saldo. No cobra | Baja la duración o agrega crédito |

Tras un cobro, el saldo reportado puede tardar unos minutos en actualizarse.

## Costos observados (MiniMax H3, noviembre de 2026)

- $0.13 por segundo, con o sin audio. El catálogo lista $0.04 por imagen de referencia, pero en los cobros que observamos no se aplicó; el estimado la cuenta por prudencia.
- Salida de 2560×1440 o similar, según el primer fotograma.

Los precios cambian: consulta `estudio modelos`.

## Límites

- ComfyUI procesa un trabajo a la vez, así que no conviene compartir una instancia entre varias personas.
- El agente no puede oír el audio: solo mide que haya sonido. Siempre lo debe aprobar una persona.
- Las imágenes salen hacia OpenRouter y el proveedor del modelo. Revisa qué material de clientes se puede subir y las condiciones de uso comercial de cada modelo.
