"""Descargas locales: clips con sus ediciones y un ZIP con la historia completa."""
import json
import os
import tempfile
import zipfile
from pathlib import Path

from . import montaje
from .produccion import ErrorProduccion, Produccion, _escribir_json


def _indice(prod):
    ruta = prod.carpeta / "exportaciones" / "indice.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else {}


def _guardar(prod, clave, datos):
    indice = _indice(prod)
    indice[clave] = datos
    _escribir_json(prod.carpeta / "exportaciones" / "indice.json", indice)


def _modo(revision):
    return "revision" if revision else "final"


def _archivo_guardado(prod, guardado):
    relativo = guardado.get("archivo")
    if not isinstance(relativo, str):
        return None
    ruta = (prod.carpeta / relativo).resolve()
    try:
        ruta.relative_to(prod.carpeta / "exportaciones")
    except ValueError:
        return None
    return ruta if ruta.is_file() and ruta.suffix in (".mp4", ".zip") else None


def _huella_clip(prod, toma, revision):
    fuente = prod.carpeta / toma["archivo"] if toma["archivo"] else None
    stat = fuente.stat() if fuente and fuente.is_file() else None
    return prod.huella({"toma": toma, "revision": revision,
                       "fuente": [stat.st_size, stat.st_mtime_ns] if stat else None})


def _huella_paquete(prod, plan, revision):
    guion = prod.carpeta / "guion.md"
    notas = {k: {"nota": v.get("nota"), "correccion_pendiente": v.get("correccion_pendiente")}
             for k, v in prod.estado["clips"].items()}
    return prod.huella({"plan": plan["huella"], "revision": revision, "produccion": prod.datos,
                       "guion": guion.read_text(encoding="utf-8") if guion.is_file() else "", "notas": notas})


def ampliar_vista(prod, datos):
    """Solo consulta: las descargas anteriores quedan visibles, aunque hayan cambiado los ajustes."""
    indice = _indice(prod)
    for revision, nombre in ((False, "montaje"), (True, "montaje_revision")):
        plan = datos[nombre]
        for toma in plan["tomas"]:
            guardado = indice.get(f"clip:{_modo(revision)}:{toma['id']}", {})
            archivo = guardado.get("archivo")
            existe = _archivo_guardado(prod, guardado) is not None
            toma["archivo_editado"] = archivo if existe else None
            # Calcular antes de agregar los propios campos de descarga a la huella.
            original = {k: v for k, v in toma.items() if k not in ("archivo_editado", "editado_actualizado")}
            toma["editado_actualizado"] = bool(existe and guardado.get("huella") == _huella_clip(prod, original, revision))
    # Una producción pendiente ofrece la revisión; la aprobada ofrece el paquete final.
    revision = bool(datos["montaje"]["faltas"])
    plan = datos["montaje_revision" if revision else "montaje"]
    paquete = indice.get(f"paquete:{_modo(revision)}", {})
    ruta = paquete.get("archivo")
    existe = _archivo_guardado(prod, paquete) is not None
    datos.update(paquete=ruta if existe else None, paquete_revision=revision,
                 paquete_actualizado=bool(existe and paquete.get("huella") == _huella_paquete(prod, plan, revision)))
    return datos


def _toma_exportable(prod, tid, revision):
    prod.toma(tid)
    toma = next(t for t in prod.vista_montaje(revision=revision)["tomas"] if t["id"] == tid)
    if toma["omitida"]:
        raise ErrorProduccion("La toma está excluida del montaje; inclúyela antes de exportarla")
    if not toma["archivo"]:
        raise ErrorProduccion("Falta un clip o imagen para revisar" if revision else "Falta un clip aprobado para exportar")
    return toma


def clip_editado(prod, tid, revision=False, avisar=print):
    """Aplica tiempos, fragmentos y silencio. No cambia fuentes ni aprobaciones."""
    toma = _toma_exportable(prod, tid, revision)
    clave = f"clip:{_modo(revision)}:{tid}"
    firma = _huella_clip(prod, toma, revision)
    previo = _indice(prod).get(clave, {})
    guardado = _archivo_guardado(prod, previo)
    if previo.get("huella") == firma and guardado:
        return guardado
    carpeta = prod.carpeta / "exportaciones" / _modo(revision)
    carpeta.mkdir(parents=True, exist_ok=True)
    final = carpeta / f"{tid}-{toma['variante'] or 'imagen'}-editado.mp4"
    entrada = {"id": tid, "archivo": prod.carpeta / toma["archivo"], "duracion": toma["duracion"], "imagen": toma["imagen"],
               "edicion": {k: toma[k] for k in ("inicio", "fin", "omitida", "silenciar", "quitar")}}
    with tempfile.TemporaryDirectory(prefix="clip-", dir=carpeta) as temporal:
        ruta = Path(temporal) / "editado.mp4"
        try:
            resultado = montaje.renderizar([entrada], ruta, 0, avisar=avisar)
        except montaje.ErrorMontaje as exc:
            raise ErrorProduccion(str(exc)) from None
        os.replace(ruta, final)
    _guardar(prod, clave, {"archivo": prod.rel(final), "huella": firma, **resultado})
    return final


def paquete(prod, revision=False, avisar=print):
    """Video completo, clips editados y documentos de trabajo. Todo es local y gratuito."""
    plan = prod.vista_montaje(revision=revision)
    if plan["faltas"]:
        raise ErrorProduccion("Antes de descargar todo: " + "; ".join(plan["faltas"]))
    firma = _huella_paquete(prod, plan, revision)
    clave = f"paquete:{_modo(revision)}"
    anterior = _indice(prod).get(clave, {})
    guardado = _archivo_guardado(prod, anterior)
    if anterior.get("huella") == firma and guardado:
        return guardado
    # Reutiliza el montaje solo si corresponde exactamente a las ediciones actuales.
    nombre_video = "video-revision.mp4" if revision else "video-final.mp4"
    completo = prod.carpeta / "animatica" / nombre_video
    if not plan["actualizado"]:
        completo = prod.montar(avisar=avisar, revision=revision)
    exportados = []
    for toma in plan["tomas"]:
        if not toma["omitida"]:
            ruta = clip_editado(prod, toma["id"], revision=revision, avisar=avisar)
            exportados.append((toma, ruta))
    # Detecta cambios durante la exportación en lugar de entregar un ZIP mezclado.
    vigente = Produccion(prod.carpeta)
    plan_vigente = vigente.vista_montaje(revision=revision)
    if firma != _huella_paquete(vigente, plan_vigente, revision):
        raise ErrorProduccion("La historia cambió durante la exportación; vuelve a preparar la descarga")
    carpeta = prod.carpeta / "exportaciones"
    carpeta.mkdir(exist_ok=True)
    final = carpeta / f"paquete-{_modo(revision)}.zip"
    def datos_toma(toma, ruta):
        estructura = prod.toma(toma["id"])
        vid = toma["variante"] or next(iter(estructura["variantes"]), None)
        nota = prod.estado["clips"].get(f"{toma['id']}/{vid}", {})
        prompt_video = prod.prompt_video(toma["id"], vid) if vid else None
        iid = next((im["id"] for im in estructura["imagenes"]
                    if prod.archivo_imagen(toma["id"], im["id"]) == prod.carpeta / toma["archivo"]), None) if toma["imagen"] else None
        prompt = prod.prompt_imagen(toma["id"], iid) if iid else prompt_video or estructura["descripcion"]
        return {"id": toma["id"], "variante": vid, "imagen_fija": toma["imagen"],
                "archivo_editado": "clips-editados/" + ruta.name,
                "instrucciones_actualizadas": bool(nota.get("instrucciones_actualizadas")),
                "nota_correccion": nota.get("nota"),
                "prompt": prompt, "prompt_video": prompt_video}

    manifiesto = {"titulo": prod.titulo, "revision": revision, "costo_exportacion_usd": 0,
                  "ediciones_aplicadas": True, "produccion": prod.datos, "montaje": prod.estado.get("montaje", {}),
                  "tomas": [datos_toma(t, ruta) for t, ruta in exportados]}
    with tempfile.TemporaryDirectory(prefix="paquete-", dir=carpeta) as temporal:
        ruta_zip = Path(temporal) / "descarga.zip"
        with zipfile.ZipFile(ruta_zip, "w", compression=zipfile.ZIP_STORED) as archivo:
            archivo.write(completo, "video-completo.mp4")
            for toma, ruta in exportados:
                archivo.write(ruta, "clips-editados/" + ruta.name)
            archivo.writestr("proyecto.json", json.dumps(manifiesto, ensure_ascii=False, indent=2))
            guion = prod.carpeta / "guion.md"
            if guion.is_file():
                archivo.write(guion, "guion.md")
            archivo.writestr("LEEME.txt", "Exportacion local sin gasto de OpenRouter.\n"
                             "video-completo.mp4 contiene las tomas unidas y sus transiciones.\n"
                             "clips-editados/ contiene cada toma con recortes y silencio ya aplicados.\n"
                             "proyecto.json conserva prompts y ajustes para trabajar con una persona o agente.\n"
                             "Los tiempos del proyecto se refieren a las fuentes originales: no vuelvas a aplicarlos a los clips editados.\n"
                             "Cambiar prompts no modifica clips existentes; generar otra version necesita estimado y aprobacion.\n"
                             "Si instrucciones_actualizadas es true, el clip conserva una version anterior de la historia.\n"
                             + ("Es una REVISION: hay material pendiente de aprobar y puede haber imagenes fijas.\n" if revision else ""))
        os.replace(ruta_zip, final)
    _guardar(prod, clave, {"archivo": prod.rel(final), "huella": firma})
    avisar(f"Descarga lista, sin gasto de saldo: {prod.rel(final)}")
    return final
