"""Post-proceso local con ffmpeg y, para blanquear, numpy y scipy."""
import json
import subprocess
from pathlib import Path


def _ffmpeg(*args, capturar=False):
    return subprocess.run(["ffmpeg", "-hide_banner", *args], check=True, capture_output=capturar, text=capturar)


def _medir(video):
    salida = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", str(video)],
        check=True, capture_output=True, text=True).stdout
    datos = json.loads(salida)
    imagen = next(s for s in datos["streams"] if s["codec_type"] == "video")
    num, den = imagen["r_frame_rate"].split("/")
    return {
        "ancho": int(imagen["width"]), "alto": int(imagen["height"]), "fps": float(num) / float(den),
        "duracion": float(datos["format"]["duration"]), "audio": any(s["codec_type"] == "audio" for s in datos["streams"]),
    }


def ultimo_cuadro(video, destino):
    """Extrae el último cuadro. Un PNG evita agregar compresión a la toma siguiente."""
    _ffmpeg("-v", "error", "-y", "-sseof", "-0.25", "-i", str(video), "-update", "1", "-frames:v", "1", str(destino))
    if not Path(destino).is_file():
        raise ValueError(f"No se pudo extraer el último cuadro de {video}")
    return Path(destino)


def cola_video(video, destino, segundos):
    """Copia los últimos segundos en H.264 y AAC para usarlos como video de referencia."""
    inicio = max(0.0, _medir(video)["duracion"] - float(segundos))
    _ffmpeg("-v", "error", "-y", "-ss", f"{inicio:.3f}", "-i", str(video), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(destino))
    return Path(destino)


def unir(videos, salida, recortar_inicio=0, fundido=0.0):
    """Une tomas con el formato de la primera. `recortar_inicio` quita cuadros repetidos de las siguientes.

    El fundido de audio usa pesos que suman 1 para no subir el volumen en el empalme.
    """
    if len(videos) < 2:
        raise ValueError("Se necesitan al menos dos videos")
    medidas = [_medir(v) for v in videos]
    base = medidas[0]
    entradas, filtros = [], []
    duraciones = []
    for i, (video, m) in enumerate(zip(videos, medidas)):
        entradas += ["-i", str(video)]
        corte = recortar_inicio / m["fps"] if i and recortar_inicio else 0.0
        duracion = m["duracion"] - corte
        if duracion <= fundido:
            raise ValueError(f"{video} es demasiado corto para este fundido")
        duraciones.append(duracion)
        filtros.append(
            f"[{i}:v]trim=start={corte:.5f},setpts=PTS-STARTPTS,"
            f"scale={base['ancho']}:{base['alto']}:force_original_aspect_ratio=decrease,"
            f"pad={base['ancho']}:{base['alto']}:(ow-iw)/2:(oh-ih)/2:white,fps={base['fps']:.5f},format=yuv420p,setsar=1[v{i}]")
        if m["audio"]:
            filtros.append(f"[{i}:a]atrim=start={corte:.5f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            filtros.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duracion:.5f},asetpts=PTS-STARTPTS[a{i}]")
    if fundido:
        v, a, acumulado = "v0", "a0", duraciones[0]
        for i in range(1, len(videos)):
            filtros.append(f"[{v}][v{i}]xfade=transition=fade:duration={fundido}:offset={acumulado - fundido:.5f}[vx{i}]")
            filtros.append(f"[{a}][a{i}]acrossfade=d={fundido}:c1=tri:c2=tri[ax{i}]")
            v, a, acumulado = f"vx{i}", f"ax{i}", acumulado + duraciones[i] - fundido
        final_v, final_a = v, a
    else:
        pares = "".join(f"[v{i}][a{i}]" for i in range(len(videos)))
        filtros.append(f"{pares}concat=n={len(videos)}:v=1:a=1[vf][af]")
        final_v, final_a = "vf", "af"
    _ffmpeg("-v", "error", "-y", *entradas, "-filter_complex", ";".join(filtros), "-map", f"[{final_v}]", "-map", f"[{final_a}]",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(salida))
    return Path(salida)


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
