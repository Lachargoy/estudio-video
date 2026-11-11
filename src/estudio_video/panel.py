"""Panel local de `estudio`: recetas, estimados, saldo, resultados y generación con aprobación de una persona.

Solo escucha en 127.0.0.1. Gasta por una única ruta: POST /api/generar, que exige el token de la página,
la huella de la receta tal como se mostró y el monto aprobado; si la receta cambió o el estimado supera
lo aprobado, no se envía nada. Las reglas de gasto son las mismas del CLI (`generacion.generar`).
"""
import hashlib
import json
import mimetypes
import re
import secrets
import shutil
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import costos, generacion, limpieza, produccion, receta
from .comfy import Cliente, ErrorComfy

MEDIOS = {".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
IGNORAR = {"salidas", "node_modules", ".git", ".venv", "venv", "__pycache__", "web"}
MAX_RECETAS = 500
MAX_SUBIDA = 25 * 1024 * 1024


def huella(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()[:16]


class Estudio:
    """Estado del panel: carpeta de trabajo, catálogo y un único trabajo de generación a la vez."""

    def __init__(self, carpeta, comfy="http://127.0.0.1:8188", generar=None, cliente=None):
        self.carpeta = Path(carpeta).resolve()
        self.comfy = comfy
        self.cliente = cliente or Cliente(comfy)
        self.generar = generar or generacion.generar
        self.trabajo = None
        self.cerrojo = threading.Lock()
        self.token = secrets.token_urlsafe(24)

    # Recetas ---------------------------------------------------------------
    def _candidatas(self):
        producciones = self.producciones_rutas()
        for ruta in sorted(self.carpeta.rglob("*.json")):
            partes = ruta.relative_to(self.carpeta).parts
            if any(p in IGNORAR or p.startswith(".") for p in partes[:-1]):
                continue
            if any(prod == ruta.parent or prod in ruta.parents for prod in producciones):
                continue  # se ven en su producción
            yield ruta

    # Producciones -----------------------------------------------------------
    def producciones_rutas(self):
        return produccion.buscar(self.carpeta)

    def producciones(self):
        lista = []
        for ruta in self.producciones_rutas():
            rel = str(ruta.relative_to(self.carpeta)) or "."
            try:
                lista.append({"id": rel, "titulo": produccion.Produccion(ruta).titulo, "error": None})
            except (produccion.ErrorProduccion, ValueError) as e:
                lista.append({"id": rel, "titulo": rel, "error": str(e)})
        return lista

    def produccion(self, pid):
        ruta = (self.carpeta / pid).resolve()
        if not self._dentro(ruta) or ruta not in self.producciones_rutas():
            raise ValueError("Producción no encontrada")
        return produccion.Produccion(ruta)

    def _catalogo(self):
        try:
            return costos.catalogo()
        except FileNotFoundError:
            return None

    def vista_produccion(self, pid):
        prod = self.produccion(pid)
        datos = prod.vista(self._catalogo())
        base = str(prod.carpeta.relative_to(self.carpeta))
        datos["id"] = pid
        prefijo = "" if base == "." else base + "/"

        def ajustar(x):
            # Rutas relativas a la producción → relativas a la carpeta del panel (las que sirve /archivo).
            if isinstance(x, dict):
                return {k: (prefijo + v if k in ("archivo", "animatica") and isinstance(v, str) else
                            {kk: prefijo + vv if vv else vv for kk, vv in v.items()} if k == "referencias_archivos" else ajustar(v))
                        for k, v in x.items()}
            if isinstance(x, list):
                return [ajustar(v) for v in x]
            return x

        return ajustar(datos)

    def recetas(self):
        try:
            cat = costos.catalogo()
            error_cat = None
        except FileNotFoundError as e:
            cat, error_cat = None, str(e)
        lista = []
        for ruta in self._candidatas():
            if len(lista) >= MAX_RECETAS:
                break
            try:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if not isinstance(datos, dict) or "prompt" not in datos or "nombre" not in datos:
                continue  # no es una receta
            lista.append(self._receta(ruta, datos, cat))
        return {"recetas": lista, "catalogo": error_cat}

    def _receta(self, ruta, datos, cat):
        rel = str(ruta.relative_to(self.carpeta))
        item = {"ruta": rel, "huella": huella(ruta), "nombre": datos.get("nombre"), "tipo": datos.get("tipo", "video"),
                "modelo": datos.get("modelo") or (receta.MODELO_VIDEO if datos.get("tipo", "video") == "video" else None),
                "duracion": datos.get("duracion"), "max_usd": datos.get("max_usd"), "prompt": datos.get("prompt", ""),
                "continuar_de": datos.get("continuar_de"), "estimado_usd": None, "error": None, "espera": None,
                "salidas": [], "gastado_usd": 0.0, "generaciones": 0}
        salida = ruta.parent / datos.get("salida", "salidas")
        try:
            r = receta.cargar(ruta)
            salida = r["salida"]
            if cat is not None:
                item["estimado_usd"] = costos.estimar(r, costos.buscar(cat, r["modelo"], r["tipo"]))
        except receta.ErrorReceta as e:
            texto = str(e)
            if datos.get("continuar_de") and "No existe el archivo" in texto and str(datos["continuar_de"]) in texto.replace("\\", "/"):
                item["espera"] = f"Espera la toma anterior: {datos['continuar_de']}"
            else:
                item["error"] = texto
        item["salidas"] = self._salidas(salida, datos.get("nombre", ""))
        for linea in self._registro(salida):
            if linea.get("nombre") == datos.get("nombre"):
                item["gastado_usd"] += float(linea.get("costo_usd") or 0)
                item["generaciones"] += 1
        item["gastado_usd"] = round(item["gastado_usd"], 4)
        return item

    def _salidas(self, carpeta, nombre):
        if not carpeta.is_dir() or not self._dentro(carpeta):
            return []
        patron = re.compile(rf"{re.escape(nombre)}(-\d+)?(\.hoja)?\.[a-z0-9]+$", re.I)
        archivos = [p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in MEDIOS and patron.fullmatch(p.name)]
        return [{"ruta": str(p.relative_to(self.carpeta)), "nombre": p.name, "bytes": p.stat().st_size,
                 "tipo": "video" if p.suffix.lower() in {".mp4", ".webm", ".mov"} else "imagen",
                 "hoja": p.name.endswith(".hoja.png"), "fecha": p.stat().st_mtime}
                for p in sorted(archivos, key=lambda p: p.stat().st_mtime, reverse=True)]

    def _registro(self, carpeta):
        ruta = carpeta / "registro.jsonl"
        if not ruta.is_file():
            return []
        lineas = []
        for texto in ruta.read_text(encoding="utf-8").splitlines():
            try:
                lineas.append(json.loads(texto))
            except ValueError:
                pass
        return lineas

    def _dentro(self, ruta):
        try:
            Path(ruta).resolve().relative_to(self.carpeta)
            return True
        except ValueError:
            return False

    def archivo(self, rel):
        """Ruta de un medio dentro de la carpeta de trabajo, o None."""
        ruta = (self.carpeta / rel).resolve()
        if not self._dentro(ruta) or not ruta.is_file() or ruta.suffix.lower() not in MEDIOS:
            return None
        return ruta

    def receta_valida(self, rel):
        ruta = (self.carpeta / rel).resolve()
        if not self._dentro(ruta) or ruta.suffix != ".json" or not ruta.is_file():
            return None
        return ruta

    # Generación ------------------------------------------------------------
    def iniciar(self, rel, huella_vista, aprobado_usd, sin_estimado=False):
        ruta = self.receta_valida(rel)
        if ruta is None:
            raise ValueError("Receta no encontrada en la carpeta de trabajo")
        if huella(ruta) != huella_vista:
            raise ValueError("La receta cambió desde que la viste. Recarga el panel y revisa el estimado otra vez.")
        return self._lanzar(rel, aprobado_usd, lambda confirmar, avisar: self.generar(
            ruta, self.cliente, confirmar, sin_estimado=sin_estimado, avisar=avisar), lambda: huella(ruta) == huella_vista,
            sin_estimado)

    def iniciar_variante(self, pid, tid, vid, huella_vista, aprobado_usd):
        prod = self.produccion(pid)
        r, estimado, faltas = prod.receta_variante(tid, vid, self._catalogo())
        if faltas:
            raise ValueError("Antes de generar: " + "; ".join(faltas))
        if prod.huella(r) != huella_vista:
            raise ValueError("La toma cambió desde que la viste. Recarga el panel y revisa el estimado otra vez.")

        def correr(confirmar, avisar):
            vigente = self.produccion(pid)
            r2, _, faltas2 = vigente.receta_variante(tid, vid, self._catalogo())
            if faltas2 or vigente.huella(r2) != huella_vista:
                raise ValueError("La toma cambió antes de enviar; no se envió nada.")
            ruta = vigente.escribir_receta(r2)
            resultado = self.generar(ruta, self.cliente, confirmar, avisar=avisar)
            vigente = self.produccion(pid)
            vigente.registrar_clip(tid, vid, resultado)
            return resultado

        return self._lanzar(f"{pid}: {tid}/{vid}", aprobado_usd, correr, lambda: True, False)

    def iniciar_animatica(self, pid):
        self.produccion(pid)
        return self._lanzar(f"{pid}: animática", 0, lambda confirmar, avisar: {"archivo": str(
            self.produccion(pid).animatica(avisar=avisar))}, lambda: True, True, gasta=False)

    def _lanzar(self, nombre, aprobado_usd, trabajo_fn, vigente, sin_estimado, gasta=True):
        aprobado = float(aprobado_usd or 0)
        rel = nombre
        with self.cerrojo:
            if self.trabajo and self.trabajo["estado"] == "en curso":
                raise ValueError("Ya hay una generación en curso; espera a que termine.")
            trabajo = {"id": secrets.token_hex(4), "receta": rel, "estado": "en curso", "inicio": time.time(), "fin": None,
                       "log": [], "resultado": None, "error": None, "aprobado_usd": aprobado}
            self.trabajo = trabajo

        trabajo["gasta"] = gasta

        def confirmar(r, estimado, disponible):
            # La persona aprobó un monto en el panel: el estimado real no puede superarlo.
            if estimado is not None and estimado > aprobado + 1e-9:
                trabajo["log"].append(f"El estimado ${estimado:.2f} supera lo aprobado (${aprobado:.2f}).")
                return False
            if estimado is None and not sin_estimado:
                return False
            return vigente()

        def correr():
            try:
                trabajo["resultado"] = trabajo_fn(confirmar, trabajo["log"].append)
                trabajo["estado"] = "listo"
            except Exception as e:  # el panel debe mostrar cualquier fallo, no morir
                trabajo["estado"], trabajo["error"] = "error", str(e)
            finally:
                trabajo["fin"] = time.time()

        threading.Thread(target=correr, daemon=True).start()
        return trabajo


def crear_manejador(estudio, pagina):
    class Manejador(BaseHTTPRequestHandler):
        server_version = "estudio-panel"

        def log_message(self, *args):
            return None

        def enviar(self, estado, cuerpo, tipo):
            self.send_response(estado)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(cuerpo)

        def json(self, datos, estado=HTTPStatus.OK):
            self.enviar(estado, json.dumps(datos, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def error(self, estado, texto):
            self.json({"error": texto}, estado)

        def host_local(self):
            # Evita DNS rebinding: un sitio ajeno que resuelva a 127.0.0.1 manda su propio nombre.
            return self.headers.get("Host", "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

        def peticion_propia(self):
            origen = self.headers.get("Origin")
            if origen and urlparse(origen).hostname not in ("127.0.0.1", "localhost"):
                return False
            return secrets.compare_digest(self.headers.get("X-Estudio-Token", ""), estudio.token)

        def cuerpo(self, limite=64 * 1024, crudo=False):
            largo = int(self.headers.get("Content-Length") or 0)
            if largo > limite:
                raise ValueError("Petición demasiado grande")
            datos = self.rfile.read(largo)
            return datos if crudo else json.loads(datos or b"{}")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.host_local():
                return self.error(HTTPStatus.FORBIDDEN, "Solo se acepta 127.0.0.1 o localhost")
            url = urlparse(self.path)
            if url.path == "/":
                return self.enviar(HTTPStatus.OK, pagina.replace(b"__TOKEN__", estudio.token.encode()), "text/html; charset=utf-8")
            if url.path == "/api/estado":
                datos = estudio.recetas()
                datos.update(carpeta=str(estudio.carpeta), comfy=estudio.comfy, trabajo=estudio.trabajo,
                             ffmpeg=bool(shutil.which("ffmpeg")), producciones=estudio.producciones())
                return self.json(datos)
            if url.path == "/api/produccion":
                try:
                    return self.json(estudio.vista_produccion(parse_qs(url.query).get("id", [""])[0]))
                except (ValueError, produccion.ErrorProduccion) as e:
                    return self.error(HTTPStatus.BAD_REQUEST, str(e))
            if url.path == "/archivo":
                ruta = estudio.archivo(parse_qs(url.query).get("ruta", [""])[0])
                if ruta is None:
                    return self.error(HTTPStatus.NOT_FOUND, "Archivo no disponible")
                return self.enviar_archivo(ruta)
            return self.error(HTTPStatus.NOT_FOUND, "No existe")

        def enviar_archivo(self, ruta):
            tam = ruta.stat().st_size
            inicio, fin, estado = 0, tam - 1, HTTPStatus.OK
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if m and tam and (m[1] or m[2]):
                if m[1]:
                    inicio, fin = int(m[1]), min(int(m[2]), tam - 1) if m[2] else tam - 1
                else:
                    inicio = max(0, tam - int(m[2]))
                if inicio > fin:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{tam}")
                    self.end_headers()
                    return
                estado = HTTPStatus.PARTIAL_CONTENT
            self.send_response(estado)
            self.send_header("Content-Type", mimetypes.guess_type(ruta.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(fin - inicio + 1 if tam else 0))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if estado == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {inicio}-{fin}/{tam}")
            self.end_headers()
            if self.command == "HEAD" or not tam:
                return
            with open(ruta, "rb") as f:
                f.seek(inicio)
                falta = fin - inicio + 1
                while falta > 0:
                    trozo = f.read(min(1 << 20, falta))
                    if not trozo:
                        break
                    self.wfile.write(trozo)
                    falta -= len(trozo)

        def do_POST(self):
            if not self.host_local() or not self.peticion_propia():
                return self.error(HTTPStatus.FORBIDDEN, "Petición no autorizada")
            try:
                url = urlparse(self.path)
                ruta = url.path
                if ruta == "/api/produccion/subir":
                    q = {k: v[0] for k, v in parse_qs(url.query).items()}
                    prod = estudio.produccion(q.get("id", ""))
                    datos = self.cuerpo(MAX_SUBIDA, crudo=True)
                    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(self.headers.get("Content-Type", ""))
                    if not ext:
                        return self.error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Solo PNG, JPG o WebP")
                    if q.get("personaje"):
                        archivo = prod.subir_referencia(q["personaje"], q.get("vista", ""), datos, ext)
                    else:
                        archivo = prod.subir_imagen(q.get("toma", ""), q.get("imagen", ""), datos, ext)
                    return self.json({"archivo": prod.rel(archivo)})
                datos = self.cuerpo()
                if ruta == "/api/produccion/accion":
                    prod = estudio.produccion(datos.get("id", ""))
                    accion = datos.get("accion")
                    if accion in ("aprobar", "descartar"):
                        prod.decidir(datos.get("grupo"), datos.get("clave", ""), "aprobada" if accion == "aprobar" else "descartada",
                                     datos.get("nota"))
                    elif accion in ("aprobar_personaje", "cambios_personaje"):
                        prod.decidir_personaje(datos.get("personaje", ""), "aprobado" if accion == "aprobar_personaje" else "cambios",
                                               datos.get("nota"))
                    elif accion == "corte":
                        prod.elegir_corte(datos.get("toma", ""), datos.get("variante", ""))
                    elif accion == "variante":
                        prod.editar_variante(datos.get("toma", ""), datos.get("variante", ""), datos.get("primer"),
                                             datos.get("ultimo"), datos.get("movimiento"))
                    else:
                        return self.error(HTTPStatus.BAD_REQUEST, "Acción desconocida")
                    return self.json({"ok": True})
                if ruta == "/api/produccion/generar":
                    if datos.get("confirmacion") is not True:
                        return self.error(HTTPStatus.BAD_REQUEST, "Falta la aprobación de la persona")
                    t = estudio.iniciar_variante(datos.get("id", ""), datos.get("toma", ""), datos.get("variante", ""),
                                                 datos.get("huella", ""), datos.get("aprobado_usd"))
                    return self.json(t, HTTPStatus.ACCEPTED)
                if ruta == "/api/produccion/animatica":
                    return self.json(estudio.iniciar_animatica(datos.get("id", "")), HTTPStatus.ACCEPTED)
                if ruta == "/api/saldo":
                    return self.json({"saldo_usd": estudio.cliente.saldo()})
                if ruta == "/api/generar":
                    if datos.get("confirmacion") is not True:
                        return self.error(HTTPStatus.BAD_REQUEST, "Falta la aprobación de la persona")
                    t = estudio.iniciar(datos.get("receta", ""), datos.get("huella", ""), datos.get("aprobado_usd"),
                                        sin_estimado=datos.get("sin_estimado") is True)
                    return self.json(t, HTTPStatus.ACCEPTED)
                if ruta in ("/api/revisar", "/api/ultimo-cuadro"):
                    video = estudio.archivo(datos.get("archivo", ""))
                    if video is None or video.suffix.lower() not in {".mp4", ".webm", ".mov"}:
                        return self.error(HTTPStatus.NOT_FOUND, "Video no disponible")
                    if not shutil.which("ffmpeg"):
                        return self.error(HTTPStatus.SERVICE_UNAVAILABLE, "Esta acción necesita ffmpeg")
                    if ruta == "/api/revisar":
                        hoja = video.with_suffix(".hoja.png")
                        info, errores, silencios = limpieza.revisar(video, hoja)
                        return self.json({"info": info, "errores": errores, "silencios": silencios,
                                          "hoja": str(hoja.relative_to(estudio.carpeta)),
                                          "aviso": "Falta que una persona escuche el audio y apruebe el resultado."})
                    destino = video.with_name(video.stem + "-ultimo.png")
                    limpieza.ultimo_cuadro(video, destino)
                    return self.json({"imagen": str(destino.relative_to(estudio.carpeta))})
                return self.error(HTTPStatus.NOT_FOUND, "No existe")
            except ErrorComfy as e:
                return self.error(HTTPStatus.BAD_GATEWAY, str(e))
            except (ValueError, TypeError, OSError, KeyError, produccion.ErrorProduccion) as e:
                return self.error(HTTPStatus.BAD_REQUEST, str(e))

    return Manejador


def crear_servidor(estudio, puerto):
    pagina = files("estudio_video").joinpath("static/panel.html").read_bytes()
    return ThreadingHTTPServer(("127.0.0.1", puerto), crear_manejador(estudio, pagina))


def servir(carpeta, puerto=4300, comfy="http://127.0.0.1:8188", abrir=False):
    estudio = Estudio(carpeta, comfy)
    servidor = crear_servidor(estudio, puerto)
    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    print(f"Panel de estudio en {url} (carpeta {estudio.carpeta}). Ctrl+C para cerrar.", flush=True)
    if abrir:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
