#!/usr/bin/env python3
"""
Precios de las tiendas online (CIET / IPS).

Consulta las APIs de catálogo VTEX de Carrefour, Vea y Jumbo por código EAN,
geolocalizadas en Tucumán, para comparar el precio publicado en la web contra
el que la cadena informa a SEPA. Guarda data/web_precios.json.

Uso:
    python3 fetch_web.py [--muestra N] [--catalogo data/productos.json]

Es un relevamiento de baja intensidad (una consulta por producto y cadena, con
pausa entre pedidos) sobre endpoints públicos. No apto para uso comercial.
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
CP_TUCUMAN = "4000"

# nombre SEPA -> dominio de la tienda online
TIENDAS = {
    "Hipermercado Carrefour": "www.carrefour.com.ar",
    "Vea": "www.vea.com.ar",
    "Jumbo": "www.jumbo.com.ar",
}


def get(url, intentos=2):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as e:
            if i == intentos - 1:
                return None
            time.sleep(1.0)
    return None


def region_id(dominio):
    """RegionId VTEX para el CP de Tucumán (precios geolocalizados)."""
    url = (f"https://{dominio}/api/checkout/pub/regions/"
           f"?country=ARG&postalCode={CP_TUCUMAN}")
    d = get(url)
    if isinstance(d, list) and d:
        return d[0].get("id")
    return None


def precio_por_ean(dominio, ean, region):
    q = urllib.parse.quote(f"alternateIds_Ean:{ean}")
    url = f"https://{dominio}/api/catalog_system/pub/products/search?fq={q}"
    if region:
        url += f"&regionId={urllib.parse.quote(region)}"
    d = get(url)
    if not isinstance(d, list) or not d:
        return None
    prod = d[0]
    # elegir el item cuyo EAN coincide exactamente
    item = next((it for it in prod.get("items", []) if it.get("ean") == ean),
                (prod.get("items") or [None])[0])
    if not item:
        return None
    ofertas = [s.get("commertialOffer", {}) for s in item.get("sellers", [])]
    ofertas = [o for o in ofertas if o.get("Price")]
    if not ofertas:
        return {"precio": None, "link": prod.get("link"), "disponible": False}
    mejor = min(ofertas, key=lambda o: o["Price"])
    return {
        "precio": round(mejor["Price"], 2),
        "link": prod.get("link"),
        "disponible": bool(mejor.get("IsAvailable", mejor.get("AvailableQuantity", 0))),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestra", type=int, default=250)
    ap.add_argument("--catalogo", default="data/productos.json")
    ap.add_argument("-o", "--salida", default="data/web_precios.json")
    args = ap.parse_args()

    cat = json.loads(Path(args.catalogo).read_text(encoding="utf-8"))
    productos = cat["productos"]
    # muestra representativa: repartida a lo largo del catálogo (no solo top)
    if args.muestra and args.muestra < len(productos):
        paso = len(productos) / args.muestra
        idx = sorted({int(i * paso) for i in range(args.muestra)})
        muestra = [productos[i] for i in idx]
    else:
        muestra = productos

    print(f"Resolviendo regiones de Tucumán…", file=sys.stderr)
    regiones = {n: region_id(dom) for n, dom in TIENDAS.items()}
    for n, r in regiones.items():
        print(f"  {n}: {'ok' if r else 'SIN REGIÓN'}", file=sys.stderr)

    precios = {}
    total = len(muestra)
    hallados = {n: 0 for n in TIENDAS}
    for i, p in enumerate(muestra, 1):
        ean = p["ean"]
        fila = {}
        for n, dom in TIENDAS.items():
            res = precio_por_ean(dom, ean, regiones[n])
            if res and res.get("precio"):
                fila[n] = res
                hallados[n] += 1
            time.sleep(0.25)  # respetuoso con el servidor
        if fila:
            fila["descripcion"] = p["descripcion"]
            precios[ean] = fila
        if i % 25 == 0 or i == total:
            print(f"  {i}/{total} · hallados {dict(hallados)}", file=sys.stderr)

    out = {
        "fecha": time.strftime("%Y-%m-%d"),
        "fuente": "Tiendas online (plataforma VTEX), precios geolocalizados en "
                  f"Tucumán (CP {CP_TUCUMAN}).",
        "cadenas": list(TIENDAS),
        "muestra": total,
        "hallados": hallados,
        "precios": precios,
    }
    Path(args.salida).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK → {args.salida} ({len(precios)} productos con precio web)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
