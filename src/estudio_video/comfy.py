"""Cliente mínimo de la API HTTP de ComfyUI, solo con la biblioteca estándar."""
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class ErrorComfy(RuntimeError):
    pass


class Cliente:
    def __init__(self, base="http://127.0.0.1:8188"):
        self.base = base.rstrip("/")

    def _json(self, ruta, cuerpo=None, cabeceras=None, tiempo=60):
        pet = urllib.request.Request(self.base + ruta, cuerpo, cabeceras or {})
        try:
            with urllib.request.urlopen(pet, timeout=tiempo) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise ErrorComfy(f"{ruta}: HTTP {e.code}: {e.read().decode(errors='replace')[:800]}") from e
        except urllib.error.URLError as e:
            raise ErrorComfy(f"ComfyUI no responde en {self.base}: {e.reason}") from e

    def esquema(self, nodo):
        return self._json(f"/object_info/{nodo}")[nodo]

    def subir_imagen(self, ruta):
        ruta = Path(ruta)
        limite = uuid.uuid4().hex
        tipo = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        nombre = f"estudio_{uuid.uuid4().hex[:8]}{ruta.suffix.lower()}"
        partes = (
            f'--{limite}\r\nContent-Disposition: form-data; name="image"; filename="{nombre}"\r\n'
            f"Content-Type: {tipo}\r\n\r\n"
        ).encode() + ruta.read_bytes() + f"\r\n--{limite}--\r\n".encode()
        r = self._json("/upload/image", partes, {"Content-Type": f"multipart/form-data; boundary={limite}"}, 120)
        return r["name"]

    def encolar(self, grafo):
        cuerpo = json.dumps({"prompt": grafo}).encode()
        return self._json("/prompt", cuerpo, {"Content-Type": "application/json"})["prompt_id"]

    def esperar(self, pid, limite):
        fin = time.time() + limite
        while time.time() < fin:
            h = self._json(f"/history/{pid}")
            if pid in h:
                return h[pid]
            time.sleep(3)
        raise TimeoutError(f"El trabajo {pid} sigue en cola o ejecutándose tras {limite} s")

    def ejecutar(self, grafo, limite):
        return self.esperar(self.encolar(grafo), limite)

    def descargar(self, archivo, destino):
        q = urllib.parse.urlencode({k: archivo.get(k, "") for k in ("filename", "subfolder", "type")})
        with urllib.request.urlopen(f"{self.base}/view?{q}", timeout=300) as r:
            Path(destino).write_bytes(r.read())

    def saldo(self):
        """Saldo disponible de OpenRouter. Puede tardar unos minutos en reflejar el último cobro."""
        grafo = {
            "e": {"class_type": "OpenRouterStudioStatus", "inputs": {"check_credits": True}},
            "s": {"class_type": "PreviewAny", "inputs": {"source": ["e", 3]}},
        }
        r = self.ejecutar(grafo, 60)
        if "s" not in r.get("outputs", {}):
            raise ErrorComfy(mensaje_error(r) or "No se pudo consultar el saldo")
        datos = json.loads(r["outputs"]["s"]["text"][0])["response"]["data"]
        return datos["total_credits"] - datos["total_usage"]


def mensaje_error(resultado):
    for tipo, datos in resultado.get("status", {}).get("messages", []):
        if tipo == "execution_error":
            return (datos.get("exception_message") or "").strip()
    return None
