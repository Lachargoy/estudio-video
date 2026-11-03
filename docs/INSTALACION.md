# Instalación

## 1. ComfyUI

Instala ComfyUI siguiendo [su guía](https://github.com/comfyanonymous/ComfyUI#installing). Esta herramienta se probó con la versión 0.43.0 y con PyTorch solo para CPU. No se necesita GPU ni modelos locales. Arráncalo solo en tu máquina:

```bash
python main.py --cpu --listen 127.0.0.1 --port 8188
```

No lo expongas a la red: ComfyUI no trae autenticación de usuarios, y quien llegue a él gasta con tu clave.

## 2. Nodo OpenRouter Studio

Es un nodo de terceros ([ComfyUI-OpenRouter-Studio](https://github.com/tahabahrami/ComfyUI-OpenRouter-Studio), licencia MIT), que no forma parte de este repo. Se probó la versión 0.3.0. Instálalo desde ComfyUI Manager buscando «OpenRouter Studio», o con el asistente incluido en su paquete:

```bash
python3 scripts/quick_setup.py --comfy-root /ruta/a/ComfyUI
```

Reinicia ComfyUI.

> Antes de desplegarlo en un equipo, revisa el código del nodo y fija su versión. Este proyecto lo usó sin auditarlo.

## 3. Clave de OpenRouter

Crea una en <https://openrouter.ai/settings/keys> con límite de crédito y pégala en la pantalla inicial del nodo, o define `OPENROUTER_API_KEY` en el entorno que arranca ComfyUI.

El nodo guarda la clave sin cifrar en `<ComfyUI>/user/__openrouter_studio/credentials.json`. Nunca la pongas en una receta, un prompt, un workflow ni en este repo.

## 4. Esta herramienta

```bash
pip install -e ".[limpieza]"       # `limpieza` solo hace falta para `blanquear`
export COMFYUI_DIR=/ruta/a/ComfyUI # para leer el catálogo de precios
export COMFYUI_URL=http://127.0.0.1:8188  # opcional
estudio saldo
```

También necesitas `ffmpeg` y `ffprobe` en el PATH.

Si `estudio saldo` muestra tu saldo, ya quedó.
