"""Panel y generación sin ComfyUI ni saldo: un cliente falso reemplaza a OpenRouter."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from estudio_video import costos, generacion, panel
from test_estudio import ESQUEMA

CATALOGO = {"videos": [{"id": "minimax/hailuo-3", "pricing_skus": {"duration_seconds_with_audio_2k": "0.13"}}], "images": []}


class ClienteFalso:
    def __init__(self, saldo=5.0, errores=()):
        self._saldo, self.errores, self.enviados = saldo, list(errores), 0

    def esquema(self, nodo):
        return ESQUEMA

    def saldo(self):
        return self._saldo

    def subir(self, ruta):
        return "subida.png"

    def ejecutar(self, grafo, limite):
        self.enviados += 1
        if self.errores:
            return {"status": {"messages": [["execution_error", {"exception_message": self.errores.pop(0)}]]}}
        return {"outputs": {"a": {"images": [{"filename": "x.mp4"}]}, "c": {"text": ["0.6"]}, "m": {"text": ['{"provider_job_id": "j1"}']}}}

    def descargar(self, archivo, destino):
        Path(destino).write_bytes(b"video")


def escribir_receta(carpeta, nombre="toma-1", **extra):
    datos = {"nombre": nombre, "prompt": "Una escena", "duracion": 5, "resolucion": "2K", "audio": True, "max_usd": 1, **extra}
    ruta = Path(carpeta) / f"{nombre}.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


class ConCatalogo(unittest.TestCase):
    def setUp(self):
        parche = mock.patch.object(costos, "catalogo", return_value=CATALOGO)
        parche.start()
        self.addCleanup(parche.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.carpeta = Path(self.dir.name)


class Generacion(ConCatalogo):
    def test_genera_registra_costo_y_no_pregunta_dos_veces(self):
        ruta, cli, preguntas = escribir_receta(self.carpeta), ClienteFalso(), []
        res = generacion.generar(ruta, cli, lambda r, e, s: preguntas.append(e) or True, avisar=lambda t: None)
        self.assertEqual(preguntas, [0.65])
        self.assertEqual(res["costo_usd"], 0.6)
        registro = (self.carpeta / "salidas/registro.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(registro[0])["trabajo"], "j1")

    def test_tope_saldo_y_cancelacion_no_envian(self):
        cli = ClienteFalso()
        with self.assertRaisesRegex(generacion.ErrorGeneracion, "supera el tope"):
            generacion.generar(escribir_receta(self.carpeta, "cara", max_usd=0.5), cli, lambda *a: True, avisar=lambda t: None)
        with self.assertRaisesRegex(generacion.ErrorGeneracion, "supera el saldo"):
            generacion.generar(escribir_receta(self.carpeta), ClienteFalso(saldo=0.1), lambda *a: True, avisar=lambda t: None)
        with self.assertRaisesRegex(generacion.ErrorGeneracion, "Cancelado"):
            generacion.generar(escribir_receta(self.carpeta), cli, lambda *a: False, avisar=lambda t: None)
        self.assertEqual(cli.enviados, 0)

    def test_reintenta_en_curso_sin_cobro(self):
        cli, esperas = ClienteFalso(errores=["in-flight charge"]), []
        generacion.generar(escribir_receta(self.carpeta), cli, lambda *a: True, avisar=lambda t: None, esperar=esperas.append)
        self.assertEqual((cli.enviados, esperas), (2, [generacion.ESPERA_EN_CURSO]))


class Panel(ConCatalogo):
    def setUp(self):
        super().setUp()
        self.receta = escribir_receta(self.carpeta)
        (self.carpeta / "notas.json").write_text('{"otra": 1}')  # no es receta
        escribir_receta(self.carpeta, "toma-2", continuar_de="salidas/toma-1.mp4")
        self.cliente = ClienteFalso()
        self.estudio = panel.Estudio(self.carpeta, cliente=self.cliente)
        self.servidor = panel.crear_servidor(self.estudio, 0)
        self.puerto = self.servidor.server_address[1]
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.addCleanup(self.servidor.server_close)
        self.addCleanup(self.servidor.shutdown)

    def pedir(self, metodo, ruta, cuerpo=None, host=None, token=True, cabeceras=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        h = {"Host": host or f"127.0.0.1:{self.puerto}", **(cabeceras or {})}
        if token:
            h["X-Estudio-Token"] = self.estudio.token
        datos = None if cuerpo is None else json.dumps(cuerpo).encode()
        if datos is not None:
            h["Content-Type"] = "application/json"
        c.request(metodo, ruta, datos, h)
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())

    def estado(self):
        return json.loads(self.pedir("GET", "/api/estado")[1])

    def test_pagina_lleva_token_y_rechaza_host_ajeno(self):
        estado, cuerpo, _ = self.pedir("GET", "/")
        self.assertEqual(estado, 200)
        self.assertIn(self.estudio.token.encode(), cuerpo)
        self.assertEqual(self.pedir("GET", "/", host="malicioso.example")[0], 403)

    def test_lista_recetas_estimado_y_espera(self):
        recetas = {r["nombre"]: r for r in self.estado()["recetas"]}
        self.assertEqual(set(recetas), {"toma-1", "toma-2"})
        self.assertEqual(recetas["toma-1"]["estimado_usd"], 0.65)
        self.assertIn("Espera la toma anterior", recetas["toma-2"]["espera"])

    def test_acciones_exigen_token_y_origen_propio(self):
        self.assertEqual(self.pedir("POST", "/api/saldo", {}, token=False)[0], 403)
        self.assertEqual(self.pedir("POST", "/api/saldo", {}, cabeceras={"Origin": "https://malicioso.example"})[0], 403)
        self.assertEqual(json.loads(self.pedir("POST", "/api/saldo", {})[1])["saldo_usd"], 5.0)

    def test_generar_requiere_aprobacion_y_huella_vigente(self):
        r = next(x for x in self.estado()["recetas"] if x["nombre"] == "toma-1")
        sin = {"receta": r["ruta"], "huella": r["huella"], "aprobado_usd": 0.65}
        self.assertEqual(self.pedir("POST", "/api/generar", sin)[0], 400)
        self.assertEqual(self.pedir("POST", "/api/generar", {**sin, "huella": "vieja", "confirmacion": True})[0], 400)
        self.assertEqual(self.cliente.enviados, 0)
        self.assertEqual(self.pedir("POST", "/api/generar", {**sin, "confirmacion": True})[0], 202)
        for _ in range(100):
            t = self.estado()["trabajo"]
            if t["estado"] != "en curso":
                break
            time.sleep(0.05)
        self.assertEqual(t["estado"], "listo", t)
        recetas = {x["nombre"]: x for x in self.estado()["recetas"]}
        self.assertEqual(recetas["toma-1"]["gastado_usd"], 0.6)
        self.assertEqual([s["nombre"] for s in recetas["toma-1"]["salidas"]], ["toma-1.mp4"])
        self.assertIsNone(recetas["toma-2"]["espera"], "la toma encadenada ya puede generarse")

    def test_aprobado_menor_que_estimado_no_envia(self):
        r = next(x for x in self.estado()["recetas"] if x["nombre"] == "toma-1")
        self.pedir("POST", "/api/generar", {"receta": r["ruta"], "huella": r["huella"], "aprobado_usd": 0.1, "confirmacion": True})
        for _ in range(100):
            t = self.estado()["trabajo"]
            if t["estado"] != "en curso":
                break
            time.sleep(0.05)
        self.assertEqual(t["estado"], "error")
        self.assertIn("Cancelado", t["error"])
        self.assertEqual(self.cliente.enviados, 0)

    def test_archivos_solo_dentro_de_la_carpeta_y_con_rangos(self):
        (self.carpeta / "salidas").mkdir()
        (self.carpeta / "salidas/toma-1.mp4").write_bytes(b"0123456789")
        estado, cuerpo, cab = self.pedir("GET", "/archivo?ruta=salidas/toma-1.mp4", cabeceras={"Range": "bytes=2-5"})
        self.assertEqual((estado, cuerpo, cab["Content-Range"]), (206, b"2345", "bytes 2-5/10"))
        self.assertEqual(self.pedir("GET", "/archivo?ruta=../../etc/passwd")[0], 404)
        self.assertEqual(self.pedir("GET", "/archivo?ruta=toma-1.json")[0], 404, "solo medios")


if __name__ == "__main__":
    unittest.main()
