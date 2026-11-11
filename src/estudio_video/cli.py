"""Línea de comandos `estudio`. Ningún comando gasta sin mostrar el estimado y pedir confirmación."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import costos, generacion, limpieza, receta
from .comfy import Cliente, ErrorComfy


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
    return generacion.preparar(ruta)


def cmd_estimar(args):
    r, estimado = _preparar(args.receta)
    if estimado is None:
        print(f"{r['nombre']}: {r['modelo']}; sin estimado (el modelo no cobra por segundo o es una imagen). Tope: ${r['max_usd']}")
    else:
        print(f"{r['nombre']}: {r['modelo']}, {r.get('duracion')} s; estimado ${estimado:.2f}. Tope: ${r['max_usd']}")
    print(f"Saldo: ${_cliente(args).saldo():.3f}")


def cmd_generar(args):
    def confirmar(r, estimado, disponible):
        return args.si or input("¿Generar y gastar? Escribe sí: ").strip().lower() in ("si", "sí")
    generacion.generar(args.receta, _cliente(args), confirmar, sin_estimado=args.sin_estimado,
                       limite=args.limite, avisar=lambda t: print(t, flush=True))


def cmd_panel(args):
    from . import panel
    panel.servir(args.carpeta, args.puerto, comfy=args.comfy, abrir=args.abrir)


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


def cmd_ultimo_cuadro(args):
    print("Guardado:", limpieza.ultimo_cuadro(args.video, args.salida))


def cmd_cola(args):
    print("Guardado:", limpieza.cola_video(args.video, args.salida, args.segundos))
    print("OpenRouter necesita este fragmento en un enlace HTTPS público. Confirma que se puede publicar antes de subirlo.")


def cmd_unir(args):
    print("Guardado:", limpieza.unir(args.videos, args.salida, args.recortar_inicio, args.fundido))


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
    u = sub.add_parser("ultimo-cuadro", help="Extrae el último cuadro de un video")
    u.add_argument("video")
    u.add_argument("salida")
    u.set_defaults(f=cmd_ultimo_cuadro)
    o = sub.add_parser("cola", help="Prepara los últimos segundos de un video para usarlos como referencia")
    o.add_argument("video")
    o.add_argument("salida")
    o.add_argument("--segundos", type=float, default=2.0)
    o.set_defaults(f=cmd_cola)
    n = sub.add_parser("unir", help="Une tomas en un solo video")
    n.add_argument("videos", nargs="+")
    n.add_argument("-o", "--salida", required=True)
    n.add_argument("--recortar-inicio", type=int, default=0, help="Cuadros a quitar al inicio de cada toma siguiente; usa 1 tras `continuar_de`")
    n.add_argument("--fundido", type=float, default=0.0, help="Segundos de fundido entre tomas")
    n.set_defaults(f=cmd_unir)
    w = sub.add_parser("web", help="Comprime para web y saca pósteres")
    w.add_argument("video")
    w.add_argument("--destino", default="web")
    w.add_argument("--nombre", default="video")
    w.add_argument("--blanco", action="store_true", help="Empuja el casi blanco a blanco puro")
    w.set_defaults(f=cmd_web)
    pa = sub.add_parser("panel", help="Panel local para ver recetas, costos y resultados, y generar con aprobación")
    pa.add_argument("carpeta", nargs="?", default=".", help="Carpeta con recetas (por defecto, la actual)")
    pa.add_argument("--puerto", type=int, default=4300)
    pa.add_argument("--abrir", action="store_true", help="Abre el panel en el navegador")
    pa.set_defaults(f=cmd_panel)
    args = p.parse_args(argv)
    if args.cmd in ("revisar", "blanquear", "web", "ultimo-cuadro", "cola", "unir") and not shutil.which("ffmpeg"):
        sys.exit("Este comando necesita ffmpeg")
    try:
        args.f(args)
    except subprocess.CalledProcessError as e:
        sys.exit(f"Error de ffmpeg: {(e.stderr or '').strip()[:500] or e}")
    except generacion.ErrorGeneracion as e:
        sys.exit(str(e))
    except (ErrorComfy, receta.ErrorReceta, ValueError, FileNotFoundError, TimeoutError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
