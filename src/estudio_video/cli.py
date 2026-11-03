"""Línea de comandos `estudio`. Ningún comando gasta sin mostrar el estimado y pedir confirmación."""
import argparse
import datetime
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import costos, grafo, limpieza, receta
from .comfy import Cliente, ErrorComfy, mensaje_error

ESPERA_EN_CURSO = 60
REINTENTOS_EN_CURSO = 6


def _cliente(args):
    return Cliente(args.comfy)


def cmd_saldo(args):
    print(f"Saldo de OpenRouter: ${_cliente(args).saldo():.3f}")
    print("Tras un cobro, el saldo puede tardar unos minutos en actualizarse.")


def cmd_modelos(args):
    cat = costos.catalogo()
    for m in cat["videos" if args.tipo == "video" else "images"]:
        skus = m.get("pricing_skus") or {}
        print(m["id"], "|", json.dumps(skus)[:110])


def _preparar(ruta):
    r = receta.cargar(ruta)
    modelo = costos.buscar(costos.catalogo(), r["modelo"], r["tipo"])
    return r, costos.estimar(r, modelo)


def cmd_estimar(args):
    r, estimado = _preparar(args.receta)
    if estimado is None:
        print(f"{r['nombre']}: {r['modelo']}; sin estimado (el modelo no cobra por segundo o es una imagen). Tope: ${r['max_usd']}")
    else:
        print(f"{r['nombre']}: {r['modelo']}, {r.get('duracion')} s; estimado ${estimado:.2f}. Tope: ${r['max_usd']}")
    print(f"Saldo: ${_cliente(args).saldo():.3f}")


def cmd_generar(args):
    r, estimado = _preparar(args.receta)
    cli = _cliente(args)
    esquema = cli.esquema(grafo.NODOS[r["tipo"]])
    # Valida la receta contra el esquema antes de subir nada o gastar.
    locales = r["referencias"] + [r[k] for k in ("primer_cuadro", "ultimo_cuadro") if r.get(k)]
    grafo.construir(r, esquema, {p: "x" for p in locales})

    if estimado is None and not args.sin_estimado:
        sys.exit("No hay estimado para este modelo. Revisa su precio con `estudio modelos` y usa --sin-estimado si aceptas el riesgo.")
    if estimado is not None and estimado > float(r["max_usd"]):
        sys.exit(f"El estimado ${estimado:.2f} supera el tope de la receta (${r['max_usd']}). No se envió nada.")
    disponible = cli.saldo()
    if estimado is not None and estimado > disponible:
        sys.exit(f"El estimado ${estimado:.2f} supera el saldo (${disponible:.3f}). No se envió nada.")
    texto = "sin estimado" if estimado is None else f"unos ${estimado:.2f}"
    print(f"{r['nombre']}: {r['modelo']}, {texto}; saldo ${disponible:.3f}")
    if not args.si and input("¿Generar y gastar? Escribe sí: ").strip().lower() not in ("si", "sí"):
        sys.exit("Cancelado; no se envió nada.")

    subidas = {p: cli.subir_imagen(p) for p in locales}
    for intento in range(REINTENTOS_EN_CURSO + 1):
        g = grafo.construir(r, esquema, subidas)
        res = cli.ejecutar(g, args.limite)
        error = mensaje_error(res)
        if not error:
            break
        if "in-flight" in error and intento < REINTENTOS_EN_CURSO:
            print(f"OpenRouter aún descuenta un cobro anterior; no se cobró. Reintento en {ESPERA_EN_CURSO} s.", flush=True)
            time.sleep(ESPERA_EN_CURSO)
            continue
        if "maximum cost" in error:
            sys.exit("OpenRouter reserva el costo máximo por adelantado y no alcanza el saldo; no se cobró. Baja la duración o agrega crédito.")
        sys.exit(f"Falló: {error}")

    r["salida"].mkdir(parents=True, exist_ok=True)
    guardados = []
    for arch in res["outputs"]["a"]["images"]:
        final = r["salida"] / f"{r['nombre']}{Path(arch['filename']).suffix}"
        n = 2
        while final.exists():
            final = r["salida"] / f"{r['nombre']}-{n}{Path(arch['filename']).suffix}"
            n += 1
        cli.descargar(arch, final)
        guardados.append(final)
        print("Guardado:", final)
    costo = float(res["outputs"]["c"]["text"][0])
    meta = json.loads(res["outputs"]["m"]["text"][0])
    print(f"Costo real: ${costo:.4f}")
    with open(r["salida"] / "registro.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "fecha": datetime.datetime.now(datetime.timezone.utc).isoformat(), "nombre": r["nombre"], "modelo": r["modelo"],
            "costo_usd": costo, "estimado_usd": estimado, "archivos": [str(x) for x in guardados],
            "trabajo": meta.get("provider_job_id") or meta.get("local_job_id"),
        }, ensure_ascii=False) + "\n")


def cmd_revisar(args):
    hoja = Path(args.hoja or Path(args.video).with_suffix(".hoja.png"))
    info, errores, silencios = limpieza.revisar(args.video, hoja)
    print(info)
    print("Errores de decodificación:", errores or "ninguno")
    print("Silencios:", silencios or "ninguno")
    print("Hoja de cuadros:", hoja)
    print("Falta que una persona escuche el audio y apruebe el resultado.")


def cmd_blanquear(args):
    print(f"Fondo detectado: {limpieza.blanquear(args.entrada, args.salida)} % de la imagen")
    print("Guardado:", args.salida)


def cmd_web(args):
    for p in limpieza.web(args.video, args.destino, args.nombre, blanco=args.blanco):
        print(p, p.stat().st_size)


def main(argv=None):
    p = argparse.ArgumentParser(prog="estudio", description=__doc__)
    p.add_argument("--comfy", default=os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("saldo", help="Muestra el saldo de OpenRouter").set_defaults(f=cmd_saldo)
    m = sub.add_parser("modelos", help="Lista modelos y precios del catálogo")
    m.add_argument("--tipo", choices=["video", "imagen"], default="video")
    m.set_defaults(f=cmd_modelos)
    e = sub.add_parser("estimar", help="Estima el costo de una receta sin gastar")
    e.add_argument("receta")
    e.set_defaults(f=cmd_estimar)
    g = sub.add_parser("generar", help="Genera una receta (gasta saldo)")
    g.add_argument("receta")
    g.add_argument("--si", action="store_true", help="No preguntar; usar solo con aprobación previa de la persona")
    g.add_argument("--sin-estimado", action="store_true", help="Permite modelos sin estimado")
    g.add_argument("--limite", type=int, default=1800, help="Segundos máximos de espera")
    g.set_defaults(f=cmd_generar)
    r = sub.add_parser("revisar", help="Datos técnicos, silencios y hoja de cuadros")
    r.add_argument("video")
    r.add_argument("--hoja")
    r.set_defaults(f=cmd_revisar)
    b = sub.add_parser("blanquear", help="Pasa a blanco un fondo gris de cámara fija")
    b.add_argument("entrada")
    b.add_argument("salida")
    b.set_defaults(f=cmd_blanquear)
    w = sub.add_parser("web", help="Comprime para web y saca pósteres")
    w.add_argument("video")
    w.add_argument("--destino", default="web")
    w.add_argument("--nombre", default="video")
    w.add_argument("--blanco", action="store_true", help="Empuja el casi blanco a blanco puro")
    w.set_defaults(f=cmd_web)
    args = p.parse_args(argv)
    if args.cmd in ("revisar", "blanquear", "web") and not shutil.which("ffmpeg"):
        sys.exit("Este comando necesita ffmpeg")
    try:
        args.f(args)
    except (ErrorComfy, receta.ErrorReceta, ValueError, FileNotFoundError, TimeoutError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
