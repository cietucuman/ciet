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
import re
import statistics
import sys
import time
import unicodedata
import urllib.request
import urllib.parse
from pathlib import Path

# palabras "ruido" que se ignoran al emparejar productos por nombre
STOP = {"gaseosa", "bebida", "lt", "lts", "l", "ml", "cc", "cm3", "grs", "gr", "g",
        "kg", "un", "u", "x", "de", "pack", "bot", "pet", "botella", "lata", "sabor",
        "the", "el", "la", "doypack", "sachet", "pouch",
        "energizante", "energy", "sin", "azucar", "en", "con"}

# sinónimos multi-palabra: se reemplazan ANTES de tokenizar (frase -> canónico).
# Unifican el mismo producto cuando cada cadena usa otra denominación.
SINONIMOS = {
    "white pineapple": "anana", "pipeline punch": "pipeline",
    "peachy keen": "peachy", "mango loco": "mango", "energy vr": "vr",
}

# traducciones/variantes palabra->canónico (inglés->español, formas alternativas).
# Es 1:1: nunca fusiona productos distintos, sólo unifica el idioma/la variante.
TRAD = {
    "pineapple": "anana", "watermelon": "sandia", "watermel": "sandia",
    "grape": "uva", "apple": "manzana", "orange": "naranja", "lemon": "limon",
    "peach": "durazno", "strawberry": "frutilla", "cherry": "cereza",
    "vanilla": "vainilla", "coffee": "cafe", "chocolate": "chocolate",
    "coconut": "coco", "banana": "banana", "mango": "mango",
    "original": "original", "sugarfree": "zero", "light": "light",
}


def _norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def clave_fuzzy(nombre, marca):
    """Firma normalizada de un producto, para unir el mismo artículo aunque
    distintas cadenas usen otro código de barras, idioma o nombre distinto."""
    s = _norm(nombre + " " + marca).replace(",", ".")
    for frase, canon in SINONIMOS.items():   # "white pineapple" -> "anana"
        s = s.replace(frase, canon)
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)   # separa "2.25l" -> "2.25 l"
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)
    s = re.sub(r"\.(?!\d)", "", s)            # "cc." -> "cc", pero deja "2.25"
    s = re.sub(r"[^a-z0-9. ]", " ", s)
    toks = [TRAD.get(t, t) for t in s.split()]   # traduce inglés->español
    toks = [t for t in toks if (t not in STOP and len(t) > 1) or t.isdigit()]
    return " ".join(sorted(set(toks)))


def _absorber(f, g):
    """Vuelca el grupo g dentro de f (precio mínimo por cadena, une EAN/imagen)."""
    gsize, fsize = len(g["pr"]), len(f["pr"])
    for cad, o in g["pr"].items():
        if cad not in f["pr"] or o[0] < f["pr"][cad][0]:
            f["pr"][cad] = o
    f.setdefault("eans", set()).update(g.get("eans") or ())
    if not f.get("i") and g.get("i"):
        f["i"] = g["i"]
    if gsize > fsize:            # muestra el nombre de la cadena que aparece en más súper
        f["n"], f["m"] = g["n"], g["m"]


def fusion_por_ean(grupos):
    """Une grupos que comparten CUALQUIER código de barras: identidad garantizada
    (dos cadenas cargan el mismo artículo con varios EAN). Union-find sobre los EAN."""
    parent = list(range(len(grupos)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ean2idx = {}
    for i, g in enumerate(grupos):
        for e in g.get("eans") or ():
            if e in ean2idx:
                parent[find(i)] = find(ean2idx[e])
            else:
                ean2idx[e] = i
    reps = {}
    for i, g in enumerate(grupos):
        r = find(i)
        if r not in reps:
            reps[r] = g
        else:
            _absorber(reps[r], g)
    return list(reps.values())

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


def get(url, intentos=2, cookie=None):
    for i in range(intentos):
        try:
            h = {"User-Agent": UA}
            if cookie:
                h["Cookie"] = f"vtex_segment={cookie}"
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=12) as r:
                return json.load(r)
        except Exception:
            if i == intentos - 1:
                return None
            time.sleep(0.6)
    return None


def segmento_tucuman(dom):
    """Cencosud (Vea/Jumbo): precio de Tucumán vía la cookie vtex_segment que
    setea la API de sesión con el CP 4000 (su /regions no funciona)."""
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    body = json.dumps({"public": {"country": {"value": "ARG"},
                                  "postalCode": {"value": CP}}}).encode()
    for u in (f"https://{dom}/api/sessions",
              f"https://{dom}/api/sessions?items=checkout.regionId"):
        try:
            op.open(urllib.request.Request(u, data=body, method="POST",
                    headers={"User-Agent": UA, "Content-Type": "application/json"}), timeout=12)
        except Exception:
            pass
    for c in cj:
        if c.name == "vtex_segment":
            return c.value
    return None


def region_id(dom):
    d = get(f"https://{dom}/api/checkout/pub/regions/?country=ARG&postalCode={CP}")
    return d[0].get("id") if isinstance(d, list) and d else None


def post_json(url, body, intentos=2):
    data = json.dumps(body).encode()
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, data=data, method="POST",
                                         headers={"User-Agent": UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=12) as r:
                return json.load(r)
        except Exception:
            if i == intentos - 1:
                return None
            time.sleep(0.6)
    return None


def precios_tucuman(dom, region, items):
    """Precio REAL de entrega en Tucumán vía simulación de checkout (sin login).
    items: lista de (sku, seller). Devuelve {sku: precio}."""
    if not region or not items:
        return {}
    url = f"https://{dom}/api/checkout/pub/orderForms/simulation?RnbBehavior=0&regionId={urllib.parse.quote(region)}"
    out = {}
    for i in range(0, len(items), 40):
        body = {"items": [{"id": s, "quantity": 1, "seller": v} for s, v in items[i:i + 40]],
                "country": "ARG", "postalCode": CP}
        d = post_json(url, body)
        if d and d.get("items"):
            for it in d["items"]:
                sp = it.get("sellingPrice")
                if sp and it.get("id"):
                    out[str(it["id"])] = round(sp / 100, 2)
        time.sleep(0.2)
    return out


def limpiar_nombre(n):
    """Nombre legible: colapsa espacios y baja el TODO-MAYÚSCULAS a Título."""
    n = " ".join((n or "").split())
    letras = [c for c in n if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.75:
        n = n.title()
    return n[:90]


def productos_termino(dom, termino, region, tope, cookie=None):
    out, frm = [], 0
    ft = urllib.parse.quote(termino)
    while frm < tope:
        url = (f"https://{dom}/api/catalog_system/pub/products/search"
               f"?ft={ft}&_from={frm}&_to={frm+49}")
        if region:
            url += f"&regionId={urllib.parse.quote(region)}"
        d = get(url, cookie=cookie)
        if not isinstance(d, list) or not d:
            break
        for p in d:
            try:
                item = p["items"][0]
                o = item["sellers"][0]["commertialOffer"]
                precio = o.get("Price")
                # sólo productos realmente comprables (como los ve un humano).
                # qty>=3: las góndolas reales reportan 10/100/99999; qty 1-2 son
                # listados fantasma (p. ej. una Coca a $164 que no existe).
                disponible = o.get("IsAvailable") and (o.get("AvailableQuantity") or 0) >= 3
                if not precio or precio < 100 or not disponible:
                    continue
                # todos los códigos de barras del producto (cada cadena carga el
                # mismo artículo con varios EAN; compartir uno = mismo producto)
                eans = [it.get("ean") for it in p.get("items", []) if it.get("ean")]
                prod = {
                    "n": limpiar_nombre(p.get("productName", "")),
                    "m": (p.get("brand") or "")[:28],
                    "e": item.get("ean") or "",
                    "eans": eans,
                    "p": round(precio, 2),
                    "l": p.get("link") or "",
                    "i": (item.get("images") or [{}])[0].get("imageUrl") or "",
                    "sku": item.get("itemId"),
                    "sel": item["sellers"][0].get("sellerId"),
                }
                # oferta: precio de lista mayor, pero con descuento realista (<=60%)
                lista = o.get("ListPrice") or 0
                if precio * 1.03 < lista <= precio * 2.5:
                    prod["op"] = round(lista, 2)
                out.append(prod)
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

    # agrupar por producto: clave = código de barras (o link si no tiene).
    # cada grupo junta el precio de todas las cadenas que lo tienen.
    grupos = {}
    geoloc = {}
    for nombre, dom in TIENDAS.items():
        region = region_id(dom)
        seg = segmento_tucuman(dom) if not region else None
        geoloc[nombre] = bool(region or seg)
        modo = "región" if region else ("segmento" if seg else "nacional")
        print(f"{nombre}: geoloc={modo}", file=sys.stderr)
        # 1) juntar los productos de la cadena (dedup por clave)
        chain = {}
        for i, term in enumerate(TERMINOS, 1):
            for pr in productos_termino(dom, term, region, args.tope, cookie=seg):
                clave = pr["e"] or pr["l"]
                if clave and clave not in chain:
                    chain[clave] = pr
            if i % 30 == 0:
                print(f"  {nombre}: {i}/{len(TERMINOS)} términos · {len(chain)} productos", file=sys.stderr)
        # 2) precio REAL de entrega en Tucumán (simulación de checkout, sin login)
        if region:
            porsku = {}
            for pr in chain.values():
                if pr.get("sku"):
                    porsku.setdefault(pr["sku"], (pr["sel"], []))[1].append(pr)
            items = [(sku, sel) for sku, (sel, _) in porsku.items()]
            sim = precios_tucuman(dom, region, items)
            for sku, (_, prs) in porsku.items():
                if sku in sim:
                    for pr in prs:
                        pr["p"] = sim[sku]
                        pr.pop("op", None)  # el precio simulado ya es el efectivo
            print(f"  {nombre}: precio Tucumán aplicado a {len(sim)}/{len(items)}", file=sys.stderr)
        # 3) volcar al agrupado global
        for pr in chain.values():
            clave = pr["e"] or pr["l"]
            g = grupos.get(clave)
            if g is None:
                g = grupos[clave] = {"n": pr["n"], "m": pr["m"], "i": pr.get("i", ""),
                                     "pr": {}, "eans": set()}
            g["eans"].update(pr.get("eans") or [])
            if not g["i"] and pr.get("i"):
                g["i"] = pr["i"]
            oferta = [pr["p"], pr["l"]]
            if "op" in pr:
                oferta.append(pr["op"])
            g["pr"][nombre] = oferta
        print(f"  {nombre}: {len(chain)} productos", file=sys.stderr)

    # 1.5) fusión por EAN COMPARTIDO (identidad garantizada): si dos grupos
    # comparten cualquier código de barras, son el mismo producto. Más confiable
    # que el nombre; resuelve casos como "Monster VR" == "Monster Rossi".
    grupos = fusion_por_ean(list(grupos.values()))

    # 2º agrupado: unir productos idénticos con distinto EAN (por nombre normalizado)
    fusion = {}
    for g in grupos:
        k = clave_fuzzy(g["n"], g["m"])
        f = fusion.get(k)
        if f is None:
            fusion[k] = g
            continue
        for cad, o in g["pr"].items():
            if cad not in f["pr"] or o[0] < f["pr"][cad][0]:
                f["pr"][cad] = o
        if not f.get("i") and g.get("i"):
            f["i"] = g["i"]
        if len(g["pr"]) > len(f["pr"]):   # nombre del que aparece en más cadenas
            f["n"], f["m"] = g["n"], g["m"]
    finales = list(fusion.values())

    # descartar precios absurdos por producto: si una cadena queda muy por debajo
    # de la mediana del mismo artículo en las demás, es un dato erróneo (no existe).
    for g in finales:
        precios_cad = {c: o[0] for c, o in g["pr"].items()}
        if len(precios_cad) < 3:
            continue
        med = statistics.median(precios_cad.values())
        for c, p in list(precios_cad.items()):
            if p < 0.4 * med or p > 2.6 * med:
                del g["pr"][c]
    finales = [g for g in finales if g["pr"]]

    cadenas_meta = {n: {"geolocalizado": geoloc[n],
                        "productos": sum(1 for g in finales if n in g["pr"])}
                    for n in TIENDAS}
    productos = [{"n": g["n"], "m": g["m"], "i": g["i"], "pr": g["pr"]} for g in finales]
    en_varias = sum(1 for g in finales if len(g["pr"]) > 1)
    out = {
        "fecha": time.strftime("%Y-%m-%d"),
        "cadenas": cadenas_meta,
        "total": len(productos),
        "en_varias_cadenas": en_varias,
        "productos": productos,
    }
    Path(args.salida).write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK → {args.salida} ({len(productos)} productos)", file=sys.stderr)


if __name__ == "__main__":
    main()
