#!/usr/bin/env python3
"""
Motor de DEMANDA — MercadoLibre Argentina (CIET).

La biblioteca de anuncios dice qué se está *promocionando*; MercadoLibre dice qué
se está *buscando y comprando*. Cruzar las dos es lo que separa un producto que
alguien está testeando de uno que ya tiene demanda real.

Dos fuentes, ambas públicas y sin credenciales (la API de ML sí pide OAuth, pero
estas páginas no):
  · tendencias.mercadolibre.com.ar → 50 búsquedas en tendencia, en tres bloques:
    las 10 de mayor crecimiento, 20 más deseadas y 20 más populares de la semana.
  · mercadolibre.com.ar/mas-vendidos → productos más vendidos con precio real,
    que además sirve para estimar a cuánto se vende un producto ganador.

Uso:
    python3 scripts/scrape_mercadolibre.py -o /tmp/mercadolibre_ar.json
"""
import argparse
import datetime
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
URL_TEND = "https://tendencias.mercadolibre.com.ar/"
URL_MV = "https://www.mercadolibre.com.ar/mas-vendidos"


def bajar(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "es-AR,es;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def des(s):
    """Deshace el escapado \\u002F que usa ML en su JSON embebido."""
    try:
        return json.loads('"' + s.replace('"', '\\"') + '"')
    except Exception:
        return s.replace("\\u002F", "/")


# Marcas: no sirven para revender (no se consiguen al por mayor sin ser oficial,
# y compiten contra el precio de las tiendas grandes). El negocio son los
# productos genéricos / sin marca.
MARCAS = {
    "xiaomi", "redmi", "samsung", "galaxy", "motorola", "moto", "apple", "iphone",
    "ipad", "airpods", "jbl", "sony", "lg", "philips", "huawei", "lenovo", "hp",
    "dell", "asus", "acer", "tcl", "noblex", "bgh", "whirlpool", "drean", "atma",
    "liliana", "peabody", "oster", "moulinex", "braun", "gillette", "nivea",
    "nike", "adidas", "puma", "reebok", "topper", "converse", "levis", "gopro",
    "logitech", "microsoft", "xbox", "playstation", "nintendo", "amazfit",
    "realme", "oppo", "vivo", "honor", "nokia", "alcatel", "tp-link", "hisense",
    "electrolux", "gaggia", "nespresso", "dolce", "smartlife", "kanji", "enova",
}


def es_marca(titulo):
    """¿El producto es de una marca conocida? Esos no se revenden."""
    palabras = set(norm(titulo).replace("-", " ").split())
    return bool(palabras & MARCAS)


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def tendencias():
    """Las 50 búsquedas en tendencia. El orden importa: ML las devuelve por bloque."""
    html = bajar(URL_TEND)
    kws, vistos = [], set()
    for k in re.findall(r'"keyword":"([^"]{2,60})"', html):
        k = des(k)
        if norm(k) not in vistos:
            vistos.add(norm(k))
            kws.append(k)
    return {"crecimiento": kws[:10], "deseadas": kws[10:30], "populares": kws[30:50],
            "todas": kws}


def mas_vendidos():
    """Productos más vendidos, con precio y link."""
    html = bajar(URL_MV)
    out, vistos = [], set()
    patron = re.compile(
        r'"categoryId":"(MLA\d+)".{0,200}?"title":"([^"]{6,120})".{0,400}?'
        r'"permalink":"([^"]+)".{0,300}?"thumbnail":"([^"]*)".{0,200}?"price":([0-9.]+)',
        re.S)
    for cat, tit, link, img, precio in (m.groups() for m in patron.finditer(html)):
        tit = des(tit)
        if norm(tit) in vistos:
            continue
        vistos.add(norm(tit))
        try:
            precio = float(precio)
        except ValueError:
            precio = None
        if es_marca(tit):
            continue
        out.append({"titulo": tit, "categoria": cat, "precio": precio,
                    "link": des(link).split("#")[0], "img": des(img)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="/tmp/mercadolibre_ar.json")
    ap.add_argument("--productos", help="productos_ar.json, para cruzar con los ganadores")
    args = ap.parse_args()

    print("Bajando tendencias de MercadoLibre…")
    try:
        tend = tendencias()
        print(f"  {len(tend['todas'])} búsquedas en tendencia")
    except Exception as e:
        sys.exit(f"Falló tendencias: {e}")

    print("Bajando más vendidos…")
    try:
        mv = mas_vendidos()
        print(f"  {len(mv)} productos más vendidos")
    except Exception as e:
        print(f"  ! falló más vendidos: {e}", file=sys.stderr)
        mv = []

    # Cruce: ¿alguno de los productos ganadores aparece en las tendencias o en los
    # más vendidos? Ese es el producto con publicidad Y demanda comprobada.
    cruce = []
    if args.productos and Path(args.productos).exists():
        d = json.loads(Path(args.productos).read_text(encoding="utf-8"))
        # Se agrupa por producto: en el ranking hay varios ítems de la misma
        # categoría (varios masajeadores distintos), y acá interesa la categoría.
        agg = {}
        for p in d.get("productos", []):
            t = (p.get("titulo") or "").strip()
            if not t:
                continue
            a = agg.setdefault(t, {"anuncios": 0, "vendedores": 0, "items": 0})
            a["anuncios"] += p.get("anuncios") or 0
            a["vendedores"] = max(a["vendedores"], p.get("vendedores") or 0)
            a["items"] += 1

        for titulo, a in agg.items():
            t = norm(titulo)
            # La coincidencia se exige sobre la palabra PRINCIPAL del producto
            # ("freidora", "masajeador"). Si no, "freidora de aire" matchea con
            # "aire acondicionado" sólo por compartir "aire".
            palabras = [w for w in t.split() if len(w) > 3]
            if not palabras:
                continue
            clave = palabras[0]
            en_tend = [k for k in tend["todas"] if clave in norm(k)]
            en_mv = [m for m in mv if clave in norm(m["titulo"])]
            if not (en_tend or en_mv):
                continue
            precios = [m["precio"] for m in en_mv if m.get("precio")]
            cruce.append({
                "producto": titulo,
                "link": ("https://listado.mercadolibre.com.ar/"
                         + urllib.parse.quote(titulo.lower())),
                "anuncios": a["anuncios"],
                "vendedores": a["vendedores"],
                "variantes": a["items"],
                "en_tendencias": en_tend[:5],
                "en_mas_vendidos": len(en_mv),
                "precio_ml_min": min(precios) if precios else None,
                "precio_ml_max": max(precios) if precios else None,
            })
        # Los que están en tendencias pesan más: publicidad + demanda buscada.
        cruce.sort(key=lambda c: (-len(c["en_tendencias"]), -(c["anuncios"] or 0)))
        print(f"  {len(cruce)} productos ganadores con señal en MercadoLibre")

    salida = {
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "fuente": "MercadoLibre Argentina — tendencias y más vendidos (páginas públicas)",
        "tendencias": tend,
        "mas_vendidos": mv[:60],
        "cruce": cruce,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(salida, ensure_ascii=False), encoding="utf-8")
    print(f"\nOK → {args.out}")


if __name__ == "__main__":
    main()
