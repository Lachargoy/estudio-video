"""Genera una receta con las reglas de gasto. La usan el CLI y el panel; ninguno gasta sin `confirmar`."""
import datetime
import json
import shutil
import tempfile
import time
from pathlib import Path

from . import costos, grafo, limpieza, receta
from .comfy import mensaje_error

ESPERA_EN_CURSO = 60
REINTENTOS_EN_CURSO = 6


class ErrorGeneracion(RuntimeError):
    """Algo impidió generar. El mensaje dice si se envió o se cobró algo."""


def preparar(ruta):
    """Receta validada y costo estimado (None si el modelo no cobra por segundo o es una imagen)."""
    r = receta.cargar(ruta)
    modelo = costos.buscar(costos.catalogo(), r["modelo"], r["tipo"])
    return r, costos.estimar(r, modelo)


def generar(ruta, cliente, confirmar, *, sin_estimado=False, limite=1800, avisar=print, esperar=time.sleep):
    """Valida, revisa tope y saldo, pide `confirmar(receta, estimado, saldo)` y solo entonces envía.

    Devuelve {"nombre", "archivos", "costo_usd", "estimado_usd"}. Lanza ErrorGeneracion sin gastar
    si algo no cuadra antes del envío.
    """
    with tempfile.TemporaryDirectory(prefix="estudio-") as carpeta:
        r, estimado = preparar(ruta)
        if r.get("continuar_de"):
            if not shutil.which("ffmpeg"):
                raise ErrorGeneracion("«continuar_de» necesita ffmpeg")
            r["primer_cuadro"] = limpieza.ultimo_cuadro(r["continuar_de"], Path(carpeta) / "continuacion.png")
        esquema = cliente.esquema(grafo.NODOS[r["tipo"]])
        # Valida la receta contra el esquema antes de subir nada o gastar.
        locales = r["referencias"] + [r[k] for k in ("primer_cuadro", "ultimo_cuadro") if r.get(k)]
        grafo.construir(r, esquema, {p: "x" for p in locales})

        if estimado is None and not sin_estimado:
            raise ErrorGeneracion("No hay estimado para este modelo. Revisa su precio con `estudio modelos` "
                                  "y usa --sin-estimado si aceptas el riesgo.")
        if estimado is not None and estimado > float(r["max_usd"]):
            raise ErrorGeneracion(f"El estimado ${estimado:.2f} supera el tope de la receta (${r['max_usd']}). No se envió nada.")
        disponible = cliente.saldo()
        if estimado is not None and estimado > disponible:
            raise ErrorGeneracion(f"El estimado ${estimado:.2f} supera el saldo (${disponible:.3f}). No se envió nada.")
        texto = "sin estimado" if estimado is None else f"unos ${estimado:.2f}"
        avisar(f"{r['nombre']}: {r['modelo']}, {texto}; saldo ${disponible:.3f}")
        if not confirmar(r, estimado, disponible):
            raise ErrorGeneracion("Cancelado; no se envió nada.")

        subidas = {p: cliente.subir(p) for p in locales}
        for intento in range(REINTENTOS_EN_CURSO + 1):
            g = grafo.construir(r, esquema, subidas)
            res = cliente.ejecutar(g, limite)
            error = mensaje_error(res)
            if not error:
                break
            if "in-flight" in error and intento < REINTENTOS_EN_CURSO:
                avisar(f"OpenRouter aún descuenta un cobro anterior; no se cobró. Reintento en {ESPERA_EN_CURSO} s.")
                esperar(ESPERA_EN_CURSO)
                continue
            if "maximum cost" in error:
                raise ErrorGeneracion("OpenRouter reserva el costo máximo por adelantado y no alcanza el saldo; "
                                      "no se cobró. Baja la duración o agrega crédito.")
            raise ErrorGeneracion(f"Falló: {error}")

        r["salida"].mkdir(parents=True, exist_ok=True)
        guardados = []
        for arch in res["outputs"]["a"]["images"]:
            sufijo = Path(arch["filename"]).suffix
            final = r["salida"] / f"{r['nombre']}{sufijo}"
            n = 2
            while final.exists():
                final = r["salida"] / f"{r['nombre']}-{n}{sufijo}"
                n += 1
            cliente.descargar(arch, final)
            guardados.append(final)
            avisar(f"Guardado: {final}")
        costo = float(res["outputs"]["c"]["text"][0])
        meta = json.loads(res["outputs"]["m"]["text"][0])
        avisar(f"Costo real: ${costo:.4f}")
        with open(r["salida"] / "registro.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "fecha": datetime.datetime.now(datetime.timezone.utc).isoformat(), "nombre": r["nombre"], "modelo": r["modelo"],
                "costo_usd": costo, "estimado_usd": estimado, "archivos": [str(x) for x in guardados],
                "trabajo": meta.get("provider_job_id") or meta.get("local_job_id"),
                "continuar_de": str(r["continuar_de"]) if r.get("continuar_de") else None,
            }, ensure_ascii=False) + "\n")
        return {"nombre": r["nombre"], "archivos": [str(x) for x in guardados], "costo_usd": costo, "estimado_usd": estimado}
