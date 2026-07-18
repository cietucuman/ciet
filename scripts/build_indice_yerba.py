#!/usr/bin/env python3
"""
Índice de Precios de Yerbas de Tucumán (CIET).

Lee todas las capturas crudas diarias (data/yerba/crudo/AAAA-MM-DD.json, que produce
fetch_buscador.py con --terminos yerba) y arma el índice y su desglose completo.

METODOLOGÍA (defendible, la que usan los institutos con precios scrapeados):
  · Universo: yerbas de 1 kg exacto, TODAS las marcas.
  · Item elemental = (producto × cadena). "Playadito 1kg en Vea" es una serie;
    "Playadito 1kg en Jumbo" es otra. Comparamos ofertas idénticas.
  · Identidad del producto = clave_fuzzy(nombre, marca) del buscador (normaliza
    mayúsculas/acentos y une el mismo artículo entre cadenas). Es estable día a día.
  · Índice = Jevons ENCADENADO: para cada par de días consecutivos, media
    GEOMÉTRICA de las relativas de precio (p_t / p_{t-1}) sobre los items presentes
    en AMBOS días. Los que entran o salen no contaminan la transición (elimina el
    sesgo de composición). Base 100 el primer día.
  · Sin ponderar (cada item pesa igual): no hay datos de ventas. Transparente.
  · Filtro de outliers: se descarta una relativa fuera de [0.33, 3.0] (que el precio
    caiga a menos de un tercio o más que triplique de un día para otro es casi seguro
    un error de dato o un cambio de producto bajo la misma clave; las promos habituales,
    incluso descuentos profundos, quedan dentro), y se reporta cuántas se descartaron.
  · Se incluyen precios de promo: es el precio de góndola real disponible al público.

Además del índice general, para TRANSPARENCIA total se guarda la serie de cada
item (producto × cadena) y stats descriptivas por día. La página puede desglosar
todo: cada yerba, cada cadena, su evolución.

Uso:
    python3 build_indice_yerba.py [--crudo data/yerba/crudo] [-o data/yerba/indice.json]
"""
import argparse
import json
import math
import re
import statistics
import sys
import unicodedata
from pathlib import Path

# Reusamos la identidad de producto del buscador (misma lógica que une el mismo
# artículo entre cadenas), para que la clave sea idéntica a la del sitio.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_buscador import clave_fuzzy, _norm  # noqa: E402

# Banda de plausibilidad para una relativa de precio día-a-día. Fuera de esto se
# considera error de dato / cambio de producto y no entra al índice.
REL_MIN, REL_MAX = 0.33, 3.0


def gramaje(nombre):
    """Gramos del paquete a partir del nombre, o None si no se puede leer."""
    s = _norm(nombre).replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*kg", s)
    if m:
        return int(round(float(m.group(1)) * 1000))
    m = re.search(r"(\d+)\s*g(?:r|rs)?\b", s)
    if m:
        return int(m.group(1))
    return None


def es_multipack(nombre):
    """Detecta packs (x2, x3, 'pack de 3'…): su gramaje unitario engaña al filtro."""
    s = _norm(nombre)
    return bool(re.search(r"\b(pack|x\s*[2-9]|[2-9]\s*x)\b", s))


def yerbas_1kg(dia):
    """De una captura cruda, devuelve {clave_producto: {n, m, i, precios:{cad:precio}}}
    quedándose sólo con yerbas de 1 kg exacto (no packs)."""
    out = {}
    for p in dia.get("productos", []):
        nombre = p.get("n", "")
        if gramaje(nombre) != 1000 or es_multipack(nombre):
            continue
        precios = {}
        for cad, o in (p.get("pr") or {}).items():
            try:
                precio = float(o[0])
            except (TypeError, ValueError, IndexError):
                continue
            if precio > 0:
                precios[cad] = round(precio, 2)
        if not precios:
            continue
        clave = clave_fuzzy(nombre, p.get("m", ""))
        if not clave:
            continue
        if clave in out:
            # dos rótulos que colapsan a la misma clave el mismo día: nos quedamos
            # con el precio mínimo por cadena (misma política que el buscador).
            prev = out[clave]["precios"]
            for cad, pr in precios.items():
                if cad not in prev or pr < prev[cad]:
                    prev[cad] = pr
        else:
            out[clave] = {"n": nombre, "m": p.get("m", ""),
                          "i": p.get("i", ""), "precios": precios}
    return out


def geomean(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crudo", default="data/yerba/crudo",
                    help="carpeta con las capturas crudas AAAA-MM-DD.json")
    ap.add_argument("-o", "--salida", default="data/yerba/indice.json")
    args = ap.parse_args()

    archivos = sorted(Path(args.crudo).glob("*.json"))
    fechas_arch = [(f.stem, f) for f in archivos
                   if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem)]
    if not fechas_arch:
        print(f"No hay capturas en {args.crudo}", file=sys.stderr)
        sys.exit(1)

    fechas = [d for d, _ in fechas_arch]
    # {fecha: {clave: {n,m,i,precios}}}
    dias = {}
    for fecha, f in fechas_arch:
        dias[fecha] = yerbas_1kg(json.loads(f.read_text(encoding="utf-8")))

    # metadatos de cada producto (último nombre/marca/imagen conocidos) y cadenas.
    meta = {}
    cadenas = set()
    for fecha in fechas:
        for clave, info in dias[fecha].items():
            meta[clave] = {"nombre": info["n"], "marca": info["m"], "img": info["i"]}
            cadenas.update(info["precios"].keys())
    cadenas = sorted(cadenas)
    claves = sorted(meta.keys())

    # serie de precios por item (producto × cadena): precio por fecha o None.
    #   precios_item[(clave, cad)] = [p_fecha0, p_fecha1, ...]
    precios_item = {}
    for clave in claves:
        for cad in cadenas:
            serie = []
            for fecha in fechas:
                info = dias[fecha].get(clave)
                serie.append(info["precios"].get(cad) if info else None)
            # sólo guardamos series con al menos un dato
            if any(v is not None for v in serie):
                precios_item[(clave, cad)] = serie

    # ÍNDICE GENERAL — Jevons encadenado sobre items (producto × cadena).
    indice = [100.0]
    n_emparejados = [None]        # items que entraron en cada transición (transparencia)
    n_descartados = [None]        # relativas descartadas por outlier
    for t in range(1, len(fechas)):
        relativas = []
        descartadas = 0
        for serie in precios_item.values():
            a, b = serie[t - 1], serie[t]
            if a and b:
                r = b / a
                if REL_MIN <= r <= REL_MAX:
                    relativas.append(r)
                else:
                    descartadas += 1
        if relativas:
            indice.append(round(indice[-1] * geomean(relativas), 4))
        else:
            # sin emparejados: el índice no se mueve (arrastra el nivel).
            indice.append(indice[-1])
        n_emparejados.append(len(relativas))
        n_descartados.append(descartadas)

    # ÍNDICE POR PRODUCTO (geomean de sus cadenas emparejadas) — para el drill-down.
    productos = []
    for clave in claves:
        series_cad = {cad: precios_item[(clave, cad)]
                      for cad in cadenas if (clave, cad) in precios_item}
        idx_prod = [100.0]
        for t in range(1, len(fechas)):
            rels = []
            for serie in series_cad.values():
                a, b = serie[t - 1], serie[t]
                if a and b and REL_MIN <= b / a <= REL_MAX:
                    rels.append(b / a)
            idx_prod.append(round(idx_prod[-1] * geomean(rels), 4) if rels else idx_prod[-1])
        productos.append({
            "id": clave,
            "nombre": meta[clave]["nombre"],
            "marca": meta[clave]["marca"],
            "img": meta[clave]["img"],
            "series": series_cad,           # {cadena: [precio|null por fecha]}
            "indice": idx_prod,             # índice del producto (base 100)
        })

    # STATS DESCRIPTIVAS POR DÍA (niveles; $/kg = precio porque son de 1 kg).
    # Un precio por producto = mínimo entre cadenas (la mejor oferta disponible).
    stats = []
    for i, fecha in enumerate(fechas):
        por_producto = []
        for clave, info in dias[fecha].items():
            if info["precios"]:
                por_producto.append(min(info["precios"].values()))
        if por_producto:
            stats.append({
                "fecha": fecha,
                "n_productos": len(por_producto),
                "n_items": sum(1 for s in precios_item.values() if s[i] is not None),
                "mediana": round(statistics.median(por_producto), 2),
                "promedio": round(statistics.mean(por_producto), 2),
                "min": round(min(por_producto), 2),
                "max": round(max(por_producto), 2),
            })
        else:
            stats.append({"fecha": fecha, "n_productos": 0, "n_items": 0})

    out = {
        "actualizado": fechas[-1],
        "base": fechas[0],
        "metodologia": ("Índice de Jevons encadenado (media geométrica de relativas "
                        "de precio) sobre items producto×cadena, yerbas de 1 kg, "
                        "todas las marcas. Base 100 = " + fechas[0] + ". Sin ponderar. "
                        "Se descartan relativas fuera de [0.33, 3.0] por día."),
        "fechas": fechas,
        "cadenas": cadenas,
        "indice": indice,
        "n_emparejados": n_emparejados,
        "n_descartados": n_descartados,
        "stats": stats,
        "productos": productos,
    }
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK → {args.salida}: {len(fechas)} día(s), {len(claves)} productos, "
          f"{len(precios_item)} items (producto×cadena), índice final "
          f"{indice[-1]:.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
