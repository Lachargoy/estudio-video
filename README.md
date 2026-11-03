# Estudio de video

Genera video e imagen con modelos de OpenRouter a través de ComfyUI, pensado para pedírselo a un agente (Claude Code o Codex). Cada pieza se describe en una **receta** JSON. La herramienta estima el costo, pide confirmación, genera, descarga el resultado y registra lo que costó.

```
Persona → Agente → estudio (recetas) → ComfyUI + nodo OpenRouter Studio → OpenRouter → proveedor del modelo
```

El video se genera en los servidores del proveedor: no hace falta GPU. ComfyUI solo hace de puente y puede correr en CPU.

## Instalación

Requisitos: Python 3.10+, ffmpeg, ComfyUI con el nodo OpenRouter Studio y una clave de OpenRouter. La guía completa está en [docs/INSTALACION.md](docs/INSTALACION.md).

```bash
git clone https://github.com/Lachargoy/estudio-video.git
cd estudio-video
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[limpieza]"
export COMFYUI_DIR=~/ComfyUI   # carpeta de tu ComfyUI
estudio saldo                  # no gasta; confirma que todo está conectado
```

## Uso

```bash
estudio modelos                         # modelos y precios
estudio estimar receta.json             # costo estimado y saldo, sin gastar
estudio generar receta.json             # muestra el costo y pide confirmación
estudio revisar salidas/pieza.mp4       # datos, silencios y hoja de cuadros
estudio blanquear gris.mp4 blanco.mp4   # fondo gris de cámara fija a blanco
estudio web pieza.mp4 --destino web --nombre pieza --blanco   # MP4, WebM y pósteres
```

## Receta

```json
{
  "nombre": "armar-logo",
  "modelo": "minimax/hailuo-3",
  "prompt": "Las letras se levantan y forman el logo...",
  "duracion": 8,
  "resolucion": "2K",
  "audio": true,
  "primer_cuadro": "imagenes/letras-tiradas.png",
  "ultimo_cuadro": "imagenes/logo-final.png",
  "max_usd": 1.5
}
```

| Clave | Uso |
|---|---|
| `nombre`, `prompt`, `max_usd` | Obligatorias. `max_usd` es el tope: si el estimado lo supera, no se envía nada |
| `modelo` | En video, por defecto `minimax/hailuo-3` (H3). En imagen es obligatorio |
| `tipo` | `video` (por defecto) o `imagen` |
| `duracion` | Obligatoria en video; se valida contra lo que admite el modelo |
| `resolucion`, `formato`, `audio` | Opcionales; se validan contra el modelo |
| `referencias` | Imágenes guía. No se combinan con fotogramas |
| `primer_cuadro`, `ultimo_cuadro` | Fotogramas. Úsalos cuando el video debe terminar en una imagen exacta |
| `salida` | Carpeta de resultados (por defecto `salidas/`) |
| `opciones` | Otros parámetros del modelo, con su nombre real |

Hay recetas de ejemplo en [ejemplos/](ejemplos/) y las lecciones de producción en [docs/FLUJO.md](docs/FLUJO.md).

## Con un agente

- **Claude Code:** la skill está en `.claude/skills/generar-video/`. Se activa al trabajar dentro de este repo; para usarla en cualquier proyecto, cópiala a `~/.claude/skills/`.
- **Codex y otros:** siguen [AGENTS.md](AGENTS.md).

## Control de gasto

- `generar` nunca envía nada sin confirmación. `--si` existe para que el agente lo use **después** de que la persona apruebe el costo.
- Si el modelo no cobra por segundo no hay estimado, y se exige `--sin-estimado` a propósito. Las imágenes siempre caen en este caso.
- Cada resultado queda en `salidas/registro.jsonl` con su costo real.
- Usa una clave de OpenRouter por persona o proyecto, con límite de crédito propio.

## Probado

- Linux, Python 3.14, ComfyUI 0.43.0 y OpenRouter Studio 0.3.0.
- Con cobro real desde esta herramienta: video con primer fotograma (Grok Imagine Lite) e imagen con referencia (Gemini 2.5 Flash Image).
- MiniMax H3 se usó en producción con el mismo nodo y los mismos parámetros, pero mediante los scripts que dieron origen a esta herramienta.

Sin probar: macOS, Windows y varias personas sobre un mismo ComfyUI.

## Pruebas

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

No gastan saldo ni necesitan ComfyUI.
