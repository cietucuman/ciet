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
import base64
import concurrent.futures
import html as _html
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
        "the", "el", "la", "del", "doypack", "sachet", "pouch",
        "energizante", "energy", "en", "con",
        "tableta", "para", "unidad", "unidades",
        # relleno confirmado (revisión manual): no distinguen producto
        "pureza", "dp", "litro", "saborizada", "valle", "clasica", "clasico",
        # co-marcas/líneas que una cadena agrega y otra no, para el MISMO producto
        # (Tuchanguito "Grisines … Veneziana Cormillot" == Vea "Grisines Veneziana …").
        "cormillot",
        # descriptores de categoría que cada cadena escribe distinto para el MISMO
        # producto (Suerox "isotónica" en una cadena, "hidratante" en otra).
        "isotonica", "isotonico", "hidratante", "hidratacion", "rehidratante"}

# sinónimos multi-palabra: se reemplazan ANTES de tokenizar (frase -> canónico).
# Unifican el mismo producto cuando cada cadena usa otra denominación.
SINONIMOS = {
    "white pineapple": "anana", "pipeline punch": "pipeline",
    "peachy keen": "peachy", "mango loco": "mango", "energy vr": "vr",
    # "sin azúcar" NO es ruido: es la variante zero (Monster/Coca sin azúcar ≠ la
    # regular). Se canoniza a "zero" para que ambas escrituras se unan entre sí
    # y NUNCA con la versión regular.
    "sin azucar": "zero",
}

# EQUIVALENCIAS ENTRE CADENAS (curadas): el MISMO producto que una cadena nombra
# distinto (o carga con otro código de barras). Se define una vez y queda
# emparejado para siempre. Sólo agrupa —nunca esconde— así que es seguro.
# Formato: "como lo escribe una cadena" -> "forma canónica (como las demás)".
# Ej.: Comodín llama "Monster Rossi" al Monster que las otras llaman "Energy VR".
# Para sumar un caso: agregá una línea acá.
ALIAS_CADENAS = {
    "monster rossi": "monster vr",
    "golsch": "grolsch",          # typo de una cadena (cerveza Grolsch)
    # Monster en lata: Tuchanguito llama "Energy" al verde (regular) y "Ultra Zero"
    # al negro sin azúcar; las otras cadenas dicen "green" y "sin azúcar/zero".
    "monster green": "monster energy",     # el verde regular == "Monster Energy"
    "monster ultra zero": "monster zero",  # el sin azúcar de lata
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
    for frase, canon in ALIAS_CADENAS.items():   # "monster rossi" -> "monster vr"
        s = s.replace(frase, canon)
    for frase, canon in SINONIMOS.items():   # "white pineapple" -> "anana"
        s = s.replace(frase, canon)
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)   # separa "2.25l" -> "2.25 l"
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)
    s = re.sub(r"\.(?!\d)", "", s)            # "cc." -> "cc", pero deja "2.25"
    s = re.sub(r"[^a-z0-9. ]", " ", s)
    toks = [TRAD.get(t, t) for t in s.split()]   # traduce inglés->español
    # plural -> singular (nuggets == nugget), sólo palabras (no números)
    toks = [t[:-1] if (len(t) > 3 and t.endswith("s") and not any(ch.isdigit() for ch in t)) else t
            for t in toks]
    toks = [t for t in toks if (t not in STOP and len(t) > 1) or t.isdigit()]
    toks = set(toks)
    # la línea Monster "Ultra" es toda sin azúcar: algunas cadenas lo aclaran en
    # el nombre y otras no. Con "ultra" presente, "zero" es redundante.
    if "ultra" in toks:
        toks.discard("zero")
    return " ".join(sorted(toks))


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


# Sucursal REAL de Tucumán por cadena (descubierta del segmento de una sesión con
# dirección de San Miguel de Tucumán). Se usa sólo donde el método por código
# postal da precios de otra sucursal. Jumbo: verificado que da los precios reales
# (Powerade $2.600). Vea NO va acá: su método por CP ya da bien ($2.000).
CENCOSUD_SUCURSAL = {
    "www.jumbo.com.ar": ("jumboargentinaj5227tucuman", "32"),
}


def segmento_tucuman(dom):
    """Cencosud (Vea/Jumbo): cookie vtex_segment con la región de Tucumán."""
    conf = CENCOSUD_SUCURSAL.get(dom)
    if conf:
        store, canal = conf
        rid = base64.b64encode(("SW#" + store).encode()).decode()
        seg = {"channel": canal, "regionId": rid, "currencyCode": "ARS",
               "currencySymbol": "$", "countryCode": "ARG", "cultureInfo": "es-AR",
               "channelPrivacy": "public"}
        return base64.b64encode(json.dumps(seg).encode()).decode()
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


def region_id(dom, sc=None, cookie=None):
    url = f"https://{dom}/api/checkout/pub/regions/?country=ARG&postalCode={CP}"
    if sc:                       # Cencosud necesita el canal de ventas y el segmento
        url += f"&sc={sc}"
    d = get(url, cookie=cookie)
    return d[0].get("id") if isinstance(d, list) and d else None


def post_json(url, body, intentos=2, cookie=None):
    data = json.dumps(body).encode()
    for i in range(intentos):
        try:
            h = {"User-Agent": UA, "Content-Type": "application/json"}
            if cookie:
                h["Cookie"] = f"vtex_segment={cookie}"
            req = urllib.request.Request(url, data=data, method="POST", headers=h)
            with urllib.request.urlopen(req, timeout=12) as r:
                return json.load(r)
        except Exception:
            if i == intentos - 1:
                return None
            time.sleep(0.6)
    return None


# Seller (sucursal) de Tucumán para pedir las PROMOS del día a /_v/search-promotions.
# Cencosud (Vea/Jumbo) NO manda el precio con descuento en la API: lo calcula el
# front como Price*(1-effectiveDiscount). Ese descuento SÍ es público (no requiere
# login), pero el endpoint sólo lo devuelve si se le pasa el seller REAL de la
# tienda de Tucumán — con el seller genérico "1" viene vacío.
SEARCH_PROMO_SELLER = {
    "www.vea.com.ar": "jumboargentinav125sarmientotucuman",
    "www.jumbo.com.ar": "jumboargentinaj5227tucuman",
}


def promos_cencosud(dom, skus, cookie=None, workers=6):
    """Ofertas del día (Vea/Jumbo) del bucket 'generic' de search-promotions —
    la oferta pública que ve cualquiera (no la de socios, que va en jumbo_prime/sgc).
    Devuelve {sku: (tipo, valor)}:
      - ("fixed", precio)   → precio de oferta FIJO (usar tal cual; el effectiveDiscount
                              es sólo una aproximación y da mal si se aplica como %).
      - ("pct", descuento)  → descuento por unidad (0..1); precio final = Price*(1-desc).
                              Cubre % y nxm (4x3, etc.): el descuento ya es el efectivo.
    Los lotes se piden EN PARALELO (pool chico, mismo patrón que precios_tucuman).
    Medido sobre SKUs reales: el endpoint responde ~0,3s/lote y tolera 6 en simultáneo
    sin un solo fallo, dando el MISMO resultado que en secuencial (~7x más rápido)."""
    seller = SEARCH_PROMO_SELLER.get(dom)
    if not seller or not skus:
        return {}
    url = f"https://{dom}/_v/search-promotions"
    # el endpoint devuelve HTTP 500 si el lote supera ~25 SKUs → se mandan de a 20.
    lotes = [[str(s) for s in skus[i:i + 20]] for i in range(0, len(skus), 20)]

    def pedir(lote):
        d = None
        for intento in range(3):         # reintenta ante error puntual (500/throttle)
            d = post_json(url, {"seller": seller, "skus": lote}, cookie=cookie)
            if d is not None:
                break
            time.sleep(1.5 * (intento + 1))
        res = {}
        gen = ((d or {}).get("promotions", {}).get("generic", {}) or {}).get("promotions", {}) or {}
        for sku, pr in gen.items():
            try:
                desc = float(pr.get("effectiveDiscount") or 0)
            except (TypeError, ValueError):
                desc = 0
            try:
                valor = float(pr.get("value") or 0)
            except (TypeError, ValueError):
                valor = 0
            if pr.get("discountType") == "fixed_price" and valor > 0:
                res[str(sku)] = ("fixed", round(valor, 2))     # precio de oferta fijo
            elif 0 < desc < 0.95:          # descuento realista (evita datos absurdos)
                res[str(sku)] = ("pct", desc)
        return res

    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(pedir, lotes):    # cada SKU cae en un solo lote → sin colisión
            out.update(r)
    return out


def precios_tucuman(dom, region, items, sc=None, cookie=None, workers=6):
    """Simulación de checkout (sin login): prueba real de si un producto se puede
    comprar en Tucumán, y a qué precio. items: [(sku, seller)].
    Devuelve {sku: (precio, availability)}.
      - Disponibilidad y precio base: simulación a cantidad 1.
      - Promos de cantidad (2do al X%, 4x3, 3x2…): se simula además a cantidad 4 y se
        toma el MENOR precio por unidad. A qty 4 tanto "2do al X%" como "4x3" dan su
        precio unitario correcto; cubre las dos cadenas (ChangoMás no expone teasers).
      - 'cannotBeDelivered' se reintenta SIN postalCode (cadenas que dejaron de enviar
        al CP pero venden en Tucumán, p. ej. Comodín).
    Los lotes se corren en paralelo (pool chico) para que sea rápido."""
    if not region or not items:
        return {}
    url = f"https://{dom}/api/checkout/pub/orderForms/simulation?RnbBehavior=0&regionId={urllib.parse.quote(region)}"
    if sc:
        url += f"&sc={sc}"

    def simular_lote(lote, qty, con_cp):
        body = {"items": [{"id": s, "quantity": qty, "seller": v} for s, v in lote],
                "country": "ARG"}
        if con_cp:
            body["postalCode"] = CP
        d = post_json(url, body, cookie=cookie)
        res = {}
        if d and d.get("items"):
            for it in d["items"]:
                sid = str(it.get("id") or "")
                if sid:
                    sp = it.get("sellingPrice")
                    res[sid] = (round(sp / 100, 2) if sp else None, it.get("availability"))
        return res

    def correr(its, qty, con_cp):
        """Simula 'its' en lotes de 40, en paralelo. Devuelve {sku: (precio, avail)}."""
        salida = {}
        lotes = [its[i:i + 40] for i in range(0, len(its), 40)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(lambda L: simular_lote(L, qty, con_cp), lotes):
                salida.update(r)
        return salida

    # --- qty 1: disponibilidad + precio base (con reintento de los que no responden) ---
    q1 = correr(items, 1, True)
    faltan = [it for it in items if str(it[0]) not in q1]
    if faltan:
        q1.update(correr(faltan, 1, True))
    # rescate de 'cannotBeDelivered' sin postalCode
    resc_skus = {str(it[0]) for it in items
                 if q1.get(str(it[0]), (None, None))[1] == "cannotBeDelivered"}
    if resc_skus:
        q1.update(correr([it for it in items if str(it[0]) in resc_skus], 1, False))

    # --- qty 4: precio con promo de cantidad (sólo los disponibles) ---
    disp = [it for it in items if q1.get(str(it[0]), (None, None))[1] == "available"]
    disp_cp = [it for it in disp if str(it[0]) not in resc_skus]
    disp_sincp = [it for it in disp if str(it[0]) in resc_skus]
    q4 = correr(disp_cp, 4, True) if disp_cp else {}
    if disp_sincp:
        q4.update(correr(disp_sincp, 4, False))

    # combinar: precio = menor por unidad entre qty1 y qty4 (captura la promo)
    out = {}
    for it in items:
        sid = str(it[0])
        p1, av = q1.get(sid, (None, None))
        p4 = q4.get(sid, (None, None))[0]
        precio = min(p1, p4) if (p1 and p4) else (p1 or p4)
        out[sid] = (precio, av)
    return out


def precios_cencosud(dom, region, items, sc=None, cookie=None, workers=6):
    """Precio REAL + disponibilidad de Tucumán para Vea/Jumbo por simulación de checkout,
    igual estándar que las cadenas de región. Resuelve DOS problemas de Cencosud a la vez:
      1) el índice de búsqueda trae precios DESACTUALIZADOS (más baratos que la góndola);
         el precio efectivo es el `sellingPrice` del checkout.
      2) distingue lo comprable/entregable en Tucumán: los fantasmas (SKUs viejos que no
         se venden) dan availability='cannotBeDelivered' al CP 4000 (verificado: el atún
         120g fantasma da cannotBeDelivered; la Veneziana real da 'available').
    OJO: el checkout de Cencosud FALSEA el precio y la disponibilidad si el carrito tiene
    3+ ítems (devuelve datos viejos cacheados). Verificado que con <=2 ítems da lo real
    (Powerade en lote>=3 = $2250 viejo vs de a 2 = $3699 real). Por eso se simula DE A 2,
    en paralelo (pool chico) → ~10-15 min para Vea+Jumbo, sin throttling a workers=6.
    NO se hace el rescate sin código postal (haría 'available' hasta a los fantasmas).
    items: [(sku, seller)]. Devuelve {sku: (precio, availability)}."""
    if not region or not items:
        return {}
    url = f"https://{dom}/api/checkout/pub/orderForms/simulation?RnbBehavior=0&regionId={urllib.parse.quote(region)}"
    if sc:
        url += f"&sc={sc}"

    def sim_par(par):
        body = {"items": [{"id": s, "quantity": 1, "seller": v} for s, v in par],
                "country": "ARG", "postalCode": CP}
        d = post_json(url, body, cookie=cookie)
        res = {}
        if d and d.get("items"):
            for it in d["items"]:
                sid = str(it.get("id") or "")
                if not sid:
                    continue
                sp = it.get("sellingPrice")
                res[sid] = (round(sp / 100, 2) if sp else None, it.get("availability"))
        return res

    pares = [items[i:i + 2] for i in range(0, len(items), 2)]   # de a 2 (3+ falsea)
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(sim_par, pares):
            out.update(r)
    return out


def _entregable_cencosud(dom, region, sku, sel, sc, cookie):
    """¿Se puede comprar y recibir en Tucumán? (simulación de 1 solo ítem).
    Devuelve True/False, o None si no hubo respuesta (no se descarta ante error)."""
    url = (f"https://{dom}/api/checkout/pub/orderForms/simulation?RnbBehavior=0"
           f"&sc={sc}&regionId={urllib.parse.quote(region)}")
    body = {"items": [{"id": sku, "quantity": 1, "seller": sel}],
            "country": "ARG", "postalCode": CP}
    d = post_json(url, body, cookie=cookie)
    if not d or not d.get("items"):
        return None
    return d["items"][0].get("availability") == "available"


def disponibles_cencosud(dom, region, items, sc, cookie, workers=6):
    """Set de SKUs entregables en Tucumán. Se consulta de a 1 (el checkout
    batchea por peso y falsea la disponibilidad si el carrito es grande);
    se paraleliza con un pool chico para que sea rápido y a la vez cortés."""
    if not region or not items:
        return set(), 0
    disp, sin_rpta = set(), 0

    def check(it):
        return it[0], _entregable_cencosud(dom, region, it[0], it[1], sc, cookie)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for sku, ent in ex.map(check, items):
            if ent is None:          # error de red: se conserva (no borrar por las dudas)
                disp.add(sku); sin_rpta += 1
            elif ent:
                disp.add(sku)
    return disp, sin_rpta


def limpiar_nombre(n):
    """Nombre legible: colapsa espacios y baja el TODO-MAYÚSCULAS a Título."""
    n = " ".join((n or "").split())
    letras = [c for c in n if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.75:
        n = n.title()
    return n[:90]


def trade_policy(seg):
    """Canal de ventas (trade policy) que viene codificado en el segmento de
    Cencosud; lo necesita la intelligent-search para dar la vista de Tucumán."""
    try:
        dec = json.loads(base64.b64decode(seg + "==").decode("utf-8", "ignore"))
        return str(dec.get("channel") or "1")
    except Exception:
        return "1"


def productos_is(dom, termino, tp, tope, cookie=None):
    """Cencosud (Vea/Jumbo): usa la intelligent-search (lo que ve el cliente en
    la web) con hideUnavailableItems para descartar fantasmas de precio ($0, $50).

    NO se usa 'sellerDefault' como filtro: en la intelligent-search ese campo es
    POCO FIABLE (marca sellerDefault=False a productos REALES y comprables, p. ej. la
    Veneziana integral de Vea que sí tiene botón "Agregar" → daba falsos negativos y
    faltaban productos). El precio real y si se puede comprar/entregar en Tucumán los
    resuelve DESPUÉS la simulación de checkout individual (precios_cencosud), igual que
    en las cadenas de región."""
    out, frm = [], 0
    q = urllib.parse.quote(termino)
    while frm < tope:
        url = (f"https://{dom}/api/io/_v/api/intelligent-search/product_search/"
               f"trade-policy/{tp}?query={q}&from={frm}&to={frm+49}"
               f"&hideUnavailableItems=true")
        d = get(url, cookie=cookie)
        prods = d.get("products") if isinstance(d, dict) else None
        if not prods:
            break
        for p in prods:
            try:
                item = p["items"][0]
                seller = next((s for s in item.get("sellers", [])
                               if s.get("sellerDefault")), None) or item["sellers"][0]
                o = seller["commertialOffer"]
                precio = o.get("Price")
                if not precio or precio < 100 or (o.get("AvailableQuantity") or 0) <= 0:
                    continue
                eans = [it.get("ean") for it in p.get("items", []) if it.get("ean")]
                link = p.get("link") or ""
                if link.startswith("/"):
                    link = f"https://{dom}{link}"
                prod = {
                    "n": limpiar_nombre(p.get("productName", "")),
                    "m": (p.get("brand") or "")[:28],
                    "e": item.get("ean") or "",
                    "eans": eans,
                    "p": round(precio, 2),
                    "l": link,
                    "i": (item.get("images") or [{}])[0].get("imageUrl") or "",
                    "sku": item.get("itemId"),
                    "sel": seller.get("sellerId"),
                }
                lista = o.get("ListPrice") or 0
                if precio * 1.03 < lista <= precio * 2.5:
                    prod["op"] = round(lista, 2)
                out.append(prod)
            except Exception:
                continue
        if len(prods) < 50:
            break
        frm += 50
        time.sleep(0.07)
    return out


def completar_por_ean(dom, region, eans, cookie=None, workers=6):
    """Busca una lista de códigos de barras en el catálogo de una cadena, de a ~40 por
    consulta (OR de alternateIds_Ean). Sirve para COMPLETAR la comparación entre súper:
    si un producto ya está en el buscador pero falta en esta cadena, se busca su EAN acá
    y se agrega el precio. Devuelve [productos] (formato productos_termino), con precio
    de CATÁLOGO — el caller aplica simulación (región) o promo (Cencosud)."""
    if not eans:
        return []
    lotes = [eans[i:i + 40] for i in range(0, len(eans), 40)]

    def pedir(lote):
        q = "&".join(f"fq=alternateIds_Ean:{e}" for e in lote)
        url = f"https://{dom}/api/catalog_system/pub/products/search?{q}&_from=0&_to=49"
        if region:
            url += f"&regionId={urllib.parse.quote(region)}"
        d = get(url, cookie=cookie)
        res = []
        if not isinstance(d, list):
            return res
        for p in d:
            try:
                item = p["items"][0]
                o = item["sellers"][0]["commertialOffer"]
                precio = o.get("Price")
                # NO se filtra por IsAvailable/qty del catálogo: miente (marca False a
                # productos que la simulación confirma comprables, p. ej. el pan de mesa
                # de Carrefour). La disponibilidad real la decide la simulación (región) o,
                # en Cencosud, el guard de nombre + que el producto ya es real en otra cadena.
                if not precio or precio < 100:
                    continue
                res.append({
                    "n": limpiar_nombre(p.get("productName", "")),
                    "m": (p.get("brand") or "")[:28],
                    "e": item.get("ean") or "",
                    "eans": [it.get("ean") for it in p.get("items", []) if it.get("ean")],
                    "p": round(precio, 2),
                    "l": p.get("link") or "",
                    "i": (item.get("images") or [{}])[0].get("imageUrl") or "",
                    "sku": item.get("itemId"),
                    "sel": item["sellers"][0].get("sellerId"),
                    "disp": bool(o.get("IsAvailable")) and (o.get("AvailableQuantity") or 0) >= 3,
                })
            except Exception:
                continue
        return res

    out = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(pedir, lotes):
            out.extend(r)
    return out


def _solape_nombre(a_toks, b_toks):
    """Coeficiente de solape entre dos conjuntos de tokens (0..1)."""
    if not a_toks or not b_toks:
        return 0.0
    return len(a_toks & b_toks) / min(len(a_toks), len(b_toks))


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


TUCHANGUITO = "www.tuchanguito.com.ar"


def _precio_ar(s):
    """'$3.333,32' -> 3333.32"""
    try:
        return round(float(s.replace(".", "").replace(",", ".")), 2)
    except Exception:
        return 0


def get_html(url, intentos=2):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            if i == intentos - 1:
                return ""
            time.sleep(0.6)
    return ""


def productos_tuchanguito(termino, paginas=5):
    """Tuchanguito (Tiendanube, cadena LOCAL de Tucumán → stock preciso). Parsea
    las tarjetas del buscador; descarta las agotadas (etiqueta de stock visible).
    No expone código de barras, así que se agrupa por nombre."""
    out = {}
    q = urllib.parse.quote(termino)
    for pg in range(1, paginas + 1):
        h = get_html(f"https://{TUCHANGUITO}/search?q={q}&page={pg}")
        if not h:
            break
        anclas = [(m.group(1), m.start())
                  for m in re.finditer(r"product-item-image-(\d+)", h)]
        if not anclas:
            break
        n0 = len(out)
        for i, (pid, pos) in enumerate(anclas):
            card = h[pos:(anclas[i + 1][1] if i + 1 < len(anclas) else pos + 3000)]
            nm = re.search(r'/productos/[^"]+"\s+title="([^"]+)"', card)
            pr = re.search(r"js-price-display[^>]*>\s*\$([\d.,]+)", card)
            if not nm or not pr:
                continue
            sl = re.search(r"<[^>]*js-stock-label[^>]*>", card)
            if sl and "display:none" not in sl.group(0).replace(" ", ""):
                continue                       # etiqueta de stock visible = agotado
            precio = _precio_ar(pr.group(1))
            if precio < 100 or pid in out:
                continue
            lk = re.search(r'href="(https://www\.tuchanguito\.com\.ar/productos/[^"]+)"', card)
            im = re.search(r'data-srcset="([^"]+)"', card)
            img = ""
            if im:
                webps = re.findall(r"(//acdn[^ ]+\.webp)", im.group(1))
                img = ("https:" + webps[-1]) if webps else ""
            out[pid] = {"n": _html.unescape(nm.group(1)), "m": "",
                        "p": precio, "l": lk.group(1) if lk else "", "i": img}
        if len(anclas) < 10 or len(out) == n0:
            break
        time.sleep(0.1)
    return list(out.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tope", type=int, default=150, help="máx productos por término")
    ap.add_argument("-o", "--salida", default="data/buscador.json")
    args = ap.parse_args()

    # agrupar por producto: clave = código de barras (o link si no tiene).
    # cada grupo junta el precio de todas las cadenas que lo tienen.
    grupos = {}
    geoloc = {}
    chaincfg = {}
    for nombre, dom in TIENDAS.items():
        region = region_id(dom)
        seg = segmento_tucuman(dom) if not region else None
        tp = trade_policy(seg) if seg else None
        # región de checkout para la simulación: directa en las de región; para
        # Cencosud (Vea/Jumbo) hace falta el canal de ventas (sc) y el segmento.
        region_sim = region or (region_id(dom, sc=tp, cookie=seg) if seg else None)
        geoloc[nombre] = bool(region or seg)
        chaincfg[nombre] = {"dom": dom, "region": region, "seg": seg, "tp": tp,
                            "region_sim": region_sim}
        modo = "región" if region else ("intelligent-search" if seg else "nacional")
        print(f"{nombre}: geoloc={modo}", file=sys.stderr)
        # 1) juntar los productos de la cadena (dedup por clave).
        # Cencosud (Vea/Jumbo) via intelligent-search: sólo disponibles reales.
        chain = {}
        for i, term in enumerate(TERMINOS, 1):
            if seg:
                prods = productos_is(dom, term, tp, args.tope, cookie=seg)
            else:
                prods = productos_termino(dom, term, region, args.tope)
            for pr in prods:
                clave = pr["e"] or pr["l"]
                if clave and clave not in chain:
                    chain[clave] = pr
            if i % 30 == 0:
                print(f"  {nombre}: {i}/{len(TERMINOS)} términos · {len(chain)} productos", file=sys.stderr)
        # 2) simulación de checkout (sin login)
        porsku = {}
        for pr in chain.values():
            if pr.get("sku"):
                porsku.setdefault(pr["sku"], (pr["sel"], []))[1].append(pr)
        items = [(sku, sel) for sku, (sel, _) in porsku.items()]
        if region:
            # cadenas de región: precio real de Tucumán + disponibilidad. Acá el
            # batch NO falsea la disponibilidad (verificado: coincide con de a 1),
            # así que se descarta lo sin stock / no entregable sin pedidos extra.
            sim = precios_tucuman(dom, region, items)
            no_entregable = set()
            for sku, (_, prs) in porsku.items():
                info = sim.get(sku)
                if info is None:
                    continue                # sin respuesta (error de red): se conserva
                precio_sim, avail = info
                if avail != "available":
                    no_entregable.add(sku)
                elif precio_sim:
                    for pr in prs:
                        pr["p"] = precio_sim
                        pr.pop("op", None)  # el precio simulado ya es el efectivo
            chain = {k: pr for k, pr in chain.items() if pr.get("sku") not in no_entregable}
            print(f"  {nombre}: {len(chain)} entregables · {len(no_entregable)} descartados "
                  f"(sin stock / no entregable)", file=sys.stderr)
        # Cencosud (Vea/Jumbo): se muestra el precio del ÍNDICE de intelligent-search
        # (lo que ve el cliente cuando busca en Vea/Jumbo) CON la promo del día aplicada.
        # NO se usa la simulación de checkout: sus precios/disponibilidad NO coinciden de
        # forma confiable con lo que muestra la web (las fuentes de Cencosud son volátiles y
        # se contradicen; a veces coincide el índice, a veces la simulación), y encima el
        # camino por simulación PERDÍA las promos. La promo (search-promotions) se aplica
        # sobre el precio del índice: "fixed" = precio de oferta; "pct" = base*(1-desc).
        # Ej.: Monster índice $3400 → promo fija $2600.
        elif seg and dom in SEARCH_PROMO_SELLER:
            # FILTRO DE FANTASMAS por DISPONIBILIDAD REAL (no por precio): se simula cada
            # producto DE A 2 (en lote falsea) y se descarta lo que NO se puede recibir en
            # Tucumán (availability != 'available') — la señal de "no se puede añadir al
            # carrito para Tucumán", mismo estándar que las cadenas de región. Verificado:
            # atún 120g fantasma = cannotBeDelivered; Veneziana real = available. El PRECIO
            # NO cambia (sigue siendo índice × promo); la simulación se usa SÓLO para filtrar.
            disp = precios_cencosud(dom, region_sim, items, sc=tp, cookie=seg)
            no_ent = {sku for sku in porsku
                      if disp.get(str(sku)) is not None and disp[str(sku)][1] != "available"}
            chain = {k: pr for k, pr in chain.items() if pr.get("sku") not in no_ent}
            # PRECIO: índice × promo del día (search-promotions), sin tocar
            promos = promos_cencosud(dom, [pr["sku"] for pr in chain.values() if pr.get("sku")],
                                     cookie=seg)
            n_promo = 0
            for pr in chain.values():
                info = promos.get(str(pr.get("sku")))
                if not info:
                    continue
                tipo, val = info
                nuevo = val if tipo == "fixed" else round(pr["p"] * (1 - val), 2)
                if nuevo and nuevo < pr["p"]:   # el precio con oferta ES el precio
                    pr["p"] = nuevo
                    n_promo += 1
            print(f"  {nombre}: {len(chain)} entregables · {len(no_ent)} descartados "
                  f"(fantasma/no entregable) · {n_promo} con promo", file=sys.stderr)
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

    # --- Tuchanguito (Tiendanube, cadena local de Tucumán, stock preciso) ---
    geoloc["Tuchanguito"] = True
    print("Tuchanguito: cadena local (Tiendanube)", file=sys.stderr)
    tchain = {}
    for i, term in enumerate(TERMINOS, 1):
        for pr in productos_tuchanguito(term):
            if pr["l"] and pr["l"] not in tchain:
                tchain[pr["l"]] = pr
        if i % 30 == 0:
            print(f"  Tuchanguito: {i}/{len(TERMINOS)} términos · {len(tchain)} productos", file=sys.stderr)
    for pr in tchain.values():
        g = grupos.get(pr["l"])
        if g is None:
            g = grupos[pr["l"]] = {"n": pr["n"], "m": "", "i": pr.get("i", ""),
                                   "pr": {}, "eans": set()}
        if not g["i"] and pr.get("i"):
            g["i"] = pr["i"]
        g["pr"]["Tuchanguito"] = [pr["p"], pr["l"]]
    print(f"  Tuchanguito: {len(tchain)} productos", file=sys.stderr)

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

    # === COMPLETAR LA COMPARACIÓN entre súper ===
    # Si un producto (por código de barras) está en unas cadenas y le falta otra que SÍ
    # lo tiene, se busca su EAN en esa cadena y se agrega el precio → el mismo producto
    # aparece en TODAS las cadenas que lo venden (que es lo que hace útil comparar). Es
    # ACOTADO: sólo completa productos ya capturados, buscando por EAN de a 40 por
    # consulta; NO crawlea el catálogo entero.
    def _nums(toks):
        return {t for t in toks if any(c.isdigit() for c in t)}
    ean2g = {}
    for g in finales:
        g["_toks"] = set(clave_fuzzy(g["n"], g.get("m", "")).split())
        for e in (g.get("eans") or ()):
            ean2g.setdefault(e, g)
    for nombre, cfg in chaincfg.items():
        # SÓLO se completan las cadenas de REGIÓN (Carrefour/Comodín/ChangoMás): la
        # simulación de checkout confirma precio real + disponibilidad. Cencosud NO se
        # completa por catálogo: trae precios VIEJOS de listados que ni aparecen en la
        # búsqueda de Vea/Jumbo (ej. tostadas $150 vs $1600 en las demás). Vea/Jumbo se
        # quedan con lo que captura su intelligent-search (visible y con precio actual).
        if not cfg["region"]:
            continue
        faltan = list({e for e, g in ean2g.items() if nombre not in g["pr"]})
        if not faltan:
            continue
        hallados = completar_por_ean(cfg["dom"], cfg["region"], faltan, cookie=cfg["seg"])
        if cfg["region"]:                    # región: precio real Tucumán + disponibilidad
            items = [(pr["sku"], pr["sel"]) for pr in hallados if pr.get("sku")]
            sim = precios_tucuman(cfg["dom"], cfg["region_sim"], items)
            for pr in hallados:
                info = sim.get(str(pr.get("sku")))
                if not info or info[1] != "available" or not info[0]:
                    pr["_drop"] = True
                else:
                    pr["p"] = info[0]
        elif cfg["seg"]:                     # Cencosud: NO hay simulación que valide, así que
            # se exige IsAvailable del catálogo (el desmenuzado fantasma da False → afuera;
            # el atún 120g con EAN del 170g lo caza el guard de tamaño). Precio índice × promo.
            promos = promos_cencosud(cfg["dom"], [pr["sku"] for pr in hallados
                                                  if pr.get("sku") and pr.get("disp")],
                                     cookie=cfg["seg"])
            for pr in hallados:
                if not pr.get("disp"):
                    pr["_drop"] = True
                    continue
                info = promos.get(str(pr.get("sku")))
                if info:
                    t, v = info
                    nuevo = v if t == "fixed" else round(pr["p"] * (1 - v), 2)
                    if nuevo and nuevo < pr["p"]:
                        pr["p"] = nuevo
        n_add = 0
        for pr in hallados:
            if pr.get("_drop"):
                continue
            g = next((ean2g[e] for e in (pr.get("eans") or ()) if e in ean2g), None)
            if g is None or nombre in g["pr"]:
                continue
            ptoks = set(clave_fuzzy(pr["n"], pr.get("m", "")).split())
            gn, pn = _nums(g["_toks"]), _nums(ptoks)
            if gn and pn and not (gn & pn):          # tamaños distintos = EAN mal cargado
                continue                             # (atún 120g con EAN del 170g)
            if _solape_nombre(ptoks, g["_toks"]) < 0.4:
                continue
            g["pr"][nombre] = [pr["p"], pr["l"]]
            n_add += 1
        print(f"  completar {nombre}: +{n_add} precios por EAN "
              f"({len(faltan)} EAN buscados)", file=sys.stderr)
    for g in finales:
        g.pop("_toks", None)

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

    # Vea/Jumbo se muestran ENTEROS, aunque el producto no esté en ninguna otra
    # cadena. Los fantasmas de Cencosud (SKUs discontinuados que no se pueden comprar)
    # ya se cortan EN LA FUENTE en productos_is, exigiendo vendedor default (= el que
    # muestra el botón "Agregar al carrito" en la web). Por eso acá ya NO se descartan
    # productos "sólo en Vea/Jumbo" ni se borran precios bajos por ancla confiable.

    todas_cadenas = list(TIENDAS) + ["Tuchanguito"]
    cadenas_meta = {n: {"geolocalizado": geoloc.get(n, False),
                        "productos": sum(1 for g in finales if n in g["pr"])}
                    for n in todas_cadenas}
    productos = [{"n": g["n"], "m": g["m"], "i": g["i"], "pr": g["pr"]} for g in finales]
    en_varias = sum(1 for g in finales if len(g["pr"]) > 1)
    out = {
        "fecha": time.strftime("%Y-%m-%d"),
        "actualizado": time.strftime("%Y-%m-%d %H:%M"),   # fecha + hora (local Tucumán)
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
