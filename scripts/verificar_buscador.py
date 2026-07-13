#!/usr/bin/env python3
"""
Verificación automática del buscador (CIET).

Después de cada scrape, toma una muestra al azar de productos de data/buscador.json
y re-consulta el precio EN VIVO de cada uno por su propio link, para cazar errores
que si no encuentra el usuario: precios fantasma/basura, links que llevan a otro
producto, o precios desfasados de la realidad.

Con QUÉ compara (clave, aprendido a los golpes):
  El precio guardado es el de TUCUMÁN (simulación de checkout en las cadenas de
  región) o el nacional con la PROMO del día aplicada (Vea/Jumbo). NO es el «Price»
  de catálogo nacional — de hecho en Tucumán muchas veces es MÁS caro que el nacional
  (regionalización). Por eso comparar contra el catálogo nacional da falsas alarmas.
  Acá se reconstruye el precio real por el MISMO camino público que la web:
    - Carrefour / Comodín / ChangoMás: simulación de checkout a CP 4000 (lo que paga
      un tucumano). Se compara guardado vs simulado (tolerancia ~15%, por variación
      intradía).
    - Vea / Jumbo (Cencosud): precio de catálogo nacional × (1 - promo del día).
  Además chequea que el link lleve al MISMO producto (solape de palabras del nombre),
  lo que caza links/agrupaciones equivocadas.

No pretende validar el centavo exacto (cambia entre scrape y chequeo); su objetivo es
cazar lo GROSERO, que es lo que rompe la confianza. Lo marcado es para revisión humana.

Uso:
    python3 verificar_buscador.py [--n 25] [--buscador data/buscador.json] [--seed N]
Sale con código 1 si hay productos marcados (para que la automatización pueda avisar).
"""
import argparse
import json
import random
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_buscador as fb   # reusa get/_norm/precios_tucuman/promos_cencosud/región…

# cadenas VTEX re-consultables por link (Tuchanguito es Tiendanube → se saltea)
DOM_VTEX = {"Carrefour": "www.carrefour.com.ar", "Vea": "www.vea.com.ar",
            "Jumbo": "www.jumbo.com.ar", "Comodín": "www.comodinencasa.com.ar",
            "ChangoMás": "www.masonline.com.ar"}
DOMS = set(DOM_VTEX.values())
CENCOSUD = {"www.vea.com.ar", "www.jumbo.com.ar"}   # sin región: precio nacional + promo

TOL = 0.15            # desfase tolerado guardado vs real (variación intradía)
# Solape de nombre bajo a propósito: sólo caza links a un producto TOTALMENTE distinto
# (Raid→Trapo de piso, ~0%). Un umbral alto daba falsos positivos con el MISMO producto
# reformulado por otra cadena ("Alfajor triple blanco" vs "Alfajores Blancos Maxi", 17%).
# Lo dudoso (solape bajo pero >0) igual pasa por el chequeo de precio, que es la otra red.
SOLAPE_MIN = 0.10
REINTENTOS = 4        # el endpoint por link throttlea; se reintenta antes de dar por fallado

_CTX = {}


def ctx(dom):
    """Contexto de geolocalización por cadena (se arma una vez, como en el scrape)."""
    if dom not in _CTX:
        region = fb.region_id(dom)
        seg = fb.segmento_tucuman(dom) if not region else None
        sc = fb.trade_policy(seg) if seg else None
        region_sim = region or (fb.region_id(dom, sc=sc, cookie=seg) if seg else None)
        _CTX[dom] = {"region": region, "seg": seg, "sc": sc, "region_sim": region_sim}
    return _CTX[dom]


def toks(s):
    t = fb._norm(s or "").replace(",", " ").replace(".", " ")
    t = "".join(c if (c.isalnum() or c == " ") else " " for c in t)
    return {w for w in t.split() if len(w) > 1 and w not in fb.STOP}


def solape(a, b):
    A, B = toks(a), toks(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def dom_de(link):
    try:
        return link.split("/")[2]
    except Exception:
        return ""


def slug_de(link, dom):
    try:
        return link.split(dom + "/", 1)[1].rsplit("/p", 1)[0]
    except Exception:
        return ""


def producto_vivo(dom, link, cookie=None):
    """Trae el producto por su slug (con reintentos ante throttle).
    Devuelve dict {nombre, sku, seller, price(catálogo), listprice} o None si no responde."""
    slug = slug_de(link, dom)
    if not slug:
        return None
    url = f"https://{dom}/api/catalog_system/pub/products/search/{urllib.parse.quote(slug)}/p"
    d = None
    for i in range(REINTENTOS):
        d = fb.get(url, cookie=cookie)
        if d:
            break
        time.sleep(0.8 * (i + 1))
    if not isinstance(d, list) or not d:
        return None
    try:
        p = d[0]
        item = p["items"][0]
        seller = next((s for s in item.get("sellers", []) if s.get("sellerDefault")),
                      None) or item["sellers"][0]
        o = seller["commertialOffer"]
        return {"nombre": p.get("productName", ""), "sku": item.get("itemId"),
                "seller": seller.get("sellerId"), "price": o.get("Price"),
                "listprice": o.get("ListPrice")}
    except Exception:
        return None


def precio_real_tucuman(dom, viv):
    """Reconstruye el precio que muestra la web HOY por ese producto, por el mismo camino
    que el scrape: Cencosud (Vea/Jumbo) = precio de catálogo/índice × promo del día;
    cadenas de región = simulación de checkout. Devuelve (precio, nota)."""
    c = ctx(dom)
    if dom in CENCOSUD:
        base = viv["price"] or 0                      # índice/catálogo = precio de la página
        # la simulación SÓLO valida disponibilidad (no aporta al precio), igual que el pipeline
        if c["region_sim"]:
            simc = fb.precios_cencosud(dom, c["region_sim"], [(viv["sku"], viv["seller"])],
                                       sc=c["sc"], cookie=c["seg"]).get(str(viv["sku"]))
            if simc and simc[1] and simc[1] != "available":
                return None, f"no disponible ({simc[1]})"
        if not base:
            return None, "sin precio"
        pinfo = fb.promos_cencosud(dom, [viv["sku"]], cookie=c["seg"]).get(str(viv["sku"]))
        precio = base
        if pinfo:                                     # mejor promo pública (generic+jumbo_prime)
            precio = min([base] + [v if t == "fixed" else round(base * (1 - v), 2)
                                   for t, v in pinfo])
        return precio, "índice+promo"
    if not c["region_sim"]:
        return viv["price"], "sin región (fallback catálogo)"
    sim = fb.precios_tucuman(dom, c["region_sim"], [(viv["sku"], viv["seller"])],
                             sc=c["sc"], cookie=c["seg"])
    precio, avail = sim.get(str(viv["sku"]), (None, None))
    if avail and avail != "available":
        return None, f"no disponible ({avail})"
    return precio, "simulado"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--buscador", default="data/buscador.json")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    data = json.loads(Path(args.buscador).read_text(encoding="utf-8"))
    productos = data.get("productos", [])
    print(f"buscador.json: {len(productos)} productos · {data.get('actualizado','?')}",
          file=sys.stderr)

    candidatos = [p for p in productos if any(c in DOM_VTEX and dom_de(o[1]) in DOMS
                                              for c, o in p["pr"].items() if len(o) > 1)]
    if not candidatos:
        print("Nada re-consultable.", file=sys.stderr)
        return
    muestra = random.Random(args.seed).sample(candidatos, min(args.n, len(candidatos)))

    ok = checks = 0
    duros, suaves = [], []
    for p in muestra:
        nombre = p["n"]
        for cad, o in p["pr"].items():
            if cad not in DOM_VTEX or len(o) < 2:
                continue
            guardado, link = o[0], o[1]
            dom = dom_de(link)
            if dom not in DOMS:
                continue
            checks += 1
            viv = producto_vivo(dom, link, cookie=ctx(dom)["seg"])
            if viv is None:
                suaves.append(("SIN_RESPUESTA", cad, nombre,
                               f"el link no respondió tras {REINTENTOS} intentos (¿throttle/agotado?)"))
                continue
            sol = solape(nombre, viv["nombre"])
            if sol < SOLAPE_MIN:
                duros.append(("LINK_OTRO_PRODUCTO", cad, nombre,
                              f"el link lleva a «{viv['nombre']}» (solape {sol:.0%})"))
                continue
            real, nota = precio_real_tucuman(dom, viv)
            if not real:
                suaves.append(("SIN_PRECIO_VIVO", cad, nombre,
                               f"guardado ${guardado} · {nota}"))
                continue
            ratio = guardado / real
            if abs(ratio - 1) > TOL:
                duros.append(("PRECIO_DESFASADO", cad, nombre,
                              f"guardado ${guardado} vs real Tucumán ${real} "
                              f"({nota}, ratio {ratio:.2f})"))
                continue
            ok += 1
            time.sleep(0.05)

    print(f"\n=== Verificación buscador — {len(muestra)} productos, {checks} chequeos "
          f"(precio×cadena) ===")
    print(f"OK: {ok}/{checks}  ·  a revisar: {len(duros)}  ·  avisos: {len(suaves)}")
    if duros:
        print("\n⚠️  A REVISAR:")
        for sev, cad, nom, det in duros:
            print(f"  [{sev}] {cad} · {nom}\n        {det}")
    if suaves:
        print("\n(info) no se pudo confirmar precio en vivo:")
        for sev, cad, nom, det in suaves:
            print(f"  [{sev}] {cad} · {nom} — {det}")
    if not duros and not suaves:
        print("Todo coincide. ✔")

    sys.exit(1 if duros else 0)


if __name__ == "__main__":
    main()
