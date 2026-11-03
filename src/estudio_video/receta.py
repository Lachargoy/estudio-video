"""Lee y valida las recetas JSON. Las rutas se resuelven respecto al archivo de la receta."""
import json
from pathlib import Path

CLAVES = {
    "nombre", "tipo", "modelo", "prompt", "duracion", "resolucion", "formato", "audio", "referencias",
    "primer_cuadro", "ultimo_cuadro", "max_usd", "salida", "opciones",
    "videos_referencia", "continuar_de",
}


MODELO_VIDEO = "minimax/hailuo-3"


class ErrorReceta(ValueError):
    pass


def cargar(ruta):
    ruta = Path(ruta).resolve()
    r = json.loads(ruta.read_text(encoding="utf-8"))
    desconocidas = set(r) - CLAVES
    if desconocidas:
        raise ErrorReceta(f"Claves desconocidas en la receta: {sorted(desconocidas)}")
    r.setdefault("tipo", "video")
    if r["tipo"] not in ("video", "imagen"):
        raise ErrorReceta("«tipo» debe ser video o imagen")
    if r["tipo"] == "video":
        r.setdefault("modelo", MODELO_VIDEO)
    for clave in ("nombre", "modelo", "prompt", "max_usd"):
        if not r.get(clave):
            raise ErrorReceta(f"Falta «{clave}» en la receta")
    if r["tipo"] == "video" and not r.get("duracion"):
        raise ErrorReceta("Un video necesita «duracion»")
    r["referencias"] = [_ruta(ruta, x) for x in r.get("referencias", [])]
    for clave in ("primer_cuadro", "ultimo_cuadro"):
        if r.get(clave):
            r[clave] = _ruta(ruta, r[clave])
    if r.get("ultimo_cuadro") and not r.get("primer_cuadro"):
        raise ErrorReceta("«ultimo_cuadro» requiere «primer_cuadro»")
    if r.get("referencias") and r.get("primer_cuadro"):
        raise ErrorReceta("No combines «referencias» con fotogramas: el proveedor podría ignorar unos u otros")
    r["videos_referencia"] = r.get("videos_referencia", [])
    for url in r["videos_referencia"]:
        if not str(url).startswith("https://"):
            raise ErrorReceta(
                "OpenRouter solo acepta videos de referencia desde un enlace HTTPS público. "
                "Prepara el fragmento con `estudio cola`, súbelo y pon su enlace."
            )
    if r["videos_referencia"] and r.get("primer_cuadro"):
        raise ErrorReceta("No combines «videos_referencia» con fotogramas: el proveedor podría ignorar unos u otros")
    if r.get("continuar_de"):
        if r["tipo"] != "video":
            raise ErrorReceta("«continuar_de» solo aplica a video")
        r["continuar_de"] = _ruta(ruta, r["continuar_de"])
        if r.get("primer_cuadro"):
            raise ErrorReceta("«continuar_de» ya define el inicio: quita «primer_cuadro»")
        if r["referencias"] or r["videos_referencia"]:
            raise ErrorReceta("«continuar_de» usa fotogramas: no lo combines con referencias")
    r["salida"] = ruta.parent / r.get("salida", "salidas")
    return r


def _ruta(base, valor):
    p = (base.parent / valor).resolve()
    if not p.is_file():
        raise ErrorReceta(f"No existe el archivo: {p}")
    return p
