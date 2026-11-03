"""Post-proceso local con ffmpeg y, para blanquear, numpy y scipy."""
import subprocess
from pathlib import Path


def _ffmpeg(*args, capturar=False):
    return subprocess.run(["ffmpeg", "-hide_banner", *args], check=True, capture_output=capturar, text=capturar)


def web(origen, destino, nombre, ancho=1920, blanco=False):
    """Comprime a MP4 y WebM y saca el póster del primer y del último cuadro."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    filtro = f"scale={ancho}:-2:flags=lanczos"
    if blanco:
        filtro += ",curves=all='0/0 0.90/0.93 0.975/1 1/1'"
    base = ["-v", "error", "-y", "-i", str(origen), "-vf", filtro]
    _ffmpeg(*base, "-c:v", "libx264", "-preset", "slow", "-crf", "25", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "112k",
            "-movflags", "+faststart", str(destino / f"{nombre}.mp4"))
    _ffmpeg(*base, "-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0", "-row-mt", "1", "-cpu-used", "2", "-pix_fmt", "yuv420p",
            "-c:a", "libopus", "-b:a", "96k", str(destino / f"{nombre}.webm"))
    _ffmpeg(*base, "-frames:v", "1", "-q:v", "3", str(destino / f"{nombre}-inicio.jpg"))
    _ffmpeg("-v", "error", "-y", "-sseof", "-0.15", "-i", str(origen), "-vf", filtro, "-update", "1", "-frames:v", "1", "-q:v", "3",
            str(destino / f"{nombre}-final.jpg"))
    return sorted(destino.glob(f"{nombre}*"))


def revisar(video, hoja):
    """Datos técnicos, errores de decodificación, tramos de silencio y una hoja de cuadros para revisar a ojo."""
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height", "-of", "compact", str(video)],
        check=True, capture_output=True, text=True).stdout.strip()
    errores = _ffmpeg("-v", "error", "-i", str(video), "-f", "null", "-", capturar=True).stderr.strip()
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-af", "silencedetect=n=-35dB:d=0.5", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True)
    silencios = [ln.split("] ")[1] for ln in r.stderr.splitlines() if "silence_end" in ln or "silence_start" in ln]
    _ffmpeg("-v", "error", "-y", "-i", str(video), "-vf", "fps=1,scale=480:-1,tile=4x4:padding=4:color=gray", "-frames:v", "1", str(hoja))
    return info, errores, silencios


def blanquear(entrada, salida, ancho=1920, alto=1080):
    """Lleva a blanco un fondo gris de cámara fija dividiendo cada cuadro entre un modelo suave del fondo."""
    import numpy as np
    from scipy import ndimage as ndi

    tam = ancho * alto * 3
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(entrada), "-vf", f"fps=1,scale={ancho}:{alto}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, check=True)
    mediana = np.median(np.frombuffer(p.stdout, np.uint8).reshape(-1, alto, ancho, 3), axis=0)
    gris = (mediana.max(axis=2) - mediana.min(axis=2)) < 28
    etiquetas, _ = ndi.label(gris & (mediana.min(axis=2) > 170))
    borde = np.unique(np.concatenate([etiquetas[0], etiquetas[:, 0], etiquetas[:, -1]]))
    fondo = ndi.binary_erosion(np.isin(etiquetas, borde[borde > 0]), iterations=8)
    if fondo.mean() < 0.05:
        raise ValueError("Encontré muy poco fondo claro; este video no parece de cámara fija con fondo liso")

    yy, xx = np.mgrid[0:alto, 0:ancho]
    u, v = xx / ancho - 0.5, yy / alto - 0.5
    terminos = [u**i * v**j for i in range(4) for j in range(4 - i)]
    idx = np.flatnonzero(fondo)[::37]
    a = np.stack([t.ravel()[idx] for t in terminos], axis=1)
    campo = np.zeros((alto, ancho, 3), np.float32)
    for c in range(3):
        coef, *_ = np.linalg.lstsq(a, mediana[..., c].ravel()[idx], rcond=None)
        campo[..., c] = sum(k * t for k, t in zip(coef, terminos))
    ganancia = 255 / (campo * 0.985)

    lector = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(entrada), "-vf", f"scale={ancho}:{alto}:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                              stdout=subprocess.PIPE)
    tasa = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(entrada)],
                          check=True, capture_output=True, text=True).stdout.strip()
    escritor = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ancho}x{alto}", "-r", tasa, "-i", "-",
                                 "-i", str(entrada), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
                                 "-c:a", "copy", str(salida)], stdin=subprocess.PIPE)
    while True:
        datos = lector.stdout.read(tam)
        if len(datos) < tam:
            break
        cuadro = np.frombuffer(datos, np.uint8).reshape(alto, ancho, 3)
        escritor.stdin.write(np.clip(cuadro * ganancia, 0, 255).astype(np.uint8).tobytes())
    escritor.stdin.close()
    if escritor.wait() or lector.wait():
        raise RuntimeError("ffmpeg falló al blanquear")
    return round(float(fondo.mean()) * 100, 1)
