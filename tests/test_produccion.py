"""Producciones y tablero, sin ComfyUI ni saldo."""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from estudio_video import costos, produccion
from estudio_video.produccion import ErrorProduccion, Produccion
from test_panel import ClienteFalso

H3 = {"id": "minimax/hailuo-3", "supported_durations": [5, 6, 8, 10, 15], "supported_frame_images": ["first_frame", "last_frame"],
      "pricing_skus": {"duration_seconds": "0.13", "reference_images": "0.04"}}
CAT = {"videos": [H3], "images": []}
def _png():
    """PNG válido de 2x2 sin dependencias."""
    import struct
    import zlib
    def bloque(tipo, datos):
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos))
    filas = b"".join(b"\x00" + b"\x80\x40\x20" * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + bloque(b"IDAT", zlib.compress(filas)) + bloque(b"IEND", b""))


PNG = _png()


def nueva(carpeta, **cambios):
    produccion.crear(carpeta)
    ruta = Path(carpeta) / "produccion.json"
    datos = json.loads(ruta.read_text())
    datos.update(cambios)
    ruta.write_text(json.dumps(datos))
    return Produccion(carpeta)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.carpeta = Path(self.dir.name) / "corto"


class Formato(Base):
    def test_plantilla_valida_y_genérica(self):
        p = nueva(self.carpeta)
        self.assertEqual(p.titulo, "Mi producción")
        self.assertTrue((self.carpeta / "guion.md").is_file())
        with self.assertRaises(ErrorProduccion):
            produccion.crear(self.carpeta)

    def test_errores_de_estructura(self):
        nueva(self.carpeta)
        ruta = self.carpeta / "produccion.json"
        base = json.loads(ruta.read_text())
        casos = [
            (lambda d: d["secuencias"][0]["tomas"][0]["personajes"].append("NADIE"), "personaje que no existe"),
            (lambda d: d["secuencias"][0]["tomas"][0]["variantes"]["A"].update(primer="9"), "imagen que no existe"),
            (lambda d: d["secuencias"][0]["tomas"].append(dict(d["secuencias"][0]["tomas"][0])), "Toma repetida"),
            (lambda d: d["secuencias"][0]["tomas"][0].pop("duracion"), "duracion"),
            (lambda d: d["secuencias"][0]["tomas"][0].update(id="../x"), "id inválido"),
        ]
        for cambio, texto in casos:
            datos = json.loads(json.dumps(base))
            cambio(datos)
            ruta.write_text(json.dumps(datos))
            with self.assertRaisesRegex(ErrorProduccion, texto):
                Produccion(self.carpeta)

    def test_duracion_ajustada_al_modelo(self):
        p = nueva(self.carpeta)
        self.assertEqual(p.duracion_generada({"duracion": 3.5}, H3), 5)
        self.assertEqual(p.duracion_generada({"duracion": 7}, H3), 8)
        self.assertEqual(p.duracion_generada({"duracion": 20}, H3), 15)
        self.assertEqual(p.duracion_generada({"duracion": 4.2}, None), 5)


class Flujo(Base):
    def test_prompts_llevan_estilo_elenco_y_nota(self):
        p = nueva(self.carpeta, estilo="Paper cut-out", negativo="text")
        prompt = p.prompt_imagen("t01", "1")
        for parte in ("Paper cut-out", "Encuadre de inicio.", "Protagonista:", "Avoid: text"):
            self.assertIn(parte, prompt)
        p.subir_imagen("t01", "1", PNG, ".png")
        with self.assertRaisesRegex(ErrorProduccion, "nota"):
            p.decidir("imagenes", "t01/1", "descartada")
        p.decidir("imagenes", "t01/1", "descartada", "más cerca")
        self.assertIn("DIRECTOR NOTE: más cerca", p.prompt_imagen("t01", "1"))
        self.assertIsNone(p.archivo_imagen("t01", "1"), "lo descartado sale de la toma")
        self.assertEqual(len(list((self.carpeta / "imagenes/anteriores").iterdir())), 1)
        self.assertEqual(p.estado["imagenes"]["t01/1"]["historial"][0]["estado"], "descartada")

    def test_variante_espera_imagenes_aprobadas(self):
        p = nueva(self.carpeta)
        _, _, faltas = p.receta_variante("t01", "B", CAT)
        self.assertEqual(faltas, ["aprobar la imagen 1", "aprobar la imagen 2"])
        for iid in ("1", "2"):
            p.subir_imagen("t01", iid, PNG, ".png")
            p.decidir("imagenes", f"t01/{iid}", "aprobada")
        r, estimado, faltas = p.receta_variante("t01", "B", CAT)
        self.assertEqual(faltas, [])
        self.assertEqual((r["primer_cuadro"], r["ultimo_cuadro"], r["salida"]), ("../imagenes/t01-1.png", "../imagenes/t01-2.png", "../clips"))
        self.assertEqual(estimado, round(0.13 * 5 + 0.04 * 2, 4))

    def test_referencia_nueva_regresa_personaje_a_revision(self):
        p = nueva(self.carpeta)
        p.decidir_personaje("PROTA", "aprobado")
        p.subir_referencia("PROTA", "frontal", PNG, ".png")
        self.assertEqual(p.estado["personajes"]["PROTA"]["estado"], "pendiente")
        with self.assertRaisesRegex(ErrorProduccion, "PNG"):
            p.subir_referencia("PROTA", "frontal", b"x", ".gif")
        with self.assertRaisesRegex(ErrorProduccion, "no es una imagen"):
            p.subir_referencia("PROTA", "frontal", b"<html>", ".png")

    def test_archivo_de_un_agente_entra_como_pendiente(self):
        p = nueva(self.carpeta)
        (self.carpeta / "imagenes").mkdir()
        (self.carpeta / "imagenes/t01-2.webp").write_bytes(PNG)
        v = p.vista(CAT)
        img = v["secuencias"][0]["tomas"][0]["imagenes"][1]
        self.assertEqual((img["estado"], img["archivo"]), ("pendiente", "imagenes/t01-2.webp"))
        self.assertEqual(v["resumen"]["por_revisar"], 1)

    @unittest.skipUnless(shutil.which("ffmpeg"), "necesita ffmpeg")
    def test_animatica_con_clip_imagen_y_negro(self):
        p = nueva(self.carpeta)
        datos = json.loads((self.carpeta / "produccion.json").read_text())
        toma2 = json.loads(json.dumps(datos["secuencias"][0]["tomas"][0]))
        toma3 = dict(toma2, id="t03")
        toma2["id"] = "t02"
        datos["secuencias"][0]["tomas"] += [toma2, toma3]
        (self.carpeta / "produccion.json").write_text(json.dumps(datos))
        p = Produccion(self.carpeta)
        (self.carpeta / "clips").mkdir()
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24", "-t", "6",
                        str(self.carpeta / "clips/t01-A.mp4")], check=True)
        p.decidir("clips", "t01/A", "aprobada")
        p.subir_imagen("t02", "1", PNG, ".png")
        p.decidir("imagenes", "t02/1", "aprobada")
        avisos = []
        final = p.animatica(avisar=avisos.append)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
                                   capture_output=True, text=True).stdout)
        self.assertAlmostEqual(dur, 15, delta=0.4)  # 5 s de cada toma: clip recortado, imagen fija y negro
        self.assertIn("t01-A.mp4", avisos[0])
        self.assertIn("t02-1.png", avisos[1])
        self.assertIn("negro", avisos[2])


class Tablero(Base):
    def setUp(self):
        super().setUp()
        parche = mock.patch.object(costos, "catalogo", return_value={"videos": [dict(H3, id="minimax/hailuo-3", pricing_skus={
            "duration_seconds_with_audio_2k": "0.13"}, supported_durations=[5, 8, 10])], "images": []})
        parche.start()
        self.addCleanup(parche.stop)
        nueva(self.carpeta)
        from estudio_video import panel
        self.cliente = ClienteFalso()
        self.estudio = panel.Estudio(self.carpeta.parent, cliente=self.cliente)
        self.servidor = panel.crear_servidor(self.estudio, 0)
        import threading
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.addCleanup(self.servidor.server_close)
        self.addCleanup(self.servidor.shutdown)
        self.puerto = self.servidor.server_address[1]

    def pedir(self, metodo, ruta, cuerpo=None, crudo=None, tipo="application/json"):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        h = {"Host": f"127.0.0.1:{self.puerto}", "X-Estudio-Token": self.estudio.token}
        datos = crudo if crudo is not None else (None if cuerpo is None else json.dumps(cuerpo).encode())
        if datos is not None:
            h["Content-Type"] = tipo
        c.request(metodo, ruta, datos, h)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def esperar(self):
        for _ in range(200):
            t = self.pedir("GET", "/api/estado")[1]["trabajo"]
            if t["estado"] != "en curso":
                return t
            time.sleep(0.05)
        self.fail("el trabajo no terminó")

    def variante(self, vid="A"):
        v = self.pedir("GET", "/api/produccion?id=corto")[1]
        return next(x for x in v["secuencias"][0]["tomas"][0]["variantes"] if x["id"] == vid), v

    def test_tablero_completo_sin_gastar_de_mas(self):
        estado, datos = self.pedir("GET", "/api/estado")
        self.assertEqual([p["id"] for p in datos["producciones"]], ["corto"])
        self.assertEqual(datos["recetas"], [], "las recetas de la producción no salen como sueltas")
        v, vista = self.variante()
        self.assertEqual(vista["secuencias"][0]["tomas"][0]["imagenes"][0]["archivo"], None)
        # Sin imagen aprobada no se puede generar.
        self.assertEqual(self.pedir("POST", "/api/produccion/generar", {"id": "corto", "toma": "t01", "variante": "A", "huella": v["huella"],
                                                                         "aprobado_usd": 1, "confirmacion": True})[0], 400)
        # Subir y aprobar la imagen desde el panel.
        self.assertEqual(self.pedir("POST", "/api/produccion/subir?id=corto&toma=t01&imagen=1", crudo=PNG, tipo="image/png")[0], 200)
        self.assertEqual(self.pedir("POST", "/api/produccion/subir?id=corto&toma=t01&imagen=1", crudo=b"x", tipo="text/plain")[0], 415)
        self.pedir("POST", "/api/produccion/accion", {"id": "corto", "accion": "aprobar", "grupo": "imagenes", "clave": "t01/1"})
        v, vista = self.variante()
        self.assertEqual(vista["secuencias"][0]["tomas"][0]["imagenes"][0]["archivo"], "corto/imagenes/t01-1.png")
        self.assertEqual((v["faltas"], v["estimado_usd"]), ([], 0.65))
        # Huella vieja o sin aprobación: no se envía nada.
        cuerpo = {"id": "corto", "toma": "t01", "variante": "A", "huella": v["huella"], "aprobado_usd": 0.65}
        self.assertEqual(self.pedir("POST", "/api/produccion/generar", cuerpo)[0], 400)
        self.assertEqual(self.pedir("POST", "/api/produccion/generar", {**cuerpo, "huella": "x", "confirmacion": True})[0], 400)
        self.assertEqual(self.cliente.enviados, 0)
        # Primera versión.
        self.assertEqual(self.pedir("POST", "/api/produccion/generar", {**cuerpo, "confirmacion": True})[0], 202)
        self.assertEqual(self.esperar()["estado"], "listo")
        v, vista = self.variante()
        self.assertEqual((v["archivo"], v["estado"], v["costo_usd"]), ("corto/clips/t01-A.mp4", "pendiente", 0.6))
        self.assertEqual(vista["resumen"]["gastado_usd"], 0.6)
        self.assertTrue((self.carpeta / "recetas/t01-A.json").is_file(), "la variante queda como receta reproducible")
        # Descartar con nota y generar otra versión: la anterior queda archivada.
        self.pedir("POST", "/api/produccion/accion", {"id": "corto", "accion": "descartar", "grupo": "clips", "clave": "t01/A", "nota": "más lento"})
        v, _ = self.variante()
        self.assertIn("DIRECTOR NOTE: más lento", v["prompt_completo"])
        self.pedir("POST", "/api/produccion/generar", {**cuerpo, "huella": v["huella"], "confirmacion": True})
        self.assertEqual(self.esperar()["estado"], "listo")
        v, vista = self.variante()
        self.assertEqual((v["archivo"], v["versiones"]), ("corto/clips/t01-A.mp4", 1))
        self.pedir("POST", "/api/produccion/accion", {"id": "corto", "accion": "aprobar", "grupo": "clips", "clave": "t01/A"})
        self.pedir("POST", "/api/produccion/accion", {"id": "corto", "accion": "corte", "toma": "t01", "variante": "A"})
        v, vista = self.variante()
        self.assertTrue(v["en_corte"])
        self.assertEqual(vista["resumen"]["clips_aprobados"], 1)
        self.assertEqual(self.cliente.enviados, 2)

    def test_editar_variante_desde_el_tablero(self):
        self.pedir("POST", "/api/produccion/accion", {"id": "corto", "accion": "variante", "toma": "t01", "variante": "A",
                                                      "ultimo": "2", "movimiento": "Paneo lento"})
        v, _ = self.variante()
        self.assertEqual((v["ultimo"], v["movimiento"]), ("2", "Paneo lento"))
        estado, datos = self.pedir("POST", "/api/produccion/accion", {"id": "corto", "accion": "variante", "toma": "t01",
                                                                       "variante": "A", "primer": "99"})
        self.assertEqual(estado, 400)
        self.assertEqual(self.pedir("GET", "/api/produccion?id=../otro")[0], 400)


if __name__ == "__main__":
    unittest.main()
