"""Producciones: guion, personajes, secuencias y tomas con imágenes, variantes A/B, clips y animática.

Una producción es una carpeta con `produccion.json` (la estructura, la escribe una persona o un agente),
`guion.md` opcional y `estado.json` (aprobaciones, notas e historial, lo escribe el panel). Cada variante
de video se convierte en una receta normal de `estudio` en `recetas/` y se genera con sus mismas reglas.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import costos, receta

ARCHIVO = "produccion.json"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}")
IMAGENES = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_DEFECTO = {"modelo": receta.MODELO_VIDEO, "resolucion": "2K", "formato": "16:9", "audio": True, "max_usd_clip": 2.0}
ESTADOS = ("pendiente", "aprobada", "descartada")


class ErrorProduccion(ValueError):
    pass


def _ahora():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _escribir_json(ruta, datos):
    """Escritura atómica: nunca deja un JSON a medias si algo se interrumpe."""
    ruta = Path(ruta)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, ruta)


def _id(valor, que):
    if not isinstance(valor, str) or not ID.fullmatch(valor):
        raise ErrorProduccion(f"{que}: id inválido {valor!r} (letras, números, - o _)")
    return valor


class Produccion:
    def __init__(self, carpeta):
        self.carpeta = Path(carpeta).resolve()
        ruta = self.carpeta / ARCHIVO
        if not ruta.is_file():
            raise ErrorProduccion(f"No hay {ARCHIVO} en {self.carpeta}")
        self.datos = json.loads(ruta.read_text(encoding="utf-8"))
        self._validar()
        self.estado = self._leer_estado()

    # Estructura ------------------------------------------------------------
    def _validar(self):
        d = self.datos
        if not isinstance(d, dict) or not d.get("titulo"):
            raise ErrorProduccion("La producción necesita «titulo»")
        d["video"] = {**VIDEO_DEFECTO, **(d.get("video") or {})}
        d.setdefault("estilo", "")
        d.setdefault("negativo", "")
        d.setdefault("personajes", [])
        personajes = set()
        for p in d["personajes"]:
            personajes.add(_id(p.get("id"), "personaje"))
            p.setdefault("nombre", p["id"])
            p.setdefault("descripcion", "")
            p.setdefault("vistas", ["oficial"])
        tomas = set()
        if not d.get("secuencias"):
            raise ErrorProduccion("La producción necesita al menos una secuencia con tomas")
        for s in d["secuencias"]:
            _id(s.get("id"), "secuencia")
            s.setdefault("titulo", s["id"])
            for t in s.get("tomas", []):
                tid = _id(t.get("id"), "toma")
                if tid in tomas:
                    raise ErrorProduccion(f"Toma repetida: {tid}")
                tomas.add(tid)
                t.setdefault("titulo", tid)
                t.setdefault("descripcion", "")
                if not isinstance(t.get("duracion"), (int, float)) or t["duracion"] <= 0:
                    raise ErrorProduccion(f"La toma {tid} necesita «duracion» en segundos")
                for pid in t.setdefault("personajes", []):
                    if pid not in personajes:
                        raise ErrorProduccion(f"La toma {tid} usa un personaje que no existe: {pid}")
                imgs = set()
                for im in t.setdefault("imagenes", []):
                    imgs.add(_id(str(im.get("id")), f"imagen de {tid}"))
                    im["id"] = str(im["id"])
                    im.setdefault("prompt", "")
                for vid, v in t.setdefault("variantes", {}).items():
                    _id(vid, f"variante de {tid}")
                    for clave in ("primer", "ultimo"):
                        if v.get(clave) is not None:
                            v[clave] = str(v[clave])
                            if v[clave] not in imgs:
                                raise ErrorProduccion(f"La variante {tid}/{vid} usa una imagen que no existe: {v[clave]}")
                    if v.get("ultimo") and not v.get("primer"):
                        raise ErrorProduccion(f"La variante {tid}/{vid} tiene «ultimo» sin «primer»")
                    v.setdefault("movimiento", "")

    def guardar_estructura(self):
        _escribir_json(self.carpeta / ARCHIVO, self.datos)

    @property
    def titulo(self):
        return self.datos["titulo"]

    def tomas(self):
        for s in self.datos["secuencias"]:
            for t in s.get("tomas", []):
                yield s, t

    def toma(self, tid):
        for _, t in self.tomas():
            if t["id"] == tid:
                return t
        raise ErrorProduccion(f"No existe la toma {tid}")

    def personaje(self, pid):
        for p in self.datos["personajes"]:
            if p["id"] == pid:
                return p
        raise ErrorProduccion(f"No existe el personaje {pid}")

    # Estado ----------------------------------------------------------------
    def _leer_estado(self):
        ruta = self.carpeta / "estado.json"
        base = {"personajes": {}, "imagenes": {}, "clips": {}, "corte": {}, "animatica": None}
        if ruta.is_file():
            base.update(json.loads(ruta.read_text(encoding="utf-8")))
        return base

    def guardar_estado(self):
        _escribir_json(self.carpeta / "estado.json", self.estado)

    # Archivos --------------------------------------------------------------
    def _buscar(self, carpeta, nombre, extensiones):
        for ext in extensiones:
            ruta = self.carpeta / carpeta / f"{nombre}{ext}"
            if ruta.is_file():
                return ruta
        return None

    def archivo_imagen(self, tid, iid):
        return self._buscar("imagenes", f"{tid}-{iid}", IMAGENES)

    def archivo_clip(self, tid, vid):
        return self._buscar("clips", f"{tid}-{vid}", (".mp4", ".webm", ".mov"))

    def archivo_referencia(self, pid, vista):
        return self._buscar(f"personajes/{pid}", vista, IMAGENES)

    def rel(self, ruta):
        return str(Path(ruta).relative_to(self.carpeta)) if ruta else None

    def _estado_pieza(self, grupo, clave, archivo):
        e = self.estado[grupo].get(clave)
        if archivo is None:
            return {"estado": "falta", "nota": (e or {}).get("nota"), "historial": (e or {}).get("historial", [])}
        if not e or e.get("archivo") != self.rel(archivo):
            # Un archivo nuevo (subido por el panel o puesto por un agente) entra como pendiente.
            e = {"estado": "pendiente", "nota": (e or {}).get("nota"), "archivo": self.rel(archivo),
                 "historial": (e or {}).get("historial", []), **({"costo_usd": e.get("costo_usd")} if e and grupo == "clips" else {})}
            self.estado[grupo][clave] = e
        return e

    def _archivar(self, ruta, sub):
        """Mueve una versión anterior a <carpeta>/anteriores/ y devuelve su ruta relativa."""
        destino = ruta.parent / "anteriores"
        destino.mkdir(exist_ok=True)
        marca = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        final = destino / f"{ruta.stem}-{marca}{ruta.suffix}"
        n = 2
        while final.exists():
            final = destino / f"{ruta.stem}-{marca}-{n}{ruta.suffix}"
            n += 1
        shutil.move(ruta, final)
        return self.rel(final)

    def _cerrar_version(self, grupo, clave, archivo):
        e = self.estado[grupo].get(clave)
        if archivo is not None:
            anterior = self._archivar(archivo, grupo)
            if e:
                e.setdefault("historial", []).append({"archivo": anterior, "estado": e.get("estado"), "nota": e.get("nota"),
                                                      "costo_usd": e.get("costo_usd"), "fecha": _ahora()})
        return e

    def subir_imagen(self, tid, iid, datos, ext):
        toma = self.toma(tid)
        if iid not in {im["id"] for im in toma["imagenes"]}:
            raise ErrorProduccion(f"La toma {tid} no tiene la imagen {iid}")
        return self._subir("imagenes", f"{tid}-{iid}", f"{tid}/{iid}", self.archivo_imagen(tid, iid), datos, ext)

    def subir_referencia(self, pid, vista, datos, ext):
        p = self.personaje(pid)
        if vista not in p["vistas"]:
            raise ErrorProduccion(f"{pid} no tiene la vista {vista}")
        ruta = self._subir(f"personajes/{pid}", vista, None, self.archivo_referencia(pid, vista), datos, ext)
        e = self.estado["personajes"].setdefault(pid, {})
        if e.get("estado") == "aprobado":  # una referencia nueva regresa el personaje a provisional
            e["estado"] = "pendiente"
        self.guardar_estado()
        return ruta

    def _subir(self, carpeta, nombre, clave, anterior, datos, ext):
        ext = ext.lower()
        if ext not in IMAGENES:
            raise ErrorProduccion("Solo se aceptan PNG, JPG o WebP")
        if not datos.startswith(FIRMAS[ext]) or (ext == ".webp" and datos[8:12] != b"WEBP"):
            raise ErrorProduccion("El archivo no es una imagen válida del tipo indicado")
        if clave and anterior is not None:
            self._cerrar_version("imagenes", clave, anterior)
        elif anterior is not None:
            self._archivar(anterior, carpeta)
        (self.carpeta / carpeta).mkdir(parents=True, exist_ok=True)
        ruta = self.carpeta / carpeta / f"{nombre}{ext}"
        ruta.write_bytes(datos)
        if clave:
            previo = self.estado["imagenes"].get(clave, {})
            self.estado["imagenes"][clave] = {"estado": "pendiente", "nota": previo.get("nota"), "archivo": self.rel(ruta),
                                              "historial": previo.get("historial", [])}
            self.guardar_estado()
        return ruta

    # Decisiones de una persona --------------------------------------------
    def decidir(self, grupo, clave, decision, nota=None):
        """grupo: imagenes | clips. decision: aprobada | descartada. Descartar pide nota."""
        if grupo not in ("imagenes", "clips") or decision not in ("aprobada", "descartada"):
            raise ErrorProduccion("Decisión no válida")
        tid, pieza = clave.split("/", 1)
        toma = self.toma(tid)
        archivo = self.archivo_imagen(tid, pieza) if grupo == "imagenes" else self.archivo_clip(tid, pieza)
        if grupo == "clips" and pieza not in toma["variantes"]:
            raise ErrorProduccion(f"La toma {tid} no tiene la variante {pieza}")
        if archivo is None:
            raise ErrorProduccion("No hay archivo que revisar")
        if decision == "descartada" and not (nota or "").strip():
            raise ErrorProduccion("Al descartar, escribe una nota de dirección (qué cambiar)")
        e = self._estado_pieza(grupo, clave, archivo)
        e.update(estado=decision, nota=(nota or "").strip() or e.get("nota"), fecha=_ahora())
        if decision == "descartada":
            # Se archiva para que la toma vuelva a la cola; la nota se agrega al prompt siguiente.
            self._cerrar_version(grupo, clave, archivo)
            e.update(estado="falta", archivo=None)
            if grupo == "clips" and self.estado["corte"].get(tid) == pieza:
                self.estado["corte"].pop(tid)
        self.guardar_estado()

    def decidir_personaje(self, pid, decision, nota=None):
        self.personaje(pid)
        if decision not in ("aprobado", "cambios"):
            raise ErrorProduccion("Decisión no válida")
        if decision == "cambios" and not (nota or "").strip():
            raise ErrorProduccion("Escribe qué cambiar")
        self.estado["personajes"][pid] = {"estado": decision, "nota": (nota or "").strip() or None, "fecha": _ahora()}
        self.guardar_estado()

    def elegir_corte(self, tid, vid):
        toma = self.toma(tid)
        if vid not in toma["variantes"]:
            raise ErrorProduccion(f"La toma {tid} no tiene la variante {vid}")
        self.estado["corte"][tid] = vid
        self.guardar_estado()

    def editar_variante(self, tid, vid, primer=None, ultimo=None, movimiento=None):
        toma = self.toma(tid)
        imgs = {im["id"] for im in toma["imagenes"]}
        v = toma["variantes"].setdefault(vid, {"primer": None, "ultimo": None, "movimiento": ""})
        _id(vid, "variante")
        for clave, valor in (("primer", primer), ("ultimo", ultimo)):
            if valor is not None:
                v[clave] = str(valor) or None
                if v[clave] and v[clave] not in imgs:
                    raise ErrorProduccion(f"La toma {tid} no tiene la imagen {valor}")
        if v.get("ultimo") and not v.get("primer"):
            raise ErrorProduccion("«ultimo» requiere «primer»")
        if movimiento is not None:
            v["movimiento"] = str(movimiento)
        self.guardar_estructura()

    # Prompts ----------------------------------------------------------------
    def _elenco(self, toma):
        partes = []
        for pid in toma["personajes"]:
            p = self.personaje(pid)
            partes.append(f"{p['nombre']}: {p['descripcion']}".strip(": "))
        return partes

    def prompt_imagen(self, tid, iid):
        t = self.toma(tid)
        im = next(x for x in t["imagenes"] if x["id"] == iid)
        partes = [self.datos["estilo"], f"Scene: {t['descripcion']}", f"Frame: {im['prompt']}"]
        elenco = self._elenco(t)
        if elenco:
            partes.append("Characters (keep their exact look): " + " | ".join(elenco))
        if self.datos["negativo"]:
            partes.append("Avoid: " + self.datos["negativo"])
        nota = (self.estado["imagenes"].get(f"{tid}/{iid}") or {}).get("nota")
        if nota:
            partes.append("DIRECTOR NOTE: " + nota)
        return "\n".join(x for x in partes if x.strip())

    def prompt_video(self, tid, vid):
        t = self.toma(tid)
        v = t["variantes"][vid]
        partes = [self.datos["estilo"], t["descripcion"], f"Motion: {v['movimiento']}" if v["movimiento"] else ""]
        elenco = self._elenco(t)
        if elenco:
            partes.append("Characters (keep their exact look): " + " | ".join(elenco))
        if self.datos["negativo"]:
            partes.append("Avoid: " + self.datos["negativo"])
        nota = (self.estado["clips"].get(f"{tid}/{vid}") or {}).get("nota")
        if nota:
            partes.append("DIRECTOR NOTE: " + nota)
        return "\n".join(x for x in partes if x.strip())

    # Video ------------------------------------------------------------------
    def duracion_generada(self, toma, modelo=None):
        """La duración admitida por el modelo más cercana por arriba a la de la toma."""
        admitidas = (modelo or {}).get("supported_durations") or []
        pedida = toma["duracion"]
        mayores = [d for d in sorted(admitidas) if d >= pedida]
        if mayores:
            return mayores[0]
        if admitidas:
            return max(admitidas)
        return int(-(-pedida // 1))

    def receta_variante(self, tid, vid, catalogo=None):
        """Receta de estudio para una variante (sin escribirla) y lo que falta para poder generarla."""
        t = self.toma(tid)
        v = t["variantes"][vid]
        video = self.datos["video"]
        modelo = costos.buscar(catalogo, video["modelo"], "video") if catalogo else None
        faltas = []
        r = {"nombre": f"{tid}-{vid}", "modelo": video["modelo"], "prompt": self.prompt_video(tid, vid),
             "duracion": self.duracion_generada(t, modelo), "max_usd": video["max_usd_clip"], "salida": "../clips"}
        for clave in ("resolucion", "formato", "audio"):
            if video.get(clave) is not None:
                r[clave] = video[clave]
        for clave, destino in (("primer", "primer_cuadro"), ("ultimo", "ultimo_cuadro")):
            iid = v.get(clave)
            if not iid:
                continue
            archivo = self.archivo_imagen(tid, iid)
            e = self._estado_pieza("imagenes", f"{tid}/{iid}", archivo)
            if archivo is None or e["estado"] != "aprobada":
                faltas.append(f"aprobar la imagen {iid}")
            elif archivo is not None:
                r[destino] = "../" + self.rel(archivo)
        if modelo and v.get("ultimo") and "last_frame" not in (modelo.get("supported_frame_images") or []):
            faltas.append(f"{video['modelo']} no admite último cuadro")
        estimado = None
        if modelo:
            calculo = {"tipo": "video", **{k: r.get(k) for k in ("duracion", "resolucion", "audio")}, "referencias": [],
                       "primer_cuadro": r.get("primer_cuadro"), "ultimo_cuadro": r.get("ultimo_cuadro")}
            estimado = costos.estimar(calculo, modelo)
        return r, estimado, faltas

    @staticmethod
    def huella(r):
        return hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    def escribir_receta(self, r):
        (self.carpeta / "recetas").mkdir(exist_ok=True)
        ruta = self.carpeta / "recetas" / f"{r['nombre']}.json"
        _escribir_json(ruta, r)
        return ruta

    def registrar_clip(self, tid, vid, resultado):
        """Tras generar: deja el clip con su nombre fijo, archiva el anterior y lo marca pendiente de revisión."""
        nuevos = [Path(p) for p in resultado["archivos"] if Path(p).suffix.lower() in (".mp4", ".webm", ".mov")]
        if not nuevos:
            raise ErrorProduccion("La generación no devolvió un video")
        nuevo = nuevos[0]
        clave = f"{tid}/{vid}"
        fijo = self.carpeta / "clips" / f"{tid}-{vid}{nuevo.suffix}"
        previo = self.estado["clips"].get(clave) or {}
        if nuevo.resolve() != fijo.resolve():
            anterior = self.archivo_clip(tid, vid)
            if anterior is not None and anterior.resolve() != nuevo.resolve():
                self._cerrar_version("clips", clave, anterior)
            shutil.move(nuevo, fijo)
        self.estado["clips"][clave] = {"estado": "pendiente", "nota": previo.get("nota"), "archivo": self.rel(fijo),
                                       "costo_usd": resultado.get("costo_usd"), "fecha": _ahora(),
                                       "historial": (self.estado["clips"].get(clave) or previo).get("historial", [])}
        self.guardar_estado()

    # Resumen ------------------------------------------------------------------
    def gasto(self):
        total = 0.0
        registro = self.carpeta / "clips" / "registro.jsonl"
        if registro.is_file():
            for linea in registro.read_text(encoding="utf-8").splitlines():
                try:
                    total += float(json.loads(linea).get("costo_usd") or 0)
                except ValueError:
                    pass
        return round(total, 4)

    def vista(self, catalogo=None):
        """Todo lo que el panel muestra, ya resuelto: archivos, estados, prompts, estimados y pendientes."""
        imgs_total = imgs_ok = clips_total = clips_ok = 0
        pendientes_revision = 0
        siguiente = []
        personajes = []
        for p in self.datos["personajes"]:
            e = self.estado["personajes"].get(p["id"], {})
            refs = {vista: self.rel(self.archivo_referencia(p["id"], vista)) for vista in p["vistas"]}
            personajes.append({**p, "estado": e.get("estado", "pendiente"), "nota": e.get("nota"), "referencias_archivos": refs})
        secuencias = []
        for s in self.datos["secuencias"]:
            tomas = []
            for t in s.get("tomas", []):
                imagenes = []
                for im in t["imagenes"]:
                    archivo = self.archivo_imagen(t["id"], im["id"])
                    e = self._estado_pieza("imagenes", f"{t['id']}/{im['id']}", archivo)
                    imgs_total += 1
                    imgs_ok += e["estado"] == "aprobada"
                    pendientes_revision += e["estado"] == "pendiente"
                    if e["estado"] == "falta":
                        siguiente.append(f"Imagen {t['id']}/{im['id']}")
                    imagenes.append({**im, "archivo": self.rel(archivo), "estado": e["estado"], "nota": e.get("nota"),
                                     "versiones": len(e.get("historial", [])), "prompt_completo": self.prompt_imagen(t["id"], im["id"])})
                variantes = []
                for vid, v in t["variantes"].items():
                    archivo = self.archivo_clip(t["id"], vid)
                    e = self._estado_pieza("clips", f"{t['id']}/{vid}", archivo)
                    r, estimado, faltas = self.receta_variante(t["id"], vid, catalogo)
                    clips_total += 1
                    clips_ok += e["estado"] == "aprobada"
                    pendientes_revision += e["estado"] == "pendiente"
                    variantes.append({"id": vid, **v, "archivo": self.rel(archivo), "estado": e["estado"], "nota": e.get("nota"),
                                      "costo_usd": e.get("costo_usd"), "versiones": len(e.get("historial", [])),
                                      "historial": e.get("historial", []), "estimado_usd": estimado, "max_usd": r["max_usd"],
                                      "duracion_generada": r["duracion"], "faltas": faltas, "huella": self.huella(r),
                                      "prompt_completo": r["prompt"], "en_corte": self.estado["corte"].get(t["id"]) == vid})
                tomas.append({**{k: t[k] for k in ("id", "titulo", "descripcion", "duracion", "personajes")},
                              "imagenes": imagenes, "variantes": variantes})
            secuencias.append({"id": s["id"], "titulo": s["titulo"], "descripcion": s.get("descripcion", ""), "tomas": tomas})
        guion = self.carpeta / "guion.md"
        self.guardar_estado()  # fija como pendientes los archivos nuevos detectados
        anim = self.carpeta / "animatica" / "animatica.mp4"
        return {
            "titulo": self.titulo, "descripcion": self.datos.get("descripcion", ""), "estilo": self.datos["estilo"],
            "video": self.datos["video"], "guion": guion.read_text(encoding="utf-8") if guion.is_file() else "",
            "personajes": personajes, "secuencias": secuencias,
            "resumen": {"imagenes": imgs_total, "imagenes_aprobadas": imgs_ok, "clips": clips_total, "clips_aprobados": clips_ok,
                        "por_revisar": pendientes_revision, "gastado_usd": self.gasto(),
                        "personajes_aprobados": sum(p["estado"] == "aprobado" for p in personajes)},
            "siguiente": siguiente[:12], "faltan_imagenes": len(siguiente),
            "animatica": self.rel(anim) if anim.is_file() else None,
        }

    # Animática ----------------------------------------------------------------
    def animatica(self, avisar=print):
        """Corte preliminar en orden: clip aprobado (el del corte o el primero), si no imagen aprobada fija, si no negro."""
        if not shutil.which("ffmpeg"):
            raise ErrorProduccion("La animática necesita ffmpeg")
        salida = self.carpeta / "animatica"
        salida.mkdir(exist_ok=True)
        filtro = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p"
        audio = ["-c:a", "aac", "-ar", "48000", "-ac", "2"]
        with tempfile.TemporaryDirectory(prefix="animatica-") as tmp:
            partes = []
            for i, (_, t) in enumerate(self.tomas()):
                seg = Path(tmp) / f"{i:04d}.mp4"
                dur = str(t["duracion"])
                clip = self._clip_del_corte(t)
                imagen = self._imagen_aprobada(t)
                if clip is not None and _tiene_audio(clip):
                    cmd = ["-i", str(clip), "-t", dur, "-vf", filtro, "-map", "0:v:0", "-map", "0:a:0"]
                elif clip is not None:
                    cmd = ["-i", str(clip), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", dur, "-vf", filtro,
                           "-map", "0:v:0", "-map", "1:a:0"]
                elif imagen is not None:
                    cmd = ["-loop", "1", "-i", str(imagen), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", dur,
                           "-vf", filtro, "-map", "0:v:0", "-map", "1:a:0"]
                else:
                    cmd = ["-f", "lavfi", "-i", "color=c=black:s=1280x720:r=24", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                           "-t", dur, "-vf", filtro, "-map", "0:v:0", "-map", "1:a:0"]
                fuente = clip and clip.name or imagen and imagen.name or "negro"
                avisar(f"Toma {t['id']}: {fuente} · {dur} s")
                _ffmpeg(["-y", "-v", "error", *cmd, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                         *audio, "-shortest", str(seg)], f"la toma {t['id']} ({fuente})")
                partes.append(seg)
            lista = Path(tmp) / "lista.txt"
            lista.write_text("".join(f"file '{p}'\n" for p in partes), encoding="utf-8")
            final = salida / "animatica.mp4"
            _ffmpeg(["-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy",
                     "-movflags", "+faststart", str(final)], "la unión final")
        self.estado["animatica"] = {"archivo": self.rel(final), "fecha": _ahora()}
        self.guardar_estado()
        avisar(f"Animática lista: {self.rel(final)}")
        return final

    def _clip_del_corte(self, t):
        orden = list(t["variantes"])
        elegido = self.estado["corte"].get(t["id"])
        if elegido in orden:
            orden.remove(elegido)
            orden.insert(0, elegido)
        for vid in orden:
            archivo = self.archivo_clip(t["id"], vid)
            if archivo is not None and self._estado_pieza("clips", f"{t['id']}/{vid}", archivo)["estado"] == "aprobada":
                return archivo
        return None

    def _imagen_aprobada(self, t):
        for im in t["imagenes"]:
            archivo = self.archivo_imagen(t["id"], im["id"])
            if archivo is not None and self._estado_pieza("imagenes", f"{t['id']}/{im['id']}", archivo)["estado"] == "aprobada":
                return archivo
        return None


def _ffmpeg(args, que, limite=300):
    """ffmpeg sin leer la terminal y con tiempo máximo: una imagen ilegible no debe colgar la animática."""
    try:
        subprocess.run(["ffmpeg", "-nostdin", *args], check=True, capture_output=True, text=True, timeout=limite)
    except subprocess.TimeoutExpired:
        raise ErrorProduccion(f"ffmpeg tardó más de {limite} s en {que}; revisa ese archivo") from None
    except subprocess.CalledProcessError as e:
        raise ErrorProduccion(f"ffmpeg falló en {que}: {(e.stderr or '').strip()[:300]}") from None


FIRMAS = {".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".webp": (b"RIFF",)}


def _tiene_audio(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                        str(video)], capture_output=True, text=True, timeout=60)
    return bool(r.stdout.strip())


def buscar(carpeta, profundidad=3):
    """Carpetas con produccion.json dentro de `carpeta` (incluida ella misma)."""
    carpeta = Path(carpeta).resolve()
    encontradas = []
    for raiz, dirs, archivos in os.walk(carpeta):
        nivel = len(Path(raiz).relative_to(carpeta).parts)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "anteriores", "clips", "imagenes")]
        if nivel >= profundidad:
            dirs[:] = []
        if ARCHIVO in archivos:
            encontradas.append(Path(raiz))
    return sorted(encontradas)


PLANTILLA = {
    "titulo": "Mi producción",
    "descripcion": "De qué trata, en una línea.",
    "estilo": "Describe aquí el estilo visual común a todas las tomas (técnica, luz, paleta, cámara).",
    "negativo": "text, watermark, extra limbs",
    "video": VIDEO_DEFECTO,
    "personajes": [{"id": "PROTA", "nombre": "Protagonista", "descripcion": "Aspecto fijo del personaje: forma, colores, ropa.",
                    "vistas": ["oficial", "frontal", "lateral"]}],
    "secuencias": [{"id": "01", "titulo": "Inicio", "descripcion": "Qué pasa en esta secuencia.", "tomas": [
        {"id": "t01", "titulo": "Presentación", "descripcion": "Qué se ve y qué ocurre en la toma.", "duracion": 5,
         "personajes": ["PROTA"],
         "imagenes": [{"id": "1", "prompt": "Encuadre de inicio."}, {"id": "2", "prompt": "Encuadre final."}],
         "variantes": {"A": {"primer": "1", "ultimo": None, "movimiento": "Movimiento de cámara y acción."},
                       "B": {"primer": "1", "ultimo": "2", "movimiento": "De la imagen 1 a la 2."}}}]}],
}


def crear(carpeta, titulo=None):
    carpeta = Path(carpeta)
    if (carpeta / ARCHIVO).exists():
        raise ErrorProduccion(f"Ya existe {carpeta / ARCHIVO}")
    carpeta.mkdir(parents=True, exist_ok=True)
    datos = json.loads(json.dumps(PLANTILLA))
    if titulo:
        datos["titulo"] = titulo
    _escribir_json(carpeta / ARCHIVO, datos)
    (carpeta / "guion.md").write_text(f"# {datos['titulo']}\n\nEscribe aquí el guion literario.\n", encoding="utf-8")
    return carpeta / ARCHIVO
