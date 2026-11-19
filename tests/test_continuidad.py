"""Cronología explícita en prompts, sin llamadas a modelos ni generación de video."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from estudio_video import produccion
from estudio_video.produccion import ErrorProduccion, Produccion


class Continuidad(unittest.TestCase):
    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        produccion.crear(self.carpeta)
        self.ruta = self.carpeta / "produccion.json"
        self.datos = json.loads(self.ruta.read_text(encoding="utf-8"))
        plantilla = self.datos["secuencias"][0]["tomas"][0]
        self.datos["secuencias"][0]["tomas"] = [dict(copy.deepcopy(plantilla), id=f"t0{i}") for i in range(1, 4)]
        self.guardar()

    def guardar(self):
        self.ruta.write_text(json.dumps(self.datos), encoding="utf-8")
        self.p = Produccion(self.carpeta)

    def clip(self, tid):
        carpeta = self.carpeta / "clips"
        carpeta.mkdir(exist_ok=True)
        ruta = carpeta / f"{tid}-A.mp4"
        ruta.write_bytes(b"clip sintetico sin cobro")
        self.p.decidir("clips", f"{tid}/A", "aprobada")
        return ruta

    def test_compatibilidad_con_produccion_sin_continuidad(self):
        vista = self.p.vista()
        for toma in vista["secuencias"][0]["tomas"]:
            self.assertEqual(toma["continuidad"], {"inicio": "", "acciones": [], "fin": "", "reglas": ""})
        prompt = self.p.prompt_video("t02", "A")
        self.assertNotIn("PREVIOUS SHOT END", prompt)
        self.assertNotIn("NEXT SHOT START", prompt)
        self.assertIn("Keep hidden objects and characters hidden", prompt)
        self.assertIn("Show the cause or action before its reaction", prompt)
        self.assertIn("Speak each scripted line only once", prompt)

    def test_estados_y_acciones_tienen_orden_temporal_explicito(self):
        self.p.editar_continuidad("t02", inicio="La máscara sigue cubierta.",
                                  acciones=["Él retira la tela.", "Ella ve el jaguar.", "Ella pronuncia una sola frase."],
                                  fin="La máscara de jaguar queda visible.", reglas="No anticipar la sorpresa.")
        prompt = self.p.prompt_video("t02", "A")
        elementos = ["STATE AT START: La máscara sigue cubierta.", "ACTION ORDER:",
                     "1. Él retira la tela.", "2. Ella ve el jaguar.", "3. Ella pronuncia una sola frase.",
                     "STATE AT END: La máscara de jaguar queda visible.", "CONTINUITY RULES:"]
        posiciones = [prompt.index(elemento) for elemento in elementos]
        self.assertEqual(posiciones, sorted(posiciones))
        self.assertIn("No anticipar la sorpresa.", prompt)
        imagen = self.p.prompt_imagen("t02", "1")
        self.assertIn("STATE AT START: La máscara sigue cubierta.", imagen)
        self.assertIn("never combine start and end states in one image", imagen)
        self.assertEqual(Produccion(self.carpeta).toma("t02")["continuidad"]["acciones"],
                         ["Él retira la tela.", "Ella ve el jaguar.", "Ella pronuncia una sola frase."])

    def test_vecinos_aportan_solo_estados_sin_acciones_dialogos_o_descripciones(self):
        tomas = self.datos["secuencias"][0]["tomas"]
        tomas[0].update(descripcion="DESCRIPCION_ANTERIOR_SECRETA", continuidad={
            "inicio": "INICIO_ANTERIOR_NO_INCLUIR", "acciones": ["DIALOGO_ANTERIOR_NO_REPETIR"], "fin": "Tela cubierta."})
        tomas[2].update(descripcion="DESCRIPCION_FUTURA_SECRETA", continuidad={
            "inicio": "La tela descansa sobre la mesa.", "acciones": ["DIALOGO_FUTURO_NO_ANTICIPAR"], "fin": "FINAL_FUTURO_NO_INCLUIR"})
        self.guardar()
        for prompt in (self.p.prompt_video("t02", "A"), self.p.prompt_imagen("t02", "1")):
            self.assertIn("PREVIOUS SHOT END (already happened; do not repeat): Tela cubierta.", prompt)
            self.assertIn("NEXT SHOT START (handoff only; do not perform the next shot here): La tela descansa sobre la mesa.", prompt)
            for secreto in ("DESCRIPCION_ANTERIOR_SECRETA", "INICIO_ANTERIOR_NO_INCLUIR", "DIALOGO_ANTERIOR_NO_REPETIR",
                            "DESCRIPCION_FUTURA_SECRETA", "DIALOGO_FUTURO_NO_ANTICIPAR", "FINAL_FUTURO_NO_INCLUIR"):
                self.assertNotIn(secreto, prompt)

    def test_reglas_globales_y_de_toma_se_incorporan_sin_duplicar_dialogo(self):
        self.datos["continuidad"] = "El objeto oculto solo aparece al retirar la tela."
        self.guardar()
        self.p.editar_continuidad("t02", acciones=["Dice: Ahora puedes mirar."], reglas="Conservar la posición de ambas manos.")
        prompt = self.p.prompt_video("t02", "A")
        self.assertIn(self.datos["continuidad"], prompt)
        self.assertIn("Conservar la posición de ambas manos.", prompt)
        self.assertEqual(prompt.count("Dice: Ahora puedes mirar."), 1)

    def test_valida_estructura_sin_convertir_errores_en_texto(self):
        original = copy.deepcopy(self.datos)
        for valor in (None, "una lista", [], {"acciones": "abrir"}, {"acciones": [1]}, {"acciones": [""]},
                      {"inicio": 7}, {"fin": False}, {"reglas": []}, {"inesperado": "x"}):
            with self.subTest(valor=valor):
                self.datos = copy.deepcopy(original)
                self.datos["secuencias"][0]["tomas"][0]["continuidad"] = valor
                self.ruta.write_text(json.dumps(self.datos), encoding="utf-8")
                with self.assertRaises(ErrorProduccion):
                    Produccion(self.carpeta)
        self.datos = copy.deepcopy(original)
        self.datos["continuidad"] = []
        self.ruta.write_text(json.dumps(self.datos), encoding="utf-8")
        with self.assertRaisesRegex(ErrorProduccion, "global"):
            Produccion(self.carpeta)

    def test_edicion_invalida_no_guarda_cambios_parciales(self):
        self.p.editar_continuidad("t02", inicio="Estado válido.")
        estructura, estado = self.ruta.read_bytes(), (self.carpeta / "estado.json").read_bytes()
        with self.assertRaises(ErrorProduccion):
            self.p.editar_continuidad("t02", acciones="abrir la tela")
        self.assertEqual(self.ruta.read_bytes(), estructura)
        self.assertEqual((self.carpeta / "estado.json").read_bytes(), estado)
        self.assertEqual(self.p.toma("t02")["continuidad"]["inicio"], "Estado válido.")

    def test_instrucciones_nuevas_senalan_clips_afectados_sin_aprobar_o_borrar(self):
        clips = {tid: self.clip(tid) for tid in ("t01", "t02", "t03")}
        antes = {tid: ruta.read_bytes() for tid, ruta in clips.items()}
        receta = self.p.receta_variante("t02", "A")[0]
        self.p.editar_continuidad("t02", inicio="Objeto aún oculto.", fin="Objeto revelado.")
        vista = self.p.vista()
        for toma in vista["secuencias"][0]["tomas"]:
            variante = toma["variantes"][0]
            self.assertTrue(variante["instrucciones_actualizadas"])
            self.assertEqual(variante["estado"], "aprobada")
            self.assertEqual(variante["versiones"], 0)
            self.assertEqual(clips[toma["id"]].read_bytes(), antes[toma["id"]])
        self.assertNotEqual(self.p.huella(receta), self.p.huella(self.p.receta_variante("t02", "A")[0]))

    def test_acciones_propias_no_invalidan_vecinos_y_nueva_version_limpia_aviso(self):
        for tid in ("t01", "t02", "t03"):
            self.clip(tid)
        self.p.editar_continuidad("t02", acciones=["Retirar la tela."])
        tomas = self.p.vista()["secuencias"][0]["tomas"]
        self.assertEqual([t["variantes"][0]["instrucciones_actualizadas"] for t in tomas], [False, True, False])
        nuevo = self.carpeta / "clips" / "t02-A-nuevo.mp4"
        nuevo.write_bytes(b"nueva version sintetica")
        self.p.registrar_clip("t02", "A", {"archivos": [str(nuevo)], "costo_usd": 0})
        toma = self.p.vista()["secuencias"][0]["tomas"][1]
        self.assertFalse(toma["variantes"][0]["instrucciones_actualizadas"])
        self.assertEqual(toma["variantes"][0]["estado"], "pendiente")
        self.assertEqual(toma["variantes"][0]["versiones"], 1)


if __name__ == "__main__":
    unittest.main()
