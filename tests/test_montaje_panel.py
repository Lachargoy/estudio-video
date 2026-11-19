"""Montaje y correcciones por HTTP: locales, reversibles y sin OpenRouter."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from estudio_video import panel, produccion


class MontajePanel(unittest.TestCase):
    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.carpeta = self.raiz / "historia"
        produccion.crear(self.carpeta)
        self.cliente = mock.Mock()
        self.generar = mock.Mock(side_effect=AssertionError("El montaje nunca genera con OpenRouter"))
        self.estudio = panel.Estudio(self.raiz, cliente=self.cliente, generar=self.generar)
        self.servidor = panel.crear_servidor(self.estudio, 0)
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.addCleanup(self.servidor.server_close)
        self.addCleanup(self.servidor.shutdown)

    def pedir(self, ruta, datos=None, token=True):
        conexion = http.client.HTTPConnection("127.0.0.1", self.servidor.server_address[1], timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Estudio-Token"] = self.estudio.token
        conexion.request("GET" if datos is None else "POST", ruta,
                         None if datos is None else json.dumps(datos), headers)
        respuesta = conexion.getresponse()
        resultado = respuesta.status, json.loads(respuesta.read())
        conexion.close()
        return resultado

    def accion(self, accion, **datos):
        return self.pedir("/api/produccion/accion", {"id": "historia", "accion": accion, **datos})

    def esperar(self):
        for _ in range(100):
            if self.estudio.trabajo["estado"] != "en curso":
                break
            time.sleep(0.01)
        self.assertEqual(self.estudio.trabajo["estado"], "listo", self.estudio.trabajo)
        self.assertFalse(self.estudio.trabajo["gasta"])
        self.generar.assert_not_called()
        self.cliente.assert_not_called()
        self.assertFalse(self.cliente.method_calls)

    def test_edicion_y_correccion_no_destruyen_clip_ni_aprueban(self):
        clips = self.carpeta / "clips"
        clips.mkdir()
        original = clips / "t01-A.mp4"
        original.write_bytes(b"video original")
        self.assertEqual(self.accion("montaje_toma", toma="t01", inicio=0.5, fin=4,
                                     silenciar=True, quitar=[{"inicio": 1, "fin": 2}])[0], 200)
        self.assertEqual(self.accion("montaje_config", fundido=0.5)[0], 200)
        self.assertEqual(self.accion("correccion", toma="t01", variante="A", nota="Quitar el objeto del fondo")[0], 200)
        estado, vista = self.pedir("/api/produccion?id=historia")
        self.assertEqual(estado, 200)
        toma = vista["montaje"]["tomas"][0]
        self.assertEqual((toma["inicio"], toma["fin"], toma["silenciar"]), (0.5, 4, True))
        variante = vista["secuencias"][0]["tomas"][0]["variantes"][0]
        self.assertTrue(variante["correccion_pendiente"])
        self.assertEqual(variante["estado"], "pendiente")
        self.assertEqual(original.read_bytes(), b"video original")
        self.assertIn("DIRECTOR NOTE: Quitar el objeto del fondo", variante["prompt_completo"])
        self.generar.assert_not_called()
        self.assertFalse(self.cliente.method_calls)

    def test_final_faltante_bloqueado_revision_es_local(self):
        with mock.patch.object(panel.shutil, "which", return_value="ffmpeg"):
            codigo, respuesta = self.pedir("/api/produccion/montar", {"id": "historia"})
            self.assertEqual(codigo, 400)
            self.assertIn("Antes de montar", respuesta["error"])
            with mock.patch.object(produccion.Produccion, "montar_revision", return_value=self.carpeta / "revision.mp4"):
                self.assertEqual(self.pedir("/api/produccion/montar-revision", {"id": "historia"})[0], 202)
                self.esperar()

    def test_montaje_en_curso_impide_perder_ediciones(self):
        self.estudio.trabajo = {"estado": "en curso"}
        codigo, respuesta = self.accion("montaje_toma", toma="t01", omitida=True)
        self.assertEqual(codigo, 400)
        self.assertIn("termine", respuesta["error"])
        self.assertFalse(produccion.Produccion(self.carpeta).estado.get("montaje", {}).get("tomas"))

    def test_rutas_de_exportacion_y_proteccion_de_acciones(self):
        salida = self.carpeta / "animatica"
        salida.mkdir()
        for nombre in ("video-final.mp4", "video-revision.mp4"):
            (salida / nombre).write_bytes(b"video")
        _, vista = self.pedir("/api/produccion?id=historia")
        self.assertEqual(vista["video_final"], "historia/animatica/video-final.mp4")
        self.assertEqual(vista["video_revision"], "historia/animatica/video-revision.mp4")
        self.assertEqual(self.pedir("/api/produccion/montar-revision", {"id": "historia"}, token=False)[0], 403)
        codigo, _ = self.accion("montaje_toma", toma="t01", inicio=3, fin=2)
        self.assertEqual(codigo, 400)


if __name__ == "__main__":
    unittest.main()
