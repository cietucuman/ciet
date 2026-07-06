#!/usr/bin/env python3
"""
Buscador de precios (CIET) — catálogo amplio de las tiendas online de Tucumán.

Recorre una lista amplia de términos de búsqueda en cada cadena (VTEX) y baja
los productos (nombre, marca, precio, link), geolocalizado en Tucumán donde se
puede. Produce data/buscador.json, que el sitio busca del lado del navegador.

Uso:
    python3 fetch_buscador.py [--tope 150] [-o data/buscador.json]
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
CP = "4000"
TIENDAS = {
    "Carrefour": "www.carrefour.com.ar",
    "Vea": "www.vea.com.ar",
    "Jumbo": "www.jumbo.com.ar",
    "Comodín": "www.comodinencasa.com.ar",
    "ChangoMás": "www.masonline.com.ar",
}

TERMINOS = [
    # almacén
    "arroz", "fideos", "aceite", "aceite de oliva", "harina", "harina leudante",
    "azucar", "sal", "yerba", "mate cocido", "cafe", "cafe instantaneo", "te",
    "cacao", "mermelada", "dulce de leche", "miel", "polenta", "pure de papas",
    "lentejas", "porotos", "garbanzos", "arvejas", "choclo", "tomate perita",
    "salsa de tomate", "pure de tomate", "atun", "caballa", "sardina",
    "aceitunas", "mayonesa", "ketchup", "mostaza", "vinagre", "caldo", "sopa",
    "gelatina", "flan", "postre", "galletitas", "galletitas dulces",
    "galletitas de agua", "tostadas", "pan lactal", "budin", "alfajor",
    "chocolate", "caramelos", "chicles", "papas fritas", "palitos", "mani",
    "frutos secos", "cereales", "avena", "granola", "barritas de cereal",
    "arroz integral", "salvado", "condimentos", "oregano", "pimienta",
    "aderezos", "escabeche", "picadillo", "leche condensada",
    # lácteos
    "leche", "leche descremada", "leche en polvo", "yogur", "yogur bebible",
    "queso", "queso cremoso", "queso rallado", "queso untable", "manteca",
    "margarina", "crema de leche", "ricota", "postre lacteo",
    # bebidas
    "gaseosa", "coca cola", "agua mineral", "agua saborizada", "jugo",
    "jugo en polvo", "cerveza", "vino", "vino tinto", "fernet", "aperitivo",
    "energizante", "isotonica", "soda", "gaseosa lima limon", "amargo",
    "whisky", "vodka", "gin", "sidra", "champagne",
    # congelados
    "helado", "hamburguesa", "milanesa de soja", "nuggets", "papas congeladas",
    "verduras congeladas", "pizza congelada", "medallon",
    # frescos / carnes / fiambres
    "pollo", "carne picada", "milanesa", "jamon", "jamon cocido", "salame",
    "mortadela", "salchicha", "chorizo", "queso de maquina", "huevos",
    "pan", "prepizza", "tapa empanada", "tapa tarta", "ravioles", "ñoquis",
    # frutas y verduras
    "banana", "manzana", "naranja", "papa", "cebolla", "tomate", "lechuga",
    "zanahoria", "limon", "zapallo", "morron",
    # limpieza
    "detergente", "lavandina", "jabon en polvo", "jabon liquido ropa",
    "suavizante", "limpiador", "limpiador de piso", "lustramuebles",
    "desodorante de ambiente", "insecticida", "papel higienico",
    "rollo de cocina", "servilletas", "esponja", "bolsas de residuo",
    "film", "papel aluminio", "trapo de piso", "escoba", "cif", "desengrasante",
    # perfumería / higiene
    "shampoo", "acondicionador", "jabon de tocador", "crema corporal",
    "desodorante", "pasta dental", "cepillo dental", "enjuague bucal",
    "espuma de afeitar", "maquina de afeitar", "toallitas femeninas",
    "protectores diarios", "algodon", "hisopos", "alcohol en gel",
    "crema de enjuague", "gel de ducha",
    # bebé
    "pañales", "toallitas humedas", "leche infantil", "papilla", "oleo calcareo",
    # mascotas
    "alimento perro", "alimento gato", "arena para gatos",
    # otros
    "pilas", "encendedor", "velas", "fosforos", "servilletas de papel",
]


def get(url, intentos=2):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == intentos - 1:
                return None
            time.sleep(0.6)
    return None


def region_id(dom):
    d = get(f"https://{dom}/api/checkout/pub/regions/?country=ARG&postalCode={CP}")
    return d[0].get("id") if isinstance(d, list) and d else None


def productos_termino(dom, termino, region, tope):
    out, frm = [], 0
    ft = urllib.parse.quote(termino)
    while frm < tope:
        url = (f"https://{dom}/api/catalog_system/pub/products/search"
               f"?ft={ft}&_from={frm}&_to={frm+49}")
        if region:
            url += f"&regionId={urllib.parse.quote(region)}"
        d = get(url)
        if not isinstance(d, list) or not d:
            break
        for p in d:
            try:
                item = p["items"][0]
                precio = item["sellers"][0]["commertialOffer"].get("Price")
                if not precio or precio < 100:  # descarta precios basura (errores de carga)
                    continue
                out.append({
                    "n": p.get("productName", "")[:90],
                    "m": (p.get("brand") or "")[:28],
                    "e": item.get("ean") or "",
                    "p": round(precio, 2),
                    "l": p.get("link") or "",
                })
            except Exception:
                continue
        if len(d) < 50:
            break
        frm += 50
        time.sleep(0.07)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tope", type=int, default=150, help="máx productos por término")
    ap.add_argument("-o", "--salida", default="data/buscador.json")
    args = ap.parse_args()

    cadenas_meta, productos = {}, []
    for nombre, dom in TIENDAS.items():
        region = region_id(dom)
        print(f"{nombre}: geoloc={'sí' if region else 'no'}", file=sys.stderr)
        vistos, n0 = set(), len(productos)
        for i, term in enumerate(TERMINOS, 1):
            for pr in productos_termino(dom, term, region, args.tope):
                clave = pr["e"] or pr["l"]
                if not clave or clave in vistos:
                    continue
                vistos.add(clave)
                pr["c"] = nombre
                productos.append(pr)
            if i % 30 == 0:
                print(f"  {nombre}: {i}/{len(TERMINOS)} términos · {len(productos)-n0} productos",
                      file=sys.stderr)
        cadenas_meta[nombre] = {"geolocalizado": bool(region), "productos": len(productos) - n0}
        print(f"  {nombre}: {len(productos)-n0} productos", file=sys.stderr)

    for p in productos:  # el EAN sólo servía para deduplicar; no va al archivo final
        p.pop("e", None)
    out = {
        "fecha": time.strftime("%Y-%m-%d"),
        "cadenas": cadenas_meta,
        "total": len(productos),
        "productos": productos,
    }
    Path(args.salida).write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK → {args.salida} ({len(productos)} productos)", file=sys.stderr)


if __name__ == "__main__":
    main()
