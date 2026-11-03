"""Construye el grafo de ComfyUI a partir del esquema real del nodo, sin fijar parámetros por modelo."""
import json
import time

NODOS = {"video": "OpenRouterStudioVideo", "imagen": "OpenRouterStudioImage"}
MAPA = {"duracion": "duration", "resolucion": "resolution", "formato": "aspect_ratio"}


def _opcion_modelo(esquema, modelo):
    for op in esquema["input"]["required"]["model"][1]["options"]:
        if op["key"] == modelo:
            return op["inputs"]
    raise ValueError(f"El modelo {modelo!r} no está en el catálogo actual; revisa `estudio modelos`")


def _por_defecto(entrada):
    return entrada[1].get("default") if len(entrada) > 1 and isinstance(entrada[1], dict) else None


def _validar(nombre, valor, entrada):
    opciones = entrada[1].get("options") if entrada[0] == "COMBO" else None
    if opciones is not None and valor not in opciones:
        raise ValueError(f"{nombre}={valor!r} no es válido para este modelo; opciones: {opciones}")


def construir(receta, esquema, subidas, nonce=None):
    """`subidas` mapea cada ruta local al nombre con el que ComfyUI la guardó."""
    opcion = _opcion_modelo(esquema, receta["modelo"])
    requeridos, opcionales = opcion.get("required", {}), opcion.get("optional", {})
    entradas = {"model": receta["modelo"]}

    for nombre, entrada in requeridos.items():
        if entrada[0] == "COMFY_AUTOGROW_V3":
            continue
        defecto = _por_defecto(entrada)
        if defecto is not None:
            entradas[f"model.{nombre}"] = defecto
    entradas["model.prompt"] = receta["prompt"]

    for clave, nombre in MAPA.items():
        if receta.get(clave) is None:
            continue
        if nombre not in requeridos:
            raise ValueError(f"El modelo {receta['modelo']} no admite «{clave}»")
        _validar(clave, receta[clave], requeridos[nombre])
        entradas[f"model.{nombre}"] = receta[clave]
    if receta.get("audio") is not None:
        if "generate_audio" not in requeridos:
            raise ValueError(f"El modelo {receta['modelo']} no admite «audio»")
        entradas["model.generate_audio"] = "true" if receta["audio"] else "false"
    for nombre, valor in receta.get("opciones", {}).items():
        if nombre not in requeridos:
            raise ValueError(f"Opción desconocida para {receta['modelo']}: {nombre}")
        _validar(nombre, valor, requeridos[nombre])
        entradas[f"model.{nombre}"] = valor

    for nombre, entrada in esquema["input"]["required"].items():
        if nombre != "model":
            defecto = _por_defecto(entrada)
            if defecto is not None:
                entradas[nombre] = defecto
    if "rerun_nonce" in entradas:
        entradas["rerun_nonce"] = nonce if nonce is not None else int(time.time())

    grafo = {}

    def cargar(id_nodo, ruta):
        grafo[id_nodo] = {"class_type": "LoadImage", "inputs": {"image": subidas[ruta]}}
        return [id_nodo, 0]

    if receta.get("referencias"):
        molde = requeridos.get("reference_images")
        if not molde:
            raise ValueError(f"El modelo {receta['modelo']} no admite imágenes de referencia")
        nombres = molde[1]["template"]["names"]
        if len(receta["referencias"]) > len(nombres):
            raise ValueError(f"Este modelo admite hasta {len(nombres)} referencias")
        for i, ruta in enumerate(receta["referencias"]):
            entradas[f"model.reference_images.{nombres[i]}"] = cargar(f"r{i}", ruta)
    if receta.get("videos_referencia"):
        molde = requeridos.get("reference_videos")
        if not molde:
            raise ValueError(f"El modelo {receta['modelo']} no admite videos de referencia")
        if len(receta["videos_referencia"]) > len(molde[1]["template"]["names"]):
            raise ValueError(f"Este modelo admite hasta {len(molde[1]['template']['names'])} videos de referencia")
        entradas["model.remote_references_json"] = json.dumps(
            [{"type": "video_url", "video_url": {"url": u}} for u in receta["videos_referencia"]])
    for clave, nombre in (("primer_cuadro", "first_frame"), ("ultimo_cuadro", "last_frame")):
        if receta.get(clave):
            if nombre not in opcionales:
                raise ValueError(f"El modelo {receta['modelo']} no admite «{clave}»")
            entradas[f"model.{nombre}"] = cargar(nombre, receta[clave])

    grafo["g"] = {"class_type": NODOS[receta["tipo"]], "inputs": entradas}
    salidas = esquema["output_name"]
    prefijo = f"estudio_{receta['nombre']}"
    if receta["tipo"] == "video":
        grafo["a"] = {"class_type": "SaveVideo", "inputs": {
            "video": ["g", salidas.index("video")], "filename_prefix": prefijo, "format": "auto", "format.codec": "auto"}}
    else:
        grafo["a"] = {"class_type": "SaveImage", "inputs": {"images": ["g", salidas.index("images")], "filename_prefix": prefijo}}
    grafo["m"] = {"class_type": "PreviewAny", "inputs": {"source": ["g", salidas.index("metadata JSON")]}}
    grafo["c"] = {"class_type": "PreviewAny", "inputs": {"source": ["g", salidas.index("actual cost USD")]}}
    return grafo
