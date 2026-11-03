import json
import tempfile
import unittest
from pathlib import Path

from estudio_video import costos, grafo, receta

ESQUEMA = {
    "output_name": ["video", "poster", "local job id", "provider job id", "metadata JSON", "estimated cost USD", "actual cost USD"],
    "input": {"required": {
        "model": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "minimax/hailuo-3", "inputs": {
            "required": {
                "prompt": ["STRING", {"default": ""}],
                "duration": ["COMBO", {"default": 5, "options": [5, 8, 10]}],
                "resolution": ["COMBO", {"default": "2K", "options": ["2K"]}],
                "aspect_ratio": ["COMBO", {"default": "16:9", "options": ["16:9", "9:16"]}],
                "generate_audio": ["COMBO", {"default": "false", "options": ["false", "true", "provider default"]}],
                "reference_images": ["COMFY_AUTOGROW_V3", {"template": {"names": ["reference_image_1", "reference_image_2"]}}],
            },
            "optional": {"first_frame": ["IMAGE", {}], "last_frame": ["IMAGE", {}]},
        }}]}],
        "strict_validation": ["BOOLEAN", {"default": True}],
        "rerun_nonce": ["INT", {"default": 0}],
    }},
}


def base(**extra):
    return {"nombre": "n", "tipo": "video", "modelo": "minimax/hailuo-3", "prompt": "p", "duracion": 8, "max_usd": 2, "referencias": [], **extra}


class Grafo(unittest.TestCase):
    def test_referencias_usan_nombres_del_esquema(self):
        g = grafo.construir(base(referencias=["a.png"], audio=True), ESQUEMA, {"a.png": "subida.png"}, nonce=7)
        e = g["g"]["inputs"]
        self.assertEqual(e["model.reference_images.reference_image_1"], ["r0", 0])
        self.assertEqual(g["r0"]["inputs"]["image"], "subida.png")
        self.assertEqual(e["model.generate_audio"], "true")
        self.assertEqual(e["model.duration"], 8)
        self.assertEqual(e["rerun_nonce"], 7)
        self.assertEqual(g["c"]["inputs"]["source"], ["g", 6])
        self.assertEqual(g["m"]["inputs"]["source"], ["g", 4])

    def test_fotogramas(self):
        g = grafo.construir(base(primer_cuadro="a", ultimo_cuadro="b"), ESQUEMA, {"a": "1", "b": "2"})
        self.assertEqual(g["g"]["inputs"]["model.first_frame"], ["first_frame", 0])
        self.assertEqual(g["g"]["inputs"]["model.last_frame"], ["last_frame", 0])

    def test_rechaza_valores_invalidos_antes_de_enviar(self):
        with self.assertRaisesRegex(ValueError, "duracion"):
            grafo.construir(base(duracion=7), ESQUEMA, {})
        with self.assertRaisesRegex(ValueError, "catálogo"):
            grafo.construir(base(modelo="otro/modelo"), ESQUEMA, {})
        with self.assertRaisesRegex(ValueError, "hasta 2"):
            grafo.construir(base(referencias=["a", "b", "c"]), ESQUEMA, {"a": "1", "b": "2", "c": "3"})


class Costos(unittest.TestCase):
    def test_h3(self):
        m = {"pricing_skus": {"duration_seconds": "0.13", "reference_images": "0.04"}}
        self.assertAlmostEqual(costos.estimar(base(duracion=10, referencias=[1, 2]), m), 1.38)

    def test_por_resolucion_y_centavos(self):
        m = {"pricing_skus": {"cents_per_video_output_second_480p": "2"}}
        self.assertAlmostEqual(costos.estimar(base(duracion=1, resolucion="480p"), m), 0.02)

    def test_cobro_por_imagen_de_entrada(self):
        m = {"pricing_skus": {"cents_per_image_input": "1", "cents_per_video_output_second_480p": "2"}}
        self.assertAlmostEqual(costos.estimar(base(duracion=1, resolucion="480p", primer_cuadro="a.png"), m), 0.03)

    def test_audio(self):
        m = {"pricing_skus": {"duration_seconds_with_audio": "0.40", "duration_seconds_without_audio": "0.20"}}
        self.assertAlmostEqual(costos.estimar(base(duracion=5, audio=True), m), 2.0)
        self.assertAlmostEqual(costos.estimar(base(duracion=5, audio=False), m), 1.0)

    def test_sin_precio_por_segundo(self):
        self.assertIsNone(costos.estimar(base(), {"pricing_skus": {"video_tokens": "0.000007"}}))


class Receta(unittest.TestCase):
    def escribir(self, datos):
        d = Path(tempfile.mkdtemp())
        (d / "a.png").write_bytes(b"x")
        (d / "r.json").write_text(json.dumps(datos))
        return d / "r.json"

    def test_rutas_relativas(self):
        r = receta.cargar(self.escribir({"nombre": "n", "modelo": "m", "prompt": "p", "duracion": 5, "max_usd": 1, "primer_cuadro": "a.png"}))
        self.assertTrue(r["primer_cuadro"].is_absolute())

    def test_h3_por_defecto_en_video(self):
        r = receta.cargar(self.escribir({"nombre": "n", "prompt": "p", "duracion": 5, "max_usd": 1}))
        self.assertEqual(r["modelo"], "minimax/hailuo-3")
        r = receta.cargar(self.escribir({"nombre": "n", "modelo": "otro/modelo", "prompt": "p", "duracion": 5, "max_usd": 1}))
        self.assertEqual(r["modelo"], "otro/modelo")
        with self.assertRaisesRegex(receta.ErrorReceta, "modelo"):
            receta.cargar(self.escribir({"nombre": "n", "tipo": "imagen", "prompt": "p", "max_usd": 1}))

    def test_errores(self):
        for datos, texto in [
            ({"modelo": "m", "prompt": "p", "max_usd": 1, "duracion": 5}, "nombre"),
            ({"nombre": "n", "modelo": "m", "prompt": "p", "duracion": 5}, "max_usd"),
            ({"nombre": "n", "modelo": "m", "prompt": "p", "max_usd": 1}, "duracion"),
            ({"nombre": "n", "modelo": "m", "prompt": "p", "max_usd": 1, "duracion": 5, "primer_cuadro": "no.png"}, "No existe"),
            ({"nombre": "n", "modelo": "m", "prompt": "p", "max_usd": 1, "duracion": 5, "referencias": ["a.png"], "primer_cuadro": "a.png"}, "No combines"),
            ({"nombre": "n", "modelo": "m", "prompt": "p", "max_usd": 1, "duracion": 5, "typo": 1}, "desconocidas"),
        ]:
            with self.assertRaisesRegex(receta.ErrorReceta, texto):
                receta.cargar(self.escribir(datos))


if __name__ == "__main__":
    unittest.main()
