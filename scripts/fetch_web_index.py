#!/usr/bin/env python3
"""
IPS Web — índice de precios a partir de las tiendas online (CIET).

A diferencia del IPS-SEPA (lo que las cadenas declaran al Estado), este índice
usa el precio que se paga comprando online. Incluye cadenas que no reportan bien
a SEPA (Comodín, ChangoMás). Consulta las APIs de catálogo VTEX por código EAN,
geolocalizado en Tucumán donde la tienda lo permite.

Uso:
    python3 fetch_web_index.py [--muestra N] [--catalogo data/productos.json]

Relevamiento de baja intensidad (una consulta por producto y cadena, con pausa)
sobre endpoints públicos. Sin uso comercial.
"""
import argparse
import json
import math
import statistics
import sys
import time
import urllib.request
import urllib.parse
from collections import defaultdict
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
CP_TUCUMAN = "4000"

# nombre para mostrar -> dominio de la tienda online (todas VTEX)
TIENDAS = {
    "Carrefour": "www.carrefour.com.ar",
    "Vea": "www.vea.com.ar",
    "Jumbo": "www.jumbo.com.ar",
    "Comodín": "www.comodinencasa.com.ar",
    "ChangoMás": "www.masonline.com.ar",
}
# cadenas que forman el índice (canasta = intersección de estas)
PRINCIPALES = ["Carrefour", "Vea", "Jumbo", "Comodín", "ChangoMás"]


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
            time.sleep(0.8)
    return None


def region_id(dominio):
    d = get(f"https://{dominio}/api/checkout/pub/regions/?country=ARG&postalCode={CP_TUCUMAN}")
    if isinstance(d, list) and d:
        return d[0].get("id")
    return None


def segmento_tucuman(dominio):
    """Cencosud (Vea/Jumbo) no expone /regions; su precio de Tucumán se obtiene
    con la cookie vtex_segment que setea la API de sesión al fijar el CP 4000."""
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    body = json.dumps({"public": {"country": {"value": "ARG"},
                                  "postalCode": {"value": CP_TUCUMAN}}}).encode()
    for url in (f"https://{dominio}/api/sessions",
                f"https://{dominio}/api/sessions?items=checkout.regionId"):
        try:
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"User-Agent": UA, "Content-Type": "application/json"})
            op.open(req, timeout=12)
        except Exception:
            pass
    for c in cj:
        if c.name == "vtex_segment":
            return c.value
    return None


def precio_por_ean(dominio, ean, region, cookie=None):
    q = urllib.parse.quote(f"alternateIds_Ean:{ean}")
    url = f"https://{dominio}/api/catalog_system/pub/products/search?fq={q}"
    if region:
        url += f"&regionId={urllib.parse.quote(region)}"
    d = get(url, cookie=cookie)
    if not isinstance(d, list) or not d:
        return None
    prod = d[0]
    item = next((it for it in prod.get("items", []) if it.get("ean") == ean),
                (prod.get("items") or [None])[0])
    if not item:
        return None
    seller = item["sellers"][0]
    precio = seller.get("commertialOffer", {}).get("Price")
    if not precio:
        return None
    return {"precio": round(precio, 2), "link": prod.get("link"),
            "sku": item.get("itemId"), "sel": seller.get("sellerId")}


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


def precios_tucuman(dominio, region, items):
    """Precio real de entrega en Tucumán (simulación de checkout). items: (sku,seller)."""
    if not region or not items:
        return {}
    url = f"https://{dominio}/api/checkout/pub/orderForms/simulation?RnbBehavior=0&regionId={urllib.parse.quote(region)}"
    out = {}
    for i in range(0, len(items), 40):
        body = {"items": [{"id": s, "quantity": 1, "seller": v} for s, v in items[i:i + 40]],
                "country": "ARG", "postalCode": CP_TUCUMAN}
        d = post_json(url, body)
        if d and d.get("items"):
            for it in d["items"]:
                sp = it.get("sellingPrice")
                if sp and it.get("id"):
                    out[str(it["id"])] = round(sp / 100, 2)
        time.sleep(0.2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestra", type=int, default=400)
    ap.add_argument("--catalogo", default="data/productos.json")
    ap.add_argument("-o", "--salida", default="data/web_index.json")
    args = ap.parse_args()

    cat = json.loads(Path(args.catalogo).read_text(encoding="utf-8"))
    productos = cat["productos"]
    if args.muestra and args.muestra < len(productos):
        paso = len(productos) / args.muestra
        idx = sorted({int(i * paso) for i in range(args.muestra)})
        muestra = [productos[i] for i in idx]
    else:
        muestra = productos

    print("Resolviendo contexto Tucumán…", file=sys.stderr)
    regiones, segmentos, geoloc = {}, {}, {}
    for n, dom in TIENDAS.items():
        r = region_id(dom)
        seg = segmento_tucuman(dom) if not r else None
        regiones[n] = r
        segmentos[n] = seg
        geoloc[n] = bool(r or seg)
        modo = "Tucumán (región)" if r else ("Tucumán (segmento)" if seg else "nacional")
        print(f"  {n}: {modo}", file=sys.stderr)

    # precios por cadena por EAN
    precios = defaultdict(dict)   # cadena -> ean -> precio
    links = defaultdict(dict)
    skus = defaultdict(dict)      # cadena -> ean -> (sku, seller)
    desc = {}
    total = len(muestra)
    for i, p in enumerate(muestra, 1):
        ean = p["ean"]
        desc[ean] = p["descripcion"]
        for n, dom in TIENDAS.items():
            res = precio_por_ean(dom, ean, regiones[n], cookie=segmentos[n])
            if res:
                precios[n][ean] = res["precio"]
                links[n][ean] = res["link"]
                if res.get("sku"):
                    skus[n][ean] = (res["sku"], res["sel"])
            time.sleep(0.18)
        if i % 25 == 0 or i == total:
            hall = {n: len(precios[n]) for n in TIENDAS}
            print(f"  {i}/{total} · hallados {hall}", file=sys.stderr)

    # precio REAL de entrega en Tucumán (simulación de checkout, sin login)
    for n, dom in TIENDAS.items():
        if not regiones[n] or not skus[n]:
            continue
        eans = list(skus[n])
        sim = precios_tucuman(dom, regiones[n], [skus[n][e] for e in eans])
        cambiados = 0
        for e in eans:
            sku = skus[n][e][0]
            if sku in sim:
                if sim[sku] != precios[n][e]:
                    cambiados += 1
                precios[n][e] = sim[sku]
        print(f"  {n}: precio Tucumán aplicado ({cambiados} cambios)", file=sys.stderr)

    # canasta: intersección de las cadenas con buena cobertura (>=25% de la
    # muestra). Las de cobertura parcial se muestran igual, sin costo de canasta.
    umbral = max(15, int(0.25 * total))
    presentes = [n for n in PRINCIPALES if len(precios[n]) >= umbral]
    if len(presentes) < 2:
        # fallback: las 2 con más cobertura
        presentes = sorted(PRINCIPALES, key=lambda n: -len(precios[n]))[:2]
    canasta = set(precios[presentes[0]])
    for n in presentes[1:]:
        canasta &= set(precios[n])
    canasta = sorted(canasta)

    resumen = []
    for n in TIENDAS:
        en_canasta = [e for e in canasta if e in precios[n]]
        costo = round(sum(precios[n][e] for e in en_canasta), 2) if n in presentes and en_canasta else None
        resumen.append({
            "cadena": n,
            "geolocalizado": geoloc[n],
            "productos_hallados": len(precios[n]),
            "en_indice": n in presentes,
            "canasta_costo": costo,
        })
    resumen.sort(key=lambda x: (x["canasta_costo"] is None, x["canasta_costo"] or 0))

    def brecha(ean):
        ps = [precios[n][ean] for n in presentes if ean in precios[n]]
        return round(max(ps) / min(ps), 3) if len(ps) >= 2 else 1

    prod_out = []
    for ean in sorted(canasta, key=brecha, reverse=True):
        pr = {n: precios[n][ean] for n in presentes if ean in precios[n]}
        lk = {n: links[n].get(ean) for n in presentes if ean in precios[n]}
        vals = list(pr.values())
        prod_out.append({
            "ean": ean, "descripcion": desc.get(ean, ean)[:80],
            "precios": pr, "links": lk,
            "min": min(vals), "max": max(vals), "brecha": round(max(vals) / min(vals), 3),
        })

    fecha = time.strftime("%Y-%m-%d")
    out = {
        "fecha": fecha,
        "fuente": "Tiendas online (VTEX) de supermercados con venta en Tucumán.",
        "cadenas": PRINCIPALES,
        "geolocalizado": geoloc,
        "canasta_n": len(canasta),
        "muestra": total,
        "resumen": resumen,
        "productos": prod_out,
    }
    Path(args.salida).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    # --- serie temporal encadenada del índice online (acumula hacia adelante) ---
    dcarpeta = Path(args.salida).parent
    hoy_precios = {f"{n}|{e}": precios[n][e] for n in presentes for e in canasta}
    last_path = dcarpeta / "web_last.json"
    serie_path = dcarpeta / "serie_web.json"
    prev = json.loads(last_path.read_text(encoding="utf-8")) if last_path.exists() else {}
    puntos = (json.loads(serie_path.read_text(encoding="utf-8")).get("puntos", [])
              if serie_path.exists() else [])
    puntos = [p for p in puntos if p["fecha"] != fecha]  # reemplaza el de hoy si existe
    if not puntos or not prev:
        indice, var, npares = (puntos[-1]["indice"] if puntos else 100.0), None, None
    else:
        comunes = [k for k in hoy_precios.keys() & prev.keys() if prev[k] > 0 and hoy_precios[k] > 0]
        if comunes:
            ratio = math.exp(sum(math.log(hoy_precios[k] / prev[k]) for k in comunes) / len(comunes))
            var, npares = round((ratio - 1) * 100, 3), len(comunes)
            indice = round(puntos[-1]["indice"] * ratio, 4)
        else:
            indice, var, npares = puntos[-1]["indice"], None, 0
    puntos.append({"fecha": fecha, "indice": round(indice, 2), "var_pct": var,
                   "canasta_n": len(canasta), "pares": npares})
    puntos.sort(key=lambda p: p["fecha"])
    serie_path.write_text(json.dumps({
        "actualizado": fecha,
        "base": "índice de precios online encadenado (Jevons), base 100 en el primer día",
        "cadenas": presentes,
        "puntos": puntos,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    last_path.write_text(json.dumps(hoy_precios, ensure_ascii=False), encoding="utf-8")

    print(f"OK → {args.salida} ({len(canasta)} en canasta, {len(presentes)} cadenas) "
          f"· serie_web {len(puntos)} pto(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
