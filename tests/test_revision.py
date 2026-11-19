"""La hoja de revisión debe cubrir la historia completa, incluso si es larga."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from estudio_video import limpieza


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "necesita ffmpeg y ffprobe")
class HojaDeRevision(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.carpeta = Path(carpeta.name)

    def pixel_final(self, hoja):
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(hoja), "-vf",
                            "crop=2:2:iw*7/8:ih*7/8,scale=1:1", "-frames:v", "1", "-pix_fmt", "rgb24",
                            "-f", "rawvideo", "-"], check=True, capture_output=True, timeout=30)
        self.assertEqual(len(r.stdout), 3)
        return tuple(r.stdout)

    def test_hoja_incluye_desenlace_despues_de_los_primeros_16_segundos(self):
        fuente, hoja = self.carpeta / "historia.mp4", self.carpeta / "hoja.png"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=red:s=64x36:r=24:d=20", "-f", "lavfi", "-i", "color=blue:s=64x36:r=24:d=4",
                        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
                        "-c:v", "libx264", "-preset", "ultrafast", str(fuente)], check=True, capture_output=True, timeout=30)
        _, errores, _ = limpieza.revisar(fuente, hoja)
        self.assertEqual(errores, "")
        rojo, _, azul = self.pixel_final(hoja)
        self.assertGreater(azul, 180)
        self.assertLess(rojo, 40)

    def test_hoja_completa_tambien_un_clip_muy_corto(self):
        fuente, hoja = self.carpeta / "corto.mp4", self.carpeta / "hoja.png"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=lime:s=64x36:r=24:d=0.125", "-c:v", "libx264", str(fuente)],
                       check=True, capture_output=True, timeout=30)
        limpieza.revisar(fuente, hoja)
        rojo, verde, azul = self.pixel_final(hoja)
        self.assertGreater(verde, 180)
        self.assertLess(max(rojo, azul), 40)


if __name__ == "__main__":
    unittest.main()
