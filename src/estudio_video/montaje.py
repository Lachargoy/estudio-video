"""Edición local y no destructiva: recortes, fragmentos, audio y transiciones."""
import json
import math
import subprocess
import tempfile
from pathlib import Path


class ErrorMontaje(ValueError):
    pass


def numero(valor, nombre):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        raise ErrorMontaje(f"{nombre} debe ser un número finito")
    return float(valor)


def validar_edicion(inicio=0, fin=None, omitida=False, silenciar=False, quitar=None, limite=None):
    inicio = numero(inicio, "El inicio")
    fin = numero(fin, "El fin") if fin is not None else None
    if inicio < 0 or (fin is not None and fin <= inicio):
        raise ErrorMontaje("El fin debe ser mayor que el inicio y el inicio no puede ser negativo")
    tope = fin if fin is not None else limite
    if tope is not None and inicio >= tope:
        raise ErrorMontaje("El inicio debe ser anterior al fin de la toma")
    if type(omitida) is not bool or type(silenciar) is not bool:
        raise ErrorMontaje("Omitida y silenciar deben ser valores booleanos")
    if quitar is None:
        quitar = []
    if not isinstance(quitar, list) or len(quitar) > 100:
        raise ErrorMontaje("Quitar debe ser una lista de hasta 100 fragmentos")
    cortes = []
    for corte in quitar:
        if not isinstance(corte, dict):
            raise ErrorMontaje("Cada fragmento necesita inicio y fin")
        a, b = numero(corte.get("inicio"), "Inicio del fragmento"), numero(corte.get("fin"), "Fin del fragmento")
        if a < inicio or b <= a or (tope is not None and b > tope):
            raise ErrorMontaje("Cada fragmento debe estar dentro del recorte y tener fin mayor que inicio")
        cortes.append({"inicio": a, "fin": b})
    cortes.sort(key=lambda c: c["inicio"])
    if any(b["inicio"] < a["fin"] for a, b in zip(cortes, cortes[1:])):
        raise ErrorMontaje("Los fragmentos a quitar no pueden solaparse")
    edicion = {"inicio": inicio, "fin": fin, "omitida": omitida, "silenciar": silenciar, "quitar": cortes}
    if tope is not None and not omitida:
        intervalos(edicion, tope)
    return edicion


def intervalos(edicion, duracion):
    """Fragmentos conservados en tiempo de la fuente, acotados a su duración real."""
    inicio = edicion["inicio"]
    fin = min(duracion, edicion["fin"] if edicion["fin"] is not None else duracion)
    if inicio >= fin:
        raise ErrorMontaje("El recorte deja la toma sin video; ajusta inicio y fin")
    tramos, cursor = [], inicio
    for corte in edicion["quitar"]:
        a, b = max(inicio, corte["inicio"]), min(fin, corte["fin"])
        if a >= fin or b <= inicio:
            continue
        if a > cursor:
            tramos.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < fin:
        tramos.append((cursor, fin))
    if not tramos or sum(b - a for a, b in tramos) < 1 / 24:
        raise ErrorMontaje("Los fragmentos eliminan toda la toma; usa Omitir toma")
    return tramos


def duracion_editada(edicion, duracion):
    return sum(b - a for a, b in intervalos(edicion, duracion))


def transicion(duraciones, fundido):
    """Una toma muy corta reduce el solapamiento para mantener visibles todas las tomas."""
    return min(fundido, min(duraciones) / 2) if len(duraciones) > 1 else 0.0


def ejecutar(args, que, limite=600):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=limite)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ErrorMontaje(f"No se pudo procesar {que}: {exc}") from None
    except subprocess.CalledProcessError as exc:
        raise ErrorMontaje(f"Error al procesar {que}: {(exc.stderr or '').strip()[-500:]}") from None


def medir(video, duracion_imagen=None):
    r = ejecutar(["ffprobe", "-v", "error", "-show_entries",
                  "stream=codec_type,width,height,duration:format=duration", "-of", "json", str(video)], str(video), 60)
    try:
        datos = json.loads(r.stdout)
        imagen = next(s for s in datos["streams"] if s["codec_type"] == "video")
        duracion = float(duracion_imagen if duracion_imagen is not None else imagen.get("duration") or datos["format"]["duration"])
        if not math.isfinite(duracion) or duracion <= 0:
            raise ValueError()
        return {"ancho": int(imagen["width"]), "alto": int(imagen["height"]), "duracion": duracion,
                "audio": any(s["codec_type"] == "audio" for s in datos["streams"])}
    except (ValueError, KeyError, StopIteration, TypeError):
        raise ErrorMontaje(f"No se encontró video válido en {video}") from None


def renderizar(tomas, salida, fundido, avisar=print):
    """Normaliza cada toma y une sus imágenes y audio; nunca modifica las fuentes."""
    medidas = [medir(t["archivo"], t["duracion"] if t.get("imagen") else None) for t in tomas]
    # Una apertura fija puede tener mucha más resolución que los clips; usar el
    # primer video evita ampliar y recodificar toda la historia innecesariamente.
    base = next((m for t, m in zip(tomas, medidas) if not t.get("imagen")), medidas[0])
    ancho = max(2, base["ancho"] // 2 * 2)
    alto = max(2, base["alto"] // 2 * 2)
    filtro_video = (f"scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
                    f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p,settb=AVTB")
    with tempfile.TemporaryDirectory(prefix="montaje-", dir=Path(salida).parent) as tmp:
        partes, duraciones = [], []
        for i, (toma, medida) in enumerate(zip(tomas, medidas)):
            edicion = toma["edicion"]
            limite = min(medida["duracion"], toma["duracion"]) if edicion["fin"] is None else medida["duracion"]
            tramos = intervalos(edicion, limite)
            dur = sum(b - a for a, b in tramos)
            avisar(f"Toma {toma['id']}: {len(tramos)} fragmento(s) · {dur:.2f} s" +
                   (" · IMAGEN FIJA: falta clip de video" if toma.get("imagen") else "") +
                   (" · sin audio" if edicion["silenciar"] else ""))
            filtros, entradas = [], (["-loop", "1"] if toma.get("imagen") else []) + ["-threads", "1", "-i", str(toma["archivo"])]
            sonido = medida["audio"] and not edicion["silenciar"]
            if len(tramos) > 1:
                filtros.append(f"[0:v]split={len(tramos)}" + "".join(f"[vi{j}]" for j in range(len(tramos))))
                if sonido:
                    filtros.append(f"[0:a]asplit={len(tramos)}" + "".join(f"[ai{j}]" for j in range(len(tramos))))
            for j, (inicio, fin) in enumerate(tramos):
                v = f"vi{j}" if len(tramos) > 1 else "0:v"
                filtros.append(f"[{v}]trim=start={inicio:.8f}:end={fin:.8f},setpts=PTS-STARTPTS,{filtro_video}[v{j}]")
                if sonido:
                    a = f"ai{j}" if len(tramos) > 1 else "0:a"
                    filtros.append(f"[{a}]atrim=start={inicio:.8f}:end={fin:.8f},asetpts=PTS-STARTPTS,"
                                   f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                                   f"apad,atrim=duration={fin-inicio:.8f}[a{j}]")
                else:
                    filtros.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={fin-inicio:.8f},asetpts=PTS-STARTPTS[a{j}]")
            pares = "".join(f"[v{j}][a{j}]" for j in range(len(tramos)))
            filtros.append(f"{pares}concat=n={len(tramos)}:v=1:a=1[v][a]")
            parte = Path(tmp) / f"toma-{i:04d}.mp4"
            ejecutar(["ffmpeg", "-nostdin", "-v", "error", "-y", *entradas, "-filter_complex_threads", "1",
                      "-filter_complex", ";".join(filtros), "-map", "[v]", "-map", "[a]", "-t", f"{dur:.8f}",
                      "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-ar", "48000", "-ac", "2", str(parte)], toma["id"])
            partes.append(parte)
            duraciones.append(medir(parte)["duracion"])
        fundido_real = transicion(duraciones, fundido)
        entradas, filtros = [], []
        for i, parte in enumerate(partes):
            entradas += ["-threads", "1", "-i", str(parte)]
            filtros.append(f"[{i}:v]fps=24,settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
            filtros.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS,apad,atrim=duration={duraciones[i]:.8f}[a{i}]")
        if fundido_real:
            v, a, acumulado = "v0", "a0", duraciones[0]
            for i in range(1, len(partes)):
                filtros.append(f"[{v}][v{i}]xfade=transition=fade:duration={fundido_real:.8f}:offset={acumulado-fundido_real:.8f}[vx{i}]")
                filtros.append(f"[{a}][a{i}]acrossfade=d={fundido_real:.8f}:c1=tri:c2=tri[ax{i}]")
                v, a, acumulado = f"vx{i}", f"ax{i}", acumulado + duraciones[i] - fundido_real
        else:
            filtros.append("".join(f"[v{i}][a{i}]" for i in range(len(partes))) + f"concat=n={len(partes)}:v=1:a=1[v][a]")
            v, a, acumulado = "v", "a", sum(duraciones)
        avisar(f"Uniendo {len(partes)} tomas · fundido {fundido_real:.2f} s · {acumulado:.2f} s")
        ejecutar(["ffmpeg", "-nostdin", "-v", "error", "-y", *entradas, "-filter_complex_threads", "1",
                  "-filter_complex", ";".join(filtros), "-map", f"[{v}]", "-map", f"[{a}]", "-t", f"{acumulado:.8f}",
                  "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(salida)], "el video final")
    return {"duracion": medir(salida)["duracion"], "fundido": fundido_real}
