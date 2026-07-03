#!/usr/bin/env python3
"""
IPS — Índice de Precios de Supermercados de Tucumán (CIET).

Procesa dumps diarios de SEPA (Sistema Electrónico de Publicidad de Precios
Argentinos) y produce data/ips.json para el sitio.

Uso:
    python3 build_index.py DIR_DIA_ACTUAL [DIR_DIA_BASE] [-o SALIDA.json]

Cada DIR es la carpeta de un día del dump SEPA (contiene un .zip por comercio,
con comercio.csv, sucursales.csv y productos.csv separados por '|').

Metodología:
- Se toman solo sucursales con provincia AR-T (Tucumán).
- Precio de un producto en una cadena = mediana del precio de lista entre sus
  sucursales tucumanas.
- Canasta: EANs presentes en al menos MIN_CADENAS cadenas el día actual.
- Variación semanal: índice de Jevons (media geométrica de los relativos de
  precio) sobre pares (cadena, EAN) presentes en ambos días.
"""
import argparse
import csv
import io
import json
import math
import statistics
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

PROVINCIA = "AR-T"
MIN_PRODUCTOS = 1000     # una cadena es "principal" si releva >= este catálogo
TABLA_EJEMPLOS = 25      # productos mostrados en la tabla del sitio
# Cadenas que integran el índice. Las demás (con reporte parcial o marginal en
# Tucumán) se listan aparte pero no entran en la canasta ni en el titular.
CADENAS_OBJETIVO = {"Hipermercado Carrefour", "Vea", "Jumbo"}


def leer_csv(zf, nombre):
    """Itera filas (dict) de un CSV interno del zip de un comercio."""
    with zf.open(nombre) as fh:
        texto = io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace")
        lineas = (ln.replace("\0", "") for ln in texto)
        for fila in csv.DictReader(lineas, delimiter="|"):
            yield fila


def procesar_dia(dir_dia):
    """Devuelve {bandera: {"sucursales": n, "precios": {ean: precio_mediana},
    "descripciones": {ean: desc}}} para las sucursales de Tucumán."""
    cadenas = {}
    zips = sorted(Path(dir_dia).glob("*.zip"))
    if not zips:
        sys.exit(f"No hay zips de comercios en {dir_dia}")
    for z in zips:
        try:
            zf = zipfile.ZipFile(z)
        except zipfile.BadZipFile:
            continue
        nombres = {Path(n).name: n for n in zf.namelist()}
        if "sucursales.csv" not in nombres or "productos.csv" not in nombres:
            continue

        # una empresa (zip) puede tener varias banderas (p.ej. Carrefour:
        # Hiper, Maxi, Express); cada bandera es una cadena distinta
        nombres_bandera = {}
        if "comercio.csv" in nombres:
            for fila in leer_csv(zf, nombres["comercio.csv"]):
                nombres_bandera[(fila.get("id_bandera") or "").strip()] = (
                    (fila.get("comercio_bandera_nombre") or "").strip() or z.stem
                )

        sucs = {  # (id_bandera, id_sucursal) de Tucumán
            ((fila.get("id_bandera") or "").strip(), (fila.get("id_sucursal") or "").strip())
            for fila in leer_csv(zf, nombres["sucursales.csv"])
            if (fila.get("sucursales_provincia") or "").strip() == PROVINCIA
        }
        if not sucs:
            continue

        precios_por_ean = defaultdict(lambda: defaultdict(list))  # bandera → ean → [precios]
        descripciones = defaultdict(dict)
        for fila in leer_csv(zf, nombres["productos.csv"]):
            b = (fila.get("id_bandera") or "").strip()
            if (b, (fila.get("id_sucursal") or "").strip()) not in sucs:
                continue
            if (fila.get("productos_ean") or "").strip() != "1":
                continue  # solo productos identificados por EAN real
            ean = (fila.get("id_producto") or "").strip()
            try:
                precio = float(fila.get("productos_precio_lista") or 0)
            except ValueError:
                continue
            if not ean or precio <= 0:
                continue
            precios_por_ean[b][ean].append(precio)
            if ean not in descripciones[b]:
                descripciones[b][ean] = (fila.get("productos_descripcion") or "").strip()

        for b, por_ean in precios_por_ean.items():
            clave = nombres_bandera.get(b, f"{z.stem}-{b}")
            i = 2
            while clave in cadenas:
                clave = f"{nombres_bandera.get(b, z.stem)} ({i})"
                i += 1
            n_sucs = sum(1 for bb, _ in sucs if bb == b)
            cadenas[clave] = {
                "sucursales": n_sucs,
                "precios": {e: statistics.median(p) for e, p in por_ean.items()},
                "descripciones": dict(descripciones[b]),
            }
            print(f"  {clave}: {n_sucs} sucursales, {len(por_ean)} productos", file=sys.stderr)
    return cadenas


def jevons(actual, base):
    """Media geométrica de relativos de precio sobre claves comunes."""
    ratios = [
        math.log(actual[k] / base[k])
        for k in actual.keys() & base.keys()
        if base[k] > 0 and actual[k] > 0
    ]
    if not ratios:
        return None, 0
    return math.exp(sum(ratios) / len(ratios)), len(ratios)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dia_actual")
    ap.add_argument("dia_base", nargs="?")
    ap.add_argument("-o", "--salida", default="data/ips.json")
    args = ap.parse_args()

    fecha = Path(args.dia_actual).name
    print(f"Procesando día actual ({fecha})…", file=sys.stderr)
    hoy = procesar_dia(args.dia_actual)
    if not hoy:
        sys.exit("Ninguna cadena con sucursales en Tucumán.")

    # cadenas principales: las de la lista objetivo con catálogo completo; la
    # canasta es la intersección exacta de sus EANs, para que el costo sea
    # directamente comparable
    principales = [
        n for n, d in hoy.items()
        if len(d["precios"]) >= MIN_PRODUCTOS
        and (not CADENAS_OBJETIVO or n in CADENAS_OBJETIVO)
    ]
    if len(principales) < 2:
        sys.exit("Menos de dos cadenas objetivo con catálogo completo en Tucumán.")
    canasta = set(hoy[principales[0]]["precios"])
    for n in principales[1:]:
        canasta &= set(hoy[n]["precios"])
    canasta = sorted(canasta)
    if not canasta:
        sys.exit("Canasta vacía: ningún EAN compartido entre cadenas principales.")

    base = {}
    fecha_base = None
    if args.dia_base:
        fecha_base = Path(args.dia_base).name
        print(f"Procesando día base ({fecha_base})…", file=sys.stderr)
        base = procesar_dia(args.dia_base)

    cadenas_out = []
    pares_actual, pares_base = {}, {}
    for nombre, datos in sorted(hoy.items()):
        es_principal = nombre in principales
        var = None
        if nombre in base:
            idx, n = jevons(datos["precios"], base[nombre]["precios"])
            if idx and n >= 50:
                var = round((idx - 1) * 100, 2)
            if es_principal:  # el titular sale solo de cadenas principales
                for e in datos["precios"].keys() & base[nombre]["precios"].keys():
                    pares_actual[(nombre, e)] = datos["precios"][e]
                    pares_base[(nombre, e)] = base[nombre]["precios"][e]
        cadenas_out.append({
            "cadena": nombre,
            "principal": es_principal,
            "sucursales": datos["sucursales"],
            "productos_relevados": len(datos["precios"]),
            "canasta_costo": round(sum(datos["precios"][e] for e in canasta), 2)
                             if es_principal else None,
            "var_semanal_pct": var,
        })

    var_total = None
    n_pares = 0
    if pares_actual:
        idx, n_pares = jevons(pares_actual, pares_base)
        if idx:
            var_total = round((idx - 1) * 100, 2)

    def brecha(ean):
        ps = [hoy[n]["precios"][ean] for n in principales]
        return max(ps) / min(ps)

    def descripcion(ean):
        return next(
            (hoy[n]["descripciones"].get(ean) for n in principales
             if hoy[n]["descripciones"].get(ean)),
            ean,
        )

    def var_producto(ean):
        """Cambio semanal del producto: media geométrica de (hoy/base) entre
        las cadenas principales que lo tienen en ambas fechas."""
        rel = []
        for n in principales:
            if n in base and ean in base[n]["precios"]:
                b = base[n]["precios"][ean]
                if b > 0:
                    rel.append(math.log(hoy[n]["precios"][ean] / b))
        if not rel:
            return None
        return round((math.exp(sum(rel) / len(rel)) - 1) * 100, 2)

    # --- catálogo completo (archivo aparte para no inflar el dashboard) ---
    productos = []
    for ean in sorted(canasta, key=brecha, reverse=True):
        precios = {n: round(hoy[n]["precios"][ean], 2) for n in principales}
        vals = list(precios.values())
        productos.append({
            "ean": ean,
            "descripcion": descripcion(ean)[:80],
            "precios": precios,
            "min": min(vals),
            "max": max(vals),
            "brecha": round(max(vals) / min(vals), 3),
            "var_pct": var_producto(ean),
        })

    # --- datos para gráficos ---
    # histograma de brechas de precio entre cadenas
    cortes = [1.0, 1.05, 1.1, 1.2, 1.35, 1.5, 2.0, float("inf")]
    etiquetas = ["<5%", "5-10%", "10-20%", "20-35%", "35-50%", "50-100%", ">100%"]
    hist = [0] * len(etiquetas)
    for p in productos:
        for i in range(len(etiquetas)):
            if cortes[i] <= p["brecha"] < cortes[i + 1]:
                hist[i] += 1
                break
    histograma = [{"rango": e, "cantidad": c} for e, c in zip(etiquetas, hist)]

    # en cuántos productos cada cadena tiene el precio más bajo (o empatado)
    mas_barato = {n: 0 for n in principales}
    for ean in canasta:
        precios = {n: hoy[n]["precios"][ean] for n in principales}
        piso = min(precios.values())
        for n, pr in precios.items():
            if abs(pr - piso) < 0.01:
                mas_barato[n] += 1

    resumen = {
        "fecha": fecha,
        "fecha_base": fecha_base,
        "provincia": "Tucumán",
        "metodo": "Mediana por cadena entre sucursales AR-T; canasta = intersección "
                  "exacta de EANs de las cadenas con catálogo completo "
                  f"(>={MIN_PRODUCTOS} productos); variación por índice de Jevons.",
        "cadenas_principales": principales,
        "canasta_total_productos": len(canasta),
        "var_semanal_total_pct": var_total,
        "pares_comparados": n_pares,
        "cadenas": cadenas_out,
        "tabla": [
            {"ean": p["ean"], "descripcion": p["descripcion"], "precios": p["precios"]}
            for p in productos[:TABLA_EJEMPLOS]
        ],
        "histograma_brechas": histograma,
        "mas_barato": [{"cadena": n, "productos": mas_barato[n]} for n in principales],
    }

    catalogo = {
        "fecha": fecha,
        "fecha_base": fecha_base,
        "cadenas_principales": principales,
        "productos": productos,
    }

    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    cat_path = salida.parent / "productos.json"
    cat_path.write_text(json.dumps(catalogo, ensure_ascii=False), encoding="utf-8")
    print(f"OK → {salida} ({len(productos)} productos → {cat_path})", file=sys.stderr)


if __name__ == "__main__":
    main()
