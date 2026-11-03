"""Estima el costo con el catálogo que guarda el nodo. Devuelve None si el modelo no cobra por segundo."""
import json
import os
from pathlib import Path


def catalogo(dir_comfy=None):
    base = Path(dir_comfy or os.environ.get("COMFYUI_DIR") or Path.home() / "ComfyUI")
    ruta = base / "user/__openrouter_studio/catalog_cache.json"
    if not ruta.is_file():
        raise FileNotFoundError(f"No encontré el catálogo en {ruta}; define COMFYUI_DIR")
    return json.loads(ruta.read_text(encoding="utf-8"))


def buscar(cat, modelo, tipo="video"):
    for m in cat["videos" if tipo == "video" else "images"]:
        if m.get("id") == modelo:
            return m
    return None


def precio_por_segundo(skus, resolucion, audio):
    r = (resolucion or "").lower()
    candidatos = []
    if audio:
        candidatos += [f"duration_seconds_with_audio_{r}", "duration_seconds_with_audio"]
    else:
        candidatos += [f"duration_seconds_without_audio_{r}", "duration_seconds_without_audio"]
    candidatos += [f"duration_seconds_{r}", "duration_seconds"]
    for c in candidatos:
        if c in skus:
            return float(skus[c])
    for c in (f"cents_per_video_output_second_{r}", f"cents_per_second_output_{r}", "cents_per_second_output"):
        if c in skus:
            return float(skus[c]) / 100
    return None


def estimar(receta, modelo):
    """Tope razonable en USD, contando referencias aunque algunos proveedores no las cobren."""
    if receta["tipo"] != "video" or not modelo:
        return None
    skus = modelo.get("pricing_skus") or {}
    por_segundo = precio_por_segundo(skus, receta.get("resolucion"), receta.get("audio"))
    if por_segundo is None:
        return None
    imagenes = len(receta.get("referencias", [])) + sum(1 for k in ("primer_cuadro", "ultimo_cuadro") if receta.get(k))
    por_imagen = float(skus.get("reference_images", 0)) + float(skus.get("cents_per_image_input", 0)) / 100
    extra = por_imagen * imagenes
    return round(por_segundo * int(receta["duracion"]) + extra, 4)
