"""Montaje local con material sintético: nunca contacta proveedores ni gasta saldo."""
import array
import json
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from estudio_video import produccion
from estudio_video.produccion import ErrorProduccion, Produccion


class MontajeBase(unittest.TestCase):
    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name) / "historia"
        produccion.crear(self.carpeta)
        ruta = self.carpeta / "produccion.json"
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        original = datos["secuencias"][0]["tomas"][0]
        tomas = []
        for numero in range(1, 4):
            toma = json.loads(json.dumps(original))
            toma.update(id=f"t0{numero}", titulo=f"Paso {numero}", duracion=2)
            tomas.append(toma)
        datos["secuencias"][0]["tomas"] = tomas
        ruta.write_text(json.dumps(datos), encoding="utf-8")
        self.p = Produccion(self.carpeta)

    def clip_falso(self, tid="t01", vid="A", aprobado=True):
        carpeta = self.carpeta / "clips"
        carpeta.mkdir(exist_ok=True)
        ruta = carpeta / f"{tid}-{vid}.mp4"
        ruta.write_bytes(b"material de prueba")
        if aprobado:
            self.p.decidir("clips", f"{tid}/{vid}", "aprobada")
        return ruta


class Edicion(MontajeBase):
    def test_editar_guarda_sin_alterar_fuente_ni_aprobaciones(self):
        original = self.clip_falso()
        antes = original.read_bytes()
        self.p.editar_montaje("t01", inicio=0.25, fin=1.75,
                              silenciar=True, quitar=[{"inicio": 0.75, "fin": 1}])
        nueva = Produccion(self.carpeta)
        toma = nueva.vista()["montaje"]["tomas"][0]
        self.assertEqual((toma["inicio"], toma["fin"], toma["silenciar"]), (0.25, 1.75, True))
        self.assertEqual(toma["quitar"], [{"inicio": 0.75, "fin": 1}])
        self.assertAlmostEqual(toma["duracion_corte"], 1.25)
        self.assertEqual(original.read_bytes(), antes)
        self.assertEqual(nueva.estado["clips"]["t01/A"]["estado"], "aprobada")

    def test_omite_y_resta_fundidos_a_duracion_estimada(self):
        for tid in ("t01", "t02", "t03"):
            self.clip_falso(tid)
        self.p.configurar_montaje(fundido=0.25)
        self.p.editar_montaje("t02", omitida=True)
        montaje = self.p.vista()["montaje"]
        self.assertEqual(montaje["faltas"], [])
        self.assertAlmostEqual(montaje["duracion"], 3.75)
        self.assertTrue(montaje["tomas"][1]["omitida"])

    def test_seleccion_explicita_no_cambia_silenciosamente_de_variante(self):
        self.clip_falso("t01", "A", aprobado=True)
        self.clip_falso("t01", "B", aprobado=False)
        self.p.elegir_corte("t01", "B")
        montaje = self.p.vista()["montaje"]
        self.assertTrue(any("t01" in falta for falta in montaje["faltas"]))
        with self.assertRaises(ErrorProduccion):
            self.p.montar(avisar=lambda _: None)

    def test_rechaza_rangos_invalidos_y_no_guarda_cambios_parciales(self):
        self.p.editar_montaje("t01", inicio=0.25, fin=1.75)
        antes = (self.carpeta / "estado.json").read_bytes()
        casos = [
            {"inicio": -1}, {"inicio": float("nan")}, {"fin": float("inf")},
            {"inicio": 1, "fin": 0.5}, {"inicio": 1, "fin": 1},
            {"quitar": [{"inicio": 1, "fin": 0.5}]},
            {"quitar": [{"inicio": -0.1, "fin": 0.5}]},
            {"inicio": 0.5, "fin": 1.5, "quitar": [{"inicio": 0, "fin": 1}]},
        ]
        for campos in casos:
            with self.subTest(campos=campos):
                with self.assertRaises(ErrorProduccion):
                    self.p.editar_montaje("t01", **campos)
                self.assertEqual((self.carpeta / "estado.json").read_bytes(), antes)

    def test_rechaza_toma_inexistente_y_fundido_no_finito(self):
        with self.assertRaises(ErrorProduccion):
            self.p.editar_montaje("no-existe")
        for valor in (-1, float("nan"), float("inf")):
            with self.subTest(valor=valor), self.assertRaises(ErrorProduccion):
                self.p.configurar_montaje(fundido=valor)

    def test_intervalos_superpuestos_no_se_descuentan_dos_veces(self):
        cortes = [{"inicio": 0.5, "fin": 1.25}, {"inicio": 1, "fin": 1.5}]
        try:
            self.p.editar_montaje("t01", quitar=cortes)
        except ErrorProduccion:
            return  # Rechazar solapes también evita contar dos veces el mismo tiempo.
        toma = self.p.vista()["montaje"]["tomas"][0]
        self.assertAlmostEqual(toma["duracion_corte"], 1)

    def test_solicitar_correccion_conserva_video_y_cambia_receta(self):
        original = self.clip_falso()
        antes = original.read_bytes()
        receta_antes = self.p.receta_variante("t01", "A")[0]
        self.p.pedir_correccion("t01", "A", "Quitar el objeto rojo del fondo")
        vista = self.p.vista()
        variante = vista["secuencias"][0]["tomas"][0]["variantes"][0]
        receta_despues = self.p.receta_variante("t01", "A")[0]
        self.assertEqual(original.read_bytes(), antes)
        self.assertEqual(variante["estado"], "aprobada")
        self.assertTrue(variante["correccion_pendiente"])
        self.assertIn("DIRECTOR NOTE: Quitar el objeto rojo del fondo", receta_despues["prompt"])
        self.assertNotEqual(self.p.huella(receta_antes), self.p.huella(receta_despues))
        self.assertEqual(variante["versiones"], 0)

    def test_nota_vacia_o_variante_inexistente_no_crea_correccion(self):
        self.clip_falso()
        for vid, nota in (("A", "  "), ("Z", "Cambiar fondo")):
            with self.subTest(vid=vid, nota=nota), self.assertRaises(ErrorProduccion):
                self.p.pedir_correccion("t01", vid, nota)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "necesita ffmpeg y ffprobe")
class Render(MontajeBase):
    def clip(self, tid, color, audio=False, duracion=2, vid="A", aprobado=True):
        carpeta = self.carpeta / "clips"
        carpeta.mkdir(exist_ok=True)
        ruta = carpeta / f"{tid}-{vid}.mp4"
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
               f"color=c={color}:s=160x120:r=24:d={duracion}"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duracion}"]
        cmd += ["-t", str(duracion), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        if audio:
            cmd += ["-c:a", "aac"]
        subprocess.run([*cmd, str(ruta)], check=True, capture_output=True, timeout=30)
        if aprobado:
            self.p.decidir("clips", f"{tid}/{vid}", "aprobada")
        return ruta

    def medir(self, ruta):
        resultado = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                     "format=duration:stream=codec_type", "-of", "json", str(ruta)],
                                    check=True, capture_output=True, text=True, timeout=15)
        return json.loads(resultado.stdout)

    def pixel(self, ruta, segundo):
        salida = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(segundo),
                                 "-i", str(ruta), "-frames:v", "1", "-vf", "scale=1:1",
                                 "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                                check=True, capture_output=True, timeout=15).stdout
        self.assertEqual(len(salida), 3)
        return tuple(salida)

    def test_une_en_orden_con_audio_mixto_y_fundido(self):
        for tid, color, audio in (("t01", "red", True), ("t02", "lime", False), ("t03", "blue", True)):
            self.clip(tid, color, audio=audio)
        self.p.configurar_montaje(fundido=0.25)
        final = self.p.montar(avisar=lambda _: None)
        self.assertEqual(final.resolve(), (self.carpeta / "animatica" / "video-final.mp4").resolve())
        medidas = self.medir(final)
        self.assertAlmostEqual(float(medidas["format"]["duration"]), 5.5, delta=0.18)
        self.assertIn("audio", [s["codec_type"] for s in medidas["streams"]])
        for segundo, canal in ((0.5, 0), (2.5, 1), (4.5, 2)):
            pixel = self.pixel(final, segundo)
            self.assertGreater(pixel[canal], 150)
            self.assertLess(max(pixel[i] for i in range(3) if i != canal), 60)
        transicion = self.pixel(final, 1.875)
        self.assertGreater(transicion[0], 30)
        self.assertGreater(transicion[1], 30)
        vista = Produccion(self.carpeta).vista()
        self.assertEqual(vista["video_final"], "animatica/video-final.mp4")
        self.assertTrue(vista["montaje"]["actualizado"])

    def test_recorta_quita_fragmento_y_omite_toma_sin_modificar_original(self):
        primero = self.clip("t01", "red", audio=True)
        self.clip("t03", "blue")
        original = primero.read_bytes()
        self.p.configurar_montaje(fundido=0)
        self.p.editar_montaje("t01", inicio=0.25, fin=1.75, quitar=[{"inicio": 0.75, "fin": 1.25}])
        self.p.editar_montaje("t02", omitida=True)
        final = self.p.montar(avisar=lambda _: None)
        self.assertAlmostEqual(float(self.medir(final)["format"]["duration"]), 3, delta=0.18)
        self.assertEqual(primero.read_bytes(), original)
        self.assertGreater(self.pixel(final, 0.4)[0], 150)
        self.assertGreater(self.pixel(final, 2)[2], 150)

    def test_silencia_una_toma_y_respeta_duracion_real_corta(self):
        self.clip("t01", "red", audio=True, duracion=0.75)
        self.p.editar_montaje("t01", silenciar=True)
        self.p.editar_montaje("t02", omitida=True)
        self.p.editar_montaje("t03", omitida=True)
        final = self.p.montar(avisar=lambda _: None)
        self.assertAlmostEqual(float(self.medir(final)["format"]["duration"]), 0.75, delta=0.15)
        crudo = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(final),
                                "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                               check=True, capture_output=True, timeout=15).stdout
        muestras = array.array("h", crudo)
        self.assertTrue(muestras)
        rms = math.sqrt(sum(n * n for n in muestras) / len(muestras))
        self.assertLess(rms, 2)

    def test_edicion_y_cambio_de_fuente_marcan_exportacion_anterior(self):
        clip = self.clip("t01", "red")
        self.p.editar_montaje("t02", omitida=True)
        self.p.editar_montaje("t03", omitida=True)
        final = self.p.montar(avisar=lambda _: None)
        self.assertTrue(self.p.vista()["montaje"]["actualizado"])
        original_final = final.read_bytes()
        self.p.editar_montaje("t01", inicio=0.25)
        self.assertFalse(self.p.vista()["montaje"]["actualizado"])
        self.assertEqual(final.read_bytes(), original_final)
        self.p.montar(avisar=lambda _: None)
        self.assertTrue(self.p.vista()["montaje"]["actualizado"])
        estadistica = clip.stat()
        os.utime(clip, ns=(estadistica.st_atime_ns, estadistica.st_mtime_ns + 2_000_000_000))
        self.assertFalse(self.p.vista()["montaje"]["actualizado"])

    def test_todo_omitido_o_fuente_invalida_preserva_final_existente(self):
        self.clip("t01", "red")
        self.p.editar_montaje("t02", omitida=True)
        self.p.editar_montaje("t03", omitida=True)
        final = self.p.montar(avisar=lambda _: None)
        original = final.read_bytes()
        self.p.editar_montaje("t01", omitida=True)
        with self.assertRaises(ErrorProduccion):
            self.p.montar(avisar=lambda _: None)
        self.assertEqual(final.read_bytes(), original)
        self.p.editar_montaje("t01")
        (self.carpeta / "clips" / "t01-A.mp4").write_bytes(b"video roto")
        with self.assertRaises(ErrorProduccion):
            self.p.montar(avisar=lambda _: None)
        self.assertEqual(final.read_bytes(), original)

    def test_revision_permite_clip_pendiente_sin_aprobar_ni_habilitar_final(self):
        self.clip("t01", "red", audio=True, aprobado=False)
        self.p.editar_montaje("t02", omitida=True)
        self.p.editar_montaje("t03", omitida=True)
        with self.assertRaises(ErrorProduccion):
            self.p.montar(avisar=lambda _: None)
        revision = self.p.montar_revision(avisar=lambda _: None)
        self.assertEqual(revision.name, "video-revision.mp4")
        self.assertAlmostEqual(float(self.medir(revision)["format"]["duration"]), 2, delta=0.15)
        vista = Produccion(self.carpeta).vista()
        variante = vista["secuencias"][0]["tomas"][0]["variantes"][0]
        self.assertEqual(variante["estado"], "pendiente")
        self.assertIsNone(vista["video_final"])
        self.assertEqual(vista["video_revision"], "animatica/video-revision.mp4")
        self.assertTrue(vista["montaje_revision"]["actualizado"])
        with self.assertRaises(ErrorProduccion):
            self.p.montar(avisar=lambda _: None)

    def test_revision_muestra_imagen_fija_si_no_hay_clip(self):
        from test_produccion import PNG
        self.p.subir_imagen("t01", "1", PNG, ".png")
        self.p.decidir("imagenes", "t01/1", "aprobada")
        self.p.editar_montaje("t02", omitida=True)
        self.p.editar_montaje("t03", omitida=True)
        revision = self.p.montar_revision(avisar=lambda _: None)
        self.assertAlmostEqual(float(self.medir(revision)["format"]["duration"]), 2, delta=0.15)
        pixel = self.pixel(revision, 1)
        self.assertGreater(pixel[0], pixel[1])
        self.assertGreater(pixel[1], pixel[2])
        self.assertTrue(Produccion(self.carpeta).vista()["montaje_revision"]["actualizado"])
        self.assertIsNone(self.p.archivo_clip("t01", "A"))


if __name__ == "__main__":
    unittest.main()
