"""ZIP y clips editados con ffmpeg local: nunca usan un proveedor ni gastan saldo."""
import array
import copy
import http.client
import json
import math
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from estudio_video import costos, exportacion, montaje, panel, produccion
from estudio_video.produccion import ErrorProduccion, Produccion
from test_panel import ClienteFalso
from test_produccion import PNG


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "necesita ffmpeg y ffprobe")
class Exportacion(unittest.TestCase):
    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name).resolve()
        self.carpeta = self.base / "historia"
        produccion.crear(self.carpeta)
        self.ruta = self.carpeta / "produccion.json"
        datos = json.loads(self.ruta.read_text(encoding="utf-8"))
        plantilla = datos["secuencias"][0]["tomas"][0]
        datos["secuencias"][0]["tomas"] = [dict(copy.deepcopy(plantilla), id=f"t0{i}", duracion=2) for i in range(1, 4)]
        self.ruta.write_text(json.dumps(datos), encoding="utf-8")
        self.p = Produccion(self.carpeta)
        self.p.configurar_montaje(fundido=0.25)
        self.p.editar_montaje("t03", omitida=True)

    def clip(self, tid, color="red", aprobado=False):
        carpeta = self.carpeta / "clips"
        carpeta.mkdir(exist_ok=True)
        ruta = carpeta / f"{tid}-A.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color={color}:s=160x120:r=24:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(ruta)], check=True, capture_output=True, timeout=30)
        self.p._estado_pieza("clips", f"{tid}/A", ruta)
        if aprobado:
            self.p.decidir("clips", f"{tid}/A", "aprobada")
        self.p.guardar_estado()
        return ruta

    def preparadas(self):
        return self.clip("t01"), self.clip("t02", "blue")

    def vista(self):
        return exportacion.ampliar_vista(self.p, self.p.vista())

    def duracion(self, ruta):
        return montaje.medir(ruta)["duracion"]

    def rms(self, ruta):
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(ruta), "-vn", "-f", "s16le",
                            "-acodec", "pcm_s16le", "-"], check=True, capture_output=True, timeout=30)
        muestras = array.array("h", r.stdout)
        self.assertTrue(muestras)
        return math.sqrt(sum(n * n for n in muestras) / len(muestras))

    def test_zip_revision_aplica_ediciones_conserva_decisiones_y_excluye_archivos_ajenos(self):
        fuentes = self.preparadas()
        originales = [ruta.read_bytes() for ruta in fuentes]
        self.p.editar_montaje("t01", inicio=0.25, fin=1.75, silenciar=True,
                              quitar=[{"inicio": 0.75, "fin": 1.25}])
        self.p.elegir_corte("t01", "A")
        antes = copy.deepcopy(self.p.estado["clips"])
        (self.carpeta / "notas-privadas.txt").write_text("Archivo ajeno al proyecto de prueba.", encoding="utf-8")
        (self.carpeta / ".env").write_text("SOLO_FIXTURE=true", encoding="utf-8")
        salida = exportacion.paquete(self.p, revision=True, avisar=lambda _: None)
        self.assertEqual(salida.resolve(), (self.carpeta / "exportaciones/paquete-revision.zip").resolve())
        with zipfile.ZipFile(salida) as archivo:
            nombres = set(archivo.namelist())
            self.assertEqual(nombres, {"video-completo.mp4", "clips-editados/t01-A-editado.mp4",
                                       "clips-editados/t02-A-editado.mp4", "proyecto.json", "guion.md", "LEEME.txt"})
            self.assertFalse(any("credentials" in nombre.lower() or nombre.endswith(".env") for nombre in nombres))
            manifiesto = json.loads(archivo.read("proyecto.json"))
            self.assertTrue(manifiesto["revision"])
            self.assertEqual(manifiesto["costo_exportacion_usd"], 0)
            self.assertTrue(manifiesto["ediciones_aplicadas"])
            self.assertEqual([t["id"] for t in manifiesto["tomas"]], ["t01", "t02"])
            self.assertIn("CONTINUITY RULES", manifiesto["tomas"][0]["prompt"])
            for nombre in ("video-completo.mp4", "clips-editados/t01-A-editado.mp4", "clips-editados/t02-A-editado.mp4"):
                destino = self.base / Path(nombre).name
                destino.write_bytes(archivo.read(nombre))
        self.assertAlmostEqual(self.duracion(self.base / "video-completo.mp4"), 2.75, delta=0.15)
        self.assertAlmostEqual(self.duracion(self.base / "t01-A-editado.mp4"), 1, delta=0.15)
        self.assertAlmostEqual(self.duracion(self.base / "t02-A-editado.mp4"), 2, delta=0.15)
        self.assertLess(self.rms(self.base / "t01-A-editado.mp4"), 2)
        self.assertGreater(self.rms(self.base / "t02-A-editado.mp4"), 100)
        self.assertEqual([ruta.read_bytes() for ruta in fuentes], originales)
        self.assertEqual(Produccion(self.carpeta).estado["clips"], antes)
        self.assertEqual(self.p.estado["corte"], {"t01": "A"})

    def test_descarga_editada_queda_obsoleta_tras_editar_y_conserva_version_anterior(self):
        self.preparadas()
        ruta = exportacion.clip_editado(self.p, "t01", revision=True, avisar=lambda _: None)
        original = ruta.read_bytes()
        datos = self.vista()
        toma = datos["montaje_revision"]["tomas"][0]
        self.assertTrue(toma["editado_actualizado"])
        self.assertEqual(toma["archivo_editado"], self.p.rel(ruta))
        self.assertTrue(exportacion.ampliar_vista(self.p, datos)["montaje_revision"]["tomas"][0]["editado_actualizado"])
        self.p.editar_montaje("t01", inicio=0.5)
        toma = self.vista()["montaje_revision"]["tomas"][0]
        self.assertFalse(toma["editado_actualizado"])
        self.assertEqual(toma["archivo_editado"], self.p.rel(ruta))
        self.assertEqual(ruta.read_bytes(), original)
        editado = exportacion.clip_editado(self.p, "t01", revision=True, avisar=lambda _: None)
        self.assertAlmostEqual(self.duracion(editado), 1.5, delta=0.15)
        self.assertTrue(self.vista()["montaje_revision"]["tomas"][0]["editado_actualizado"])

    def test_paquete_se_actualiza_por_continuidad_y_notas_sin_cambiar_el_video(self):
        self.preparadas()
        exportacion.paquete(self.p, revision=True, avisar=lambda _: None)
        datos = self.vista()
        self.assertTrue(datos["paquete_actualizado"])
        self.assertTrue(datos["paquete_revision"])
        clip = self.carpeta / datos["montaje_revision"]["tomas"][0]["archivo_editado"]
        original = clip.read_bytes()
        self.p.editar_continuidad("t01", inicio="La máscara permanece oculta.", acciones=["Retirar la tela después de la señal."])
        datos = self.vista()
        self.assertFalse(datos["paquete_actualizado"])
        self.assertTrue(datos["montaje_revision"]["tomas"][0]["editado_actualizado"])
        self.assertEqual(clip.read_bytes(), original)
        salida = exportacion.paquete(self.p, revision=True, avisar=lambda _: None)
        self.assertTrue(self.vista()["paquete_actualizado"])
        with zipfile.ZipFile(salida) as archivo:
            self.assertIn("La máscara permanece oculta.", json.loads(archivo.read("proyecto.json"))["tomas"][0]["prompt"])
        self.p.pedir_correccion("t01", "A", "Quitar el objeto rojo del fondo.")
        self.assertFalse(self.vista()["paquete_actualizado"])
        self.assertEqual(clip.read_bytes(), original)

    def test_final_estricto_no_exporta_pendientes_y_omitida_no_se_exporta(self):
        self.preparadas()
        with self.assertRaisesRegex(ErrorProduccion, "aprobado"):
            exportacion.paquete(self.p, revision=False, avisar=lambda _: None)
        with self.assertRaisesRegex(ErrorProduccion, "aprobado"):
            exportacion.clip_editado(self.p, "t01", revision=False, avisar=lambda _: None)
        with self.assertRaisesRegex(ErrorProduccion, "excluida"):
            exportacion.clip_editado(self.p, "t03", revision=True, avisar=lambda _: None)
        self.assertEqual([e["estado"] for e in self.p.estado["clips"].values()], ["pendiente", "pendiente"])

    def test_revision_con_imagen_fija_sin_variantes_incluye_prompt_de_imagen(self):
        self.p.datos["secuencias"][0]["tomas"][0]["variantes"] = {}
        self.p.guardar_estructura()
        self.p.subir_imagen("t01", "1", PNG, ".png")
        self.p.editar_montaje("t02", omitida=True)
        salida = exportacion.paquete(self.p, revision=True, avisar=lambda _: None)
        with zipfile.ZipFile(salida) as archivo:
            manifiesto = json.loads(archivo.read("proyecto.json"))
            self.assertEqual([t["id"] for t in manifiesto["tomas"]], ["t01"])
            self.assertTrue(manifiesto["tomas"][0]["imagen_fija"])
            self.assertIn("Frame:", manifiesto["tomas"][0]["prompt"])
            self.assertIn("clips-editados/t01-imagen-editado.mp4", archivo.namelist())
        self.assertEqual(self.p.estado["imagenes"]["t01/1"]["estado"], "pendiente")

    def test_rutas_http_exportan_y_sirven_zip_sin_generacion_ni_aprobacion(self):
        self.preparadas()
        parche = mock.patch.object(costos, "catalogo", return_value=None)
        parche.start()
        self.addCleanup(parche.stop)
        cliente = ClienteFalso()
        generar = mock.Mock(side_effect=AssertionError("La exportación no debe generar"))
        estudio = panel.Estudio(self.base, cliente=cliente, generar=generar)
        servidor = panel.crear_servidor(estudio, 0)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)

        def pedir(metodo, ruta, cuerpo=None, token=True):
            conexion = http.client.HTTPConnection("127.0.0.1", servidor.server_address[1], timeout=15)
            cabeceras = {"Content-Type": "application/json"}
            if token:
                cabeceras["X-Estudio-Token"] = estudio.token
            conexion.request(metodo, ruta, json.dumps(cuerpo) if cuerpo is not None else None, cabeceras)
            respuesta = conexion.getresponse()
            salida = (respuesta.status, respuesta.read(), dict(respuesta.getheaders()))
            conexion.close()
            return salida

        def esperar():
            limite = time.monotonic() + 15
            while time.monotonic() < limite:
                trabajo = estudio.trabajo
                if trabajo["estado"] != "en curso":
                    self.assertEqual(trabajo["estado"], "listo", trabajo)
                    self.assertFalse(trabajo["gasta"])
                    self.assertEqual(trabajo["resultado"]["costo_usd"], 0)
                    return trabajo
                time.sleep(0.05)
            self.fail("La exportación local no terminó")

        datos = {"id": "historia", "toma": "t01", "revision": True}
        self.assertEqual(pedir("POST", "/api/produccion/clip-editado", datos, token=False)[0], 403)
        self.assertEqual(pedir("POST", "/api/produccion/paquete", {"id": "historia", "revision": "true"})[0], 400)
        self.assertEqual(pedir("POST", "/api/produccion/clip-editado", datos)[0], 202)
        trabajo = esperar()
        self.assertEqual(pedir("GET", "/archivo?ruta=" + quote(trabajo["resultado"]["archivo"]))[0], 200)
        self.assertEqual(pedir("POST", "/api/produccion/paquete", {"id": "historia", "revision": True})[0], 202)
        trabajo = esperar()
        estado, contenido, cabeceras = pedir("GET", "/archivo?ruta=" + quote(trabajo["resultado"]["archivo"]))
        self.assertEqual(estado, 200)
        self.assertIn(cabeceras["Content-Type"], ("application/zip", "application/x-zip-compressed"))
        self.assertTrue(contenido.startswith(b"PK"))
        self.assertEqual(cliente.enviados, 0)
        generar.assert_not_called()
        vista = json.loads(pedir("GET", "/api/produccion?id=historia")[1])
        self.assertEqual(vista["paquete"], "historia/exportaciones/paquete-revision.zip")
        self.assertTrue(vista["paquete_actualizado"])
        self.assertEqual(vista["montaje_revision"]["tomas"][0]["archivo_editado"],
                         "historia/exportaciones/revision/t01-A-editado.mp4")


if __name__ == "__main__":
    unittest.main()
