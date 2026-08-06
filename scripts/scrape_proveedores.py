#!/usr/bin/env python3
"""
Motor de PROVEEDORES (CIET) — dónde comprar al por mayor los productos ganadores.

Busca cada producto ganador en los catálogos mayoristas argentinos que publican
precios de forma pública, y arma el match "producto ganador → proveedor".

Cómo lee los catálogos: casi todos los mayoristas AR corren sobre TiendaNube o
WooCommerce, y ambos emiten JSON-LD (schema.org/Product) en la página de
resultados. Eso da nombre, precio, moneda, imagen y link sin depender del HTML,
que cambia seguido. No hace falta navegador: son requests simples.

IMPORTANTE — proveedores con clave: los catálogos que piden contraseña (Kiran
Import, Fyn Tecno) NO se scrapean ni se publican. El repo del CIET es público, y
publicar precios de un catálogo privado expondría datos que el proveedor eligió
no mostrar, además de arriesgar tu relación comercial. Van en el directorio como
ficha de contacto, para consultarlos a mano.

Uso:
    python3 scripts/scrape_proveedores.py -o /tmp/proveedores_ar.json
    python3 scripts/scrape_proveedores.py --productos /tmp/productos_ar.json
"""
import argparse
import concurrent.futures
import datetime
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Proveedores. `buscar` = plantilla de búsqueda ({q} = término). Si es None, el
# proveedor va sólo al directorio (no tiene catálogo público consultable).
PROVEEDORES = [
    {
        "id": "desellershub", "nombre": "DeSellersHub",
        "url": "https://www.desellershub.com",
        "buscar": "https://www.desellershub.com/search/?q={q}",
        "plataforma": "TiendaNube", "rubro": "Electrónica y tecnología",
        "nota": "Venta por bulto cerrado, precios en USD. Envíos a todo el país.",
    },
    {
        "id": "importadoraelectro", "nombre": "Importadora Electro",
        "url": "https://importadoraelectro.com",
        "buscar": "https://importadoraelectro.com/search/?q={q}",
        "plataforma": "TiendaNube", "rubro": "Accesorios de celular y electrónica",
        "nota": "Precios en pesos, compra online.",
    },
    {
        "id": "once", "nombre": "Once.ar",
        "url": "https://www.once.ar",
        "buscar": "https://www.once.ar/?s={q}&post_type=product",
        "plataforma": "WooCommerce", "rubro": "Bazar, electrónica y hogar",
        "nota": "Mayorista del Once online. Pedido mínimo $80.000.",
    },
    {
        "id": "newred", "nombre": "NewRed Mayorista",
        "url": "https://newredmayorista.com.ar",
        "buscar": "https://newredmayorista.com.ar/?s={q}&post_type=product",
        "plataforma": "WooCommerce", "rubro": "Electrónica, hogar y electrodomésticos",
        "nota": "Catálogo online abierto, pero los precios son para mayoristas: "
                "no los publica en la web, se consultan al pedir. Compra mínima 3 unidades.",
        "contacto": "https://www.instagram.com/newred.central/",
    },
    # --- Directorio (sin catálogo público consultable) ---
    {
        "id": "kiran", "nombre": "Kiran Import", "url": "https://kiranimport.com",
        "buscar": None, "plataforma": "Catálogo con clave", "rubro": "Importados variados",
        "nota": "El catálogo pide contraseña. No se scrapea: es privado del proveedor. "
                "Consultá a mano; la clave se pide por Instagram.",
        "contacto": "https://www.instagram.com/kiranimport/",
    },
    {
        "id": "fyntecno", "nombre": "Fyn Tecno", "url": "https://fyntecno.com",
        "buscar": None, "plataforma": "Catálogo con clave", "rubro": "Tecnología",
        "nota": "El catálogo pide clave de acceso. No se scrapea: consultá a mano.",
    },
    {
        "id": "lambotech", "nombre": "LamboTech", "url": "https://lambotecharg.com",
        "buscar": None, "catalogo_local": "catalogo_lambo.json",
        "plataforma": "Catálogo PDF", "rubro": "Importador directo — electrónica, bazar y más",
        "nota": "Importador directo. Su catálogo son PDFs por rubro (Drive), no una web consultable: se revisan a mano o se cargan aparte.",
        "contacto": "https://www.instagram.com/lambotechstore/",
    },
]


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def dolar():
    """Dólar blue, para poder comparar costos en USD con precios en pesos."""
    try:
        req = urllib.request.Request("https://dolarapi.com/v1/dolares/blue",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            return float(json.loads(r.read())["venta"])
    except Exception:
        return 1500.0


# Cuánto se multiplica el costo mayorista para llegar al precio de venta al
# público en venta directa (WhatsApp / contra entrega). 2,5× es el piso habitual.
MARKUP = 2.5
# Margen en pesos a partir del cual un producto sirve para el modelo "high ticket":
# pocas ventas por semana, pero cada una deja mucho.
UMBRAL_ALTO = 100_000
UMBRAL_MEDIO = 40_000


def costo_unitario(fila, tc):
    """Costo por unidad en pesos.

    Varios mayoristas (DeSellersHub) venden por bulto cerrado y ponen el precio
    unitario en el nombre ("Precio Unitario $47.42 USD"); ese es el número que
    importa, no el total del bulto. Si no está, se usa el precio publicado.
    """
    m = re.search(r"Precio\s+Unitario:?\s*\$?\s*([0-9]+(?:[.,][0-9]+)?)\s*USD",
                  fila.get("nombre") or "", re.I)
    if m:
        try:
            return float(m.group(1).replace(",", ".")) * tc
        except ValueError:
            pass
    precio = fila.get("precio")
    if precio is None:
        return None
    return precio * tc if (fila.get("moneda") == "USD") else precio


def segmentar(margen):
    if margen is None:
        return None
    if margen >= UMBRAL_ALTO:
        return "alto"
    if margen >= UMBRAL_MEDIO:
        return "medio"
    return "bajo"


def bajar(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept-Language": "es-AR,es;q=0.9"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def productos_jsonld(html):
    """Saca los schema.org/Product del HTML (TiendaNube y WooCommerce los emiten)."""
    if not html:
        return []
    out = []
    for bloque in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                             html, re.S):
        try:
            d = json.loads(bloque)
        except Exception:
            continue
        for item in (d if isinstance(d, list) else [d]):
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "Product":
                out.append(item)
            # WooCommerce a veces anida en @graph
            for g in item.get("@graph", []) if isinstance(item.get("@graph"), list) else []:
                if isinstance(g, dict) and g.get("@type") == "Product":
                    out.append(g)
    return out


def precio_de(p):
    of = p.get("offers") or {}
    if isinstance(of, list):
        of = of[0] if of else {}
    if not isinstance(of, dict):
        return None, None, None
    precio = of.get("price") or of.get("lowPrice")
    try:
        precio = float(str(precio).replace(",", "."))
    except Exception:
        precio = None
    return precio, of.get("priceCurrency"), of.get("url")


def productos_html(html, base):
    """Fallback para tiendas sin JSON-LD (ej. NewRed, WooCommerce + Elementor).

    Saca título, link e imagen del marcado de WooCommerce. El precio queda en None
    cuando la tienda no lo publica (los mayoristas suelen mostrarlo sólo al cliente).
    """
    if not html:
        return []
    out, vistos = [], set()
    for m in re.finditer(r'product_title[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]{4,90})</a>',
                         html):
        link, nombre = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if link in vistos:
            continue
        vistos.add(link)
        img = None
        ctx = html[max(0, m.start() - 2500):m.start()]
        im = re.findall(r'<img[^>]+src="([^"]+\.(?:webp|jpg|jpeg|png))"', ctx)
        if im:
            img = im[-1]
        out.append({"nombre": nombre, "link": link, "img": img, "precio": None})
    return out


CATALOGO_LOCAL = {}


def cargar_catalogo(ruta):
    """Catálogo ya parseado desde PDFs (LamboTech)."""
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8")).get("productos", [])
    except Exception:
        return []


def buscar_local(prov, termino, tc):
    """Busca en un catálogo local por palabras de la descripción."""
    prods = CATALOGO_LOCAL.get(prov["id"]) or []
    if not prods:
        return []
    palabras = [w for w in norm(termino).split() if len(w) > 3]
    if not palabras:
        return []
    clave = palabras[0]
    hits = [p for p in prods if clave in norm(p["descripcion"])]
    hits.sort(key=lambda p: -p["precio_usd"])
    return [{
        "proveedor": prov["id"], "proveedor_nombre": prov["nombre"],
        "nombre": f"{p['descripcion'][:90]} ({p['codigo']})",
        "precio": p["precio_usd"], "moneda": "USD",
        "link": prov["url"], "img": None,
        "_costo_directo": p["precio_usd"] * tc,
    } for p in hits[:8]]


def buscar_en(prov, termino):
    url = prov["buscar"].format(q=urllib.parse.quote(termino))
    html = bajar(url)
    res = []
    jsonld = productos_jsonld(html)
    if not jsonld:
        # Sin JSON-LD: probamos el marcado de WooCommerce.
        return [{"proveedor": prov["id"], "proveedor_nombre": prov["nombre"],
                 "nombre": p["nombre"][:110], "precio": None, "moneda": "",
                 "link": p["link"], "img": p["img"]}
                for p in productos_html(html, prov["url"])[:8]]
    for p in jsonld:
        nombre = (p.get("name") or "").strip()
        if not nombre:
            continue
        precio, moneda, link = precio_de(p)
        img = p.get("image")
        if isinstance(img, list):
            img = img[0] if img else None
        res.append({
            "proveedor": prov["id"], "proveedor_nombre": prov["nombre"],
            "nombre": nombre[:110], "precio": precio, "moneda": moneda or "ARS",
            "link": link or url, "img": img,
        })
    return res[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="/tmp/proveedores_ar.json")
    ap.add_argument("--productos", help="productos_ar.json para tomar los términos a buscar")
    ap.add_argument("--terminos", help="archivo con términos, uno por línea (alternativa)")
    ap.add_argument("--pausa", type=float, default=1.0)
    ap.add_argument("--catalogo-lambo", dest="catalogo_lambo",
                    help="ruta a catalogo_lambo.json (default /tmp/catalogo_lambo.json)")
    args = ap.parse_args()

    # Términos = títulos de los productos ganadores (el match que queremos).
    terminos = []
    if args.productos and Path(args.productos).exists():
        d = json.loads(Path(args.productos).read_text(encoding="utf-8"))
        vistos = set()
        for p in sorted(d.get("productos", []), key=lambda x: -x.get("score", 0)):
            t = (p.get("titulo") or "").strip()
            if t and norm(t) not in vistos:
                vistos.add(norm(t))
                terminos.append(t)
    elif args.terminos:
        terminos = [l.strip() for l in Path(args.terminos).read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")]
    if not terminos:
        sys.exit("Sin términos. Pasá --productos productos_ar.json o --terminos archivo.txt")

    # Precio de venta observado por categoría (mediana de lo que cobran las
    # tiendas que lo anuncian). Es la base del margen real.
    ventas = {}
    if args.productos and Path(args.productos).exists():
        import statistics
        acum = {}
        for p in json.loads(Path(args.productos).read_text(encoding="utf-8")).get("productos", []):
            if p.get("precio_venta"):
                acum.setdefault(norm(p.get("titulo")), []).append(p["precio_venta"])
        ventas = {k: statistics.median(v) for k, v in acum.items() if v}
        print(f"  precio de venta observado en {len(ventas)} categorías")

    consultables = [p for p in PROVEEDORES if p.get("buscar")]
    for prov in PROVEEDORES:
        if prov.get("catalogo_local"):
            ruta = args.catalogo_lambo or ("/tmp/" + prov["catalogo_local"])
            CATALOGO_LOCAL[prov["id"]] = cargar_catalogo(ruta)
            print(f"  catálogo {prov['nombre']}: {len(CATALOGO_LOCAL[prov['id']])} productos")
    tc = dolar()
    print(f"Buscando {len(terminos)} productos en {len(consultables)} proveedores "
          f"(dólar ${tc:,.0f})…")

    busquedas = {}
    for i, t in enumerate(terminos, 1):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(consultables)) as ex:
            futs = {ex.submit(buscar_en, p, t): p for p in consultables}
            filas = []
            for f in concurrent.futures.as_completed(futs):
                try:
                    filas.extend(f.result() or [])
                except Exception:
                    pass
        for prov in PROVEEDORES:
            if prov.get("catalogo_local"):
                filas.extend(buscar_local(prov, t, tc))
        # Costo por unidad y margen. Si sabemos a cuánto se vende de verdad
        # (precio observado en la tienda que lo anuncia), el margen es REAL:
        # precio de venta − costo del mayorista. Si no, se estima a MARKUP×.
        venta_real = ventas.get(norm(t))
        for f in filas:
            c = f.pop("_costo_directo", None) or costo_unitario(f, tc)
            f["costo_unitario"] = round(c) if c else None
            # El precio de venta es el de la CATEGORÍA. Sólo vale como margen real
            # si el ítem del mayorista es plausiblemente ese mismo producto: si
            # cuesta una fracción ínfima del precio de venta, es otra cosa más
            # barata que cayó en la misma búsqueda, y el margen sería una fantasía.
            plausible = bool(c and venta_real and c >= venta_real * 0.15)
            if plausible:
                f["venta_est"] = round(venta_real)
                f["margen_est"] = round(venta_real - c)
                f["margen_real"] = True
            else:
                f["venta_est"] = round(c * MARKUP) if c else None
                f["margen_est"] = round(c * (MARKUP - 1)) if c else None
                f["margen_real"] = False
            f["ticket"] = segmentar(f["margen_est"])
        # Primero los de mayor margen: es lo que busca el modelo high ticket.
        filas.sort(key=lambda x: -(x.get("margen_est") or 0))
        busquedas[t] = filas
        n_prov = len({f["proveedor"] for f in filas})
        print(f"  [{i}/{len(terminos)}] {t}: {len(filas)} resultados en {n_prov} proveedores")
        if i < len(terminos):
            time.sleep(args.pausa)

    salida = {
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "dolar": tc, "markup": MARKUP,
        "umbrales": {"alto": UMBRAL_ALTO, "medio": UMBRAL_MEDIO},
        "proveedores": [{k: v for k, v in p.items() if k != "buscar"} |
                        {"consultable": bool(p.get("buscar") or p.get("catalogo_local"))} for p in PROVEEDORES],
        "busquedas": busquedas,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(salida, ensure_ascii=False), encoding="utf-8")
    con = sum(1 for v in busquedas.values() if v)
    altos = sum(1 for v in busquedas.values() for f in v if f.get("ticket") == "alto")
    print(f"\nOK: {con}/{len(terminos)} productos con proveedor → {args.out}")
    print(f"   {altos} opciones de ticket alto (margen ≥ ${UMBRAL_ALTO:,})")


if __name__ == "__main__":
    main()
