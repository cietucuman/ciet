#!/usr/bin/env python3
"""
Motor de PRODUCTOS ganadores — Biblioteca de anuncios de Meta, Argentina (CIET).

No rankea anunciantes (una página vende mil cosas), sino PRODUCTOS: agrupa los
anuncios por su imagen (huella perceptual), de modo que el mismo producto —aunque
lo vendan cuentas distintas— cae en un solo grupo. Un producto ganador es el que
tiene muchos anuncios duplicados, de varios vendedores, y hace tiempo al aire.

Meta bloquea todo lo que no sea navegador real, así que usa Playwright y corre en
tu máquina. Baja las miniaturas (60×60, gratis) para comparar imágenes.

Uso:
    python3 scripts/scrape_biblioteca_ar.py -o /tmp/productos_ar.json
    python3 scripts/scrape_biblioteca_ar.py --keywords data/ecommerce/keywords.txt
    python3 scripts/scrape_biblioteca_ar.py --scrolls 15   # más anuncios por producto

Requisitos (una sola vez):
    pip3 install --user playwright pillow && python3 -m playwright install chromium
"""
import argparse
import base64
import concurrent.futures
import re
import datetime
import io
import json
from collections import Counter, defaultdict
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Falta Playwright: pip3 install --user playwright && python3 -m playwright install chromium")
try:
    from PIL import Image
except ImportError:
    sys.exit("Falta Pillow: pip3 install --user pillow")

URL = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
       "&country={pais}&q={q}&media_type=all&search_type=keyword_unordered")

KEYWORDS_DEFAULT = [
    "freidora de aire", "proyector", "cepillo alisador", "masajeador",
    "lampara de luna", "aspiradora inalambrica", "reloj inteligente",
    "auriculares inalambricos", "camara seguridad wifi", "depiladora laser",
    "humidificador", "organizador",
]

# Extrae una fila por anuncio: id, anunciante, antigüedad, texto, imagen creativa.
JS_ADS = r"""() => {
  const meses={ene:0,feb:1,mar:2,abr:3,may:4,jun:5,jul:6,ago:7,sep:8,oct:9,nov:10,dic:11};
  const cnt=s=>(s.match(/Identificador de la biblioteca/g)||[]).length;
  const marks=[...document.querySelectorAll('div')].filter(el=>/Identificador de la biblioteca/.test(el.textContent)&&cnt(el.textContent)===1&&el.querySelectorAll('div').length<3);
  const out=[];const seen=new Set();
  for(const m of marks){let c=m;while(c.parentElement&&cnt(c.parentElement.textContent)===1){c=c.parentElement;}
    if(seen.has(c))continue;seen.add(c);
    const t=c.innerText||'';
    const id=(t.match(/biblioteca:\s*([0-9]+)/)||[])[1];
    const adv=(t.match(/([^\n]+)\n\s*Publicidad/)||[])[1];
    const dm=t.match(/desde el (\d{1,2}) (\w{3})\.?\s*(\d{4})/);let dias=null;
    if(dm&&meses[dm[2].toLowerCase()]!=null){const dt=new Date(+dm[3],meses[dm[2].toLowerCase()],+dm[1]);dias=Math.round((Date.now()-dt)/864e5);}
    const desp=t.split(/\n\s*Publicidad\s*\n/)[1]||'';
    const texto=desp.split('\n').map(x=>x.trim()).filter(x=>x&&!/^(Ver detalles|Ver resumen|Abrir|Me gusta|Más información|Comprar|Enviar mensaje|Reservar|Registrar|Contact|Descargar|Solicitar|Suscribir)/.test(x)).slice(0,2).join(' ').slice(0,180);
    // Imagen: la miniatura chica (60×60) para comparar barato, y la grande
    // (poster de video o el creativo) para mostrar. dHash da igual con cualquier tamaño.
    const imgs=[...c.querySelectorAll('img[src*="t39.35426"]')];
    let small=null,sA=Infinity,big=null,bA=0;
    for(const im of imgs){const a=(im.naturalWidth||0)*(im.naturalHeight||0); if(a>0){if(a<sA){sA=a;small=im.src;} if(a>bA){bA=a;big=im.src;}}}
    if(!small&&imgs[0])small=imgs[0].src;
    const poster=[...c.querySelectorAll('video')].map(v=>v.poster).filter(Boolean)[0];
    const imgBig=poster||big||small;
    // Link de destino: adónde manda el anuncio. Es lo que separa un PRODUCTO que
    // alguien vende (tienda con precio) de una app, un formulario o un servicio.
    let destino=null;
    for(const a of c.querySelectorAll('a[href*="l.facebook.com/l.php"]')){
      const m=a.href.match(/[?&]u=([^&]+)/);
      if(m){ try{ destino=decodeURIComponent(m[1]); }catch(e){} break; }
    }
    out.push({id,adv:adv?adv.trim():null,dias,versiones:/varias versiones/.test(t),texto,thumb:(small||imgBig),img:imgBig,destino});
  }
  return out;
}"""


PAIS = ["AR"]   # país en curso (lo fija main con --pais)


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def dhash(data: bytes, size: int = 8):
    """Huella perceptual (difference hash) de una imagen. Devuelve un entero de
    size*size bits; imágenes parecidas dan huellas con pocos bits de diferencia."""
    try:
        im = Image.open(io.BytesIO(data)).convert("L").resize((size + 1, size), Image.LANCZOS)
    except Exception:
        return None
    px = list(im.getdata())
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def bajar(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except Exception:
        return None


def imagen_display(url, lado=440):
    """Baja la imagen grande del producto y la deja lista para mostrar:
    redimensionada a `lado` px máx y comprimida, como data URI base64."""
    data = bajar(url)
    if not data:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((lado, lado), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


# Destinos que NO son un producto a la venta: apps, redes, formularios.
NO_TIENDA = re.compile(
    r"(play\.google\.com|apps\.apple\.com|itunes\.apple|facebook\.com|instagram\.com|"
    r"m\.me|t\.me|wa\.me|api\.whatsapp|youtube\.com|tiktok\.com|kwai|linktr\.ee|"
    r"forms\.gle|docs\.google\.com|typeform|"
    # apps disfrazadas de producto (series, juegos, casino) y marketplaces:
    # ninguna es un producto que puedas revender.
    r"reelshort|dramabox|goodshort|shortmax|melolo|netshort|"
    r"^app\.|\.app\.|temu\.com|shein\.com|aliexpress|alibaba|wish\.com|"
    r"amazon\.|mercadolibre\.|mercadolivre\.|ebay\.|walmart\.|"
    r"onlyfans|betano|bet365|codere|casino)", re.I)


def precio_landing(url):
    """Precio al que se vende el producto en la tienda del anunciante.

    Es el número que faltaba: comparado con el costo del mayorista, da el margen
    REAL que está haciendo quien ya lo vende. Se lee del JSON-LD (schema.org) o
    del meta og:price, que publican Shopify y TiendaNube.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
            "Accept-Language": "es-AR,es;q=0.9"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(1_500_000).decode("utf-8", errors="ignore")
    except Exception:
        return None, None
    # 1) JSON-LD Product
    for bloque in re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(bloque)
        except Exception:
            continue
        for it in (d if isinstance(d, list) else [d]):
            if not isinstance(it, dict) or it.get("@type") != "Product":
                continue
            of = it.get("offers") or {}
            if isinstance(of, list):
                of = of[0] if of else {}
            if isinstance(of, dict) and of.get("price"):
                try:
                    return float(str(of["price"]).replace(",", ".")), of.get("priceCurrency", "ARS")
                except ValueError:
                    pass
    # 2) meta og:price
    m = re.search(r'og:price:amount"\s+content="([0-9.,]+)"', html)
    if m:
        v = m.group(1).replace(".", "").replace(",", ".")
        try:
            return float(v), "ARS"
        except ValueError:
            pass
    return None, None


PERFIL_TIENDA = {}   # dominio -> perfil (se consulta una vez por corrida)


def perfil_tienda(dominio):
    """Ficha de la tienda: cuántos productos vende y de qué tipo es.

    Shopify publica el catálogo entero en /products.json sin autenticación. Con
    eso se sabe si la tienda es de NICHO (vive de uno o dos productos, señal
    fuerte de que ese producto funciona) o una tienda general que vende de todo.
    """
    if dominio in PERFIL_TIENDA:
        return PERFIL_TIENDA[dominio]
    perfil = {"catalogo": None, "tipo": None, "plataforma": None}
    try:
        req = urllib.request.Request(
            f"https://{dominio}/products.json?limit=250",
            headers={"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")})
        with urllib.request.urlopen(req, timeout=18) as r:
            datos = json.loads(r.read())
        n = len(datos.get("products", []))
        perfil["plataforma"] = "Shopify"
        perfil["catalogo"] = n
        if n <= 3:
            perfil["tipo"] = "mono-producto"
        elif n <= 15:
            perfil["tipo"] = "nicho"
        elif n <= 60:
            perfil["tipo"] = "especializada"
        else:
            perfil["tipo"] = "general"
    except Exception:
        pass
    PERFIL_TIENDA[dominio] = perfil
    return perfil


def ficha_jsonld(url):
    """Ficha del producto en tiendas que no son Shopify (TiendaNube y demás).

    No tienen producto.json, pero sí JSON-LD en la página, que trae nombre, fotos
    y precio. Entre Shopify y TiendaNube está casi todo el comercio argentino.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
            "Accept-Language": "es-AR,es;q=0.9"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(1_500_000).decode("utf-8", errors="ignore")
    except Exception:
        return {}
    for bloque in re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(bloque)
        except Exception:
            continue
        for it in (d if isinstance(d, list) else [d]):
            if not isinstance(it, dict) or it.get("@type") != "Product":
                continue
            img = it.get("image")
            fotos = [i for i in (img if isinstance(img, list) else [img]) if isinstance(i, str)][:8]
            of = it.get("offers") or {}
            if isinstance(of, list):
                of = of[0] if of else {}
            precio = None
            try:
                precio = float(str(of.get("price", "")).replace(",", "."))
            except (ValueError, AttributeError):
                pass
            return {"fotos_tienda": fotos, "nombre_tienda": (it.get("name") or "")[:110],
                    "precio_tienda": precio}
    return {}


def ficha_tienda_producto(url):
    """Ficha del producto en la tienda que lo vende: fotos y precio exacto.

    Shopify devuelve el producto completo agregando .json a la URL. Las fotos son
    las que usa el vendedor (mucho mejores que el frame del anuncio) y sus URLs
    del CDN son estables, así que se guardan como link y no como imagen embebida.
    """
    if not url or "/products/" not in url:
        return {}
    limpia = url.split("?")[0].rstrip("/")
    try:
        req = urllib.request.Request(limpia + ".json", headers={
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")})
        with urllib.request.urlopen(req, timeout=18) as r:
            prod = json.loads(r.read()).get("product") or {}
    except Exception:
        return ficha_jsonld(limpia)
    fotos = [i.get("src") for i in (prod.get("images") or []) if i.get("src")][:8]
    precio = None
    vs = prod.get("variants") or []
    if vs:
        try:
            precio = float(str(vs[0].get("price", "")).replace(",", "."))
        except ValueError:
            pass
    return {"fotos_tienda": fotos, "nombre_tienda": (prod.get("title") or "")[:110],
            "precio_tienda": precio}


def dominio_de(url):
    try:
        return re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc).lower()
    except Exception:
        return None


def scrape_keyword(page, kw, scrolls, espera_ms=8000):
    try:
        page.goto(URL.format(q=kw.replace(" ", "%20"), pais=PAIS[0]),
                  wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"    ! error navegando '{kw}': {e}", file=sys.stderr)
        return []
    page.wait_for_timeout(3000)
    for _ in range(scrolls):
        try:
            page.mouse.wheel(0, 4200)
            page.wait_for_timeout(1000)
        except Exception:
            break
    try:
        return page.evaluate(JS_ADS) or []
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="/tmp/productos_ar.json")
    ap.add_argument("--keywords")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--pais", default="AR",
                    help="país de la biblioteca: AR, ES, MX… (default AR). "
                         "España suele adelantar tendencias que después llegan acá.")
    ap.add_argument("--scrolls", type=int, default=12, help="scrolls por producto (más = más anuncios)")
    ap.add_argument("--umbral", type=int, default=6, help="bits de tolerancia para 'misma imagen' (0-64; menos = más estricto)")
    ap.add_argument("--tope", type=int, default=60, help="máximo de productos en la salida")
    ap.add_argument("--pausa", type=float, default=4.0)
    ap.add_argument("--todos", dest="solo_con_precio", action="store_false",
                    help="no filtrar: incluir también productos sin precio de venta")
    args = ap.parse_args()
    PAIS[0] = args.pais.upper()

    if args.keywords:
        kws = [l.strip() for l in Path(args.keywords).read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith("#")]
    else:
        kws = KEYWORDS_DEFAULT

    print(f"Buscando anuncios de {len(kws)} categorías (país {PAIS[0]})…")
    ads = []
    perfil = Path.home() / ".ciet_playwright"
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(perfil), headless=args.headless, locale="es-AR",
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        for i, kw in enumerate(kws, 1):
            fila = scrape_keyword(page, kw, args.scrolls)
            for a in fila:
                if a.get("img"):
                    a["keyword"] = kw
                    ads.append(a)
            print(f"  [{i}/{len(kws)}] {kw!r}: {len(fila)} anuncios con imagen")
            if i < len(kws):
                time.sleep(args.pausa)
        ctx.close()

    # Bajar la miniatura chica (liviana) y calcular la huella de cada anuncio.
    print(f"Comparando imágenes de {len(ads)} anuncios…")
    def procesar(a):
        a["_hash"] = dhash(bajar(a["thumb"]))
        return a
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        ads = list(ex.map(procesar, ads))
    ads = [a for a in ads if a.get("_hash") is not None]

    # Agrupar por imagen: cada grupo = un producto. Greedy por distancia de Hamming.
    grupos = []  # cada uno: {"rep": hash, "ads": [...]}
    for a in ads:
        h = a["_hash"]
        for g in grupos:
            if hamming(h, g["rep"]) <= args.umbral:
                g["ads"].append(a)
                break
        else:
            grupos.append({"rep": h, "ads": [a]})

    # Construir productos con sus métricas.
    productos = []
    for g in grupos:
        gads = g["ads"]
        vendedores = sorted({a["adv"] for a in gads if a["adv"]})
        dias = [a["dias"] for a in gads if a["dias"] is not None]
        dias_max = max(dias) if dias else None
        rep = max(gads, key=lambda a: (a["dias"] or 0, len(a.get("texto") or "")))
        n_ads = len(gads)
        n_vend = len(vendedores)
        # Puntaje: duplicación (anuncios) × pluralidad de vendedores × antigüedad.
        f_edad = 1 + min(dias_max, 365) / 365 if dias_max else 1
        score = round(n_ads * (1 + 0.6 * (n_vend - 1)) * f_edad, 1)
        # Título = la categoría del anuncio que se muestra (el representativo), así
        # el título coincide con la imagen. Si no, la categoría dominante del grupo.
        kw_dom = rep.get("keyword") or Counter(a["keyword"] for a in gads).most_common(1)[0][0]
        titulo = kw_dom[:1].upper() + kw_dom[1:]
        # Detalle por vendedor, con IDs de sus anuncios para linkear a cada uno.
        por_vend = defaultdict(list)
        for a in gads:
            if a.get("id"):
                por_vend[(a.get("adv") or "—")].append(a["id"])
        detalle = [{"adv": v, "n": len(ids), "ids": ids[:6]}
                   for v, ids in sorted(por_vend.items(), key=lambda x: -len(x[1]))][:20]
        # Destino más frecuente del grupo: la tienda donde se vende.
        dests = [a.get("destino") for a in gads if a.get("destino")
                 and not NO_TIENDA.search(a["destino"])]
        rep_dest = rep.get("destino")
        if rep_dest and not NO_TIENDA.search(rep_dest):
            destino = rep_dest          # el del anuncio que se muestra: manda
        else:
            destino = Counter(dests).most_common(1)[0][0] if dests else None
        # Mini investigación: qué tiendas venden este producto, con cuántos
        # anuncios cada una y desde hace cuánto.
        por_tienda = {}
        for a in gads:
            d = a.get("destino")
            if not d or NO_TIENDA.search(d):
                continue
            dom = dominio_de(d)
            if not dom:
                continue
            e = por_tienda.setdefault(dom, {"dominio": dom, "anuncios": 0, "dias": None,
                                            "url": d, "anunciantes": set()})
            e["anuncios"] += 1
            if a.get("dias") is not None:
                e["dias"] = a["dias"] if e["dias"] is None else max(e["dias"], a["dias"])
            if a.get("adv"):
                e["anunciantes"].add(a["adv"])
        tiendas = sorted(por_tienda.values(), key=lambda x: -x["anuncios"])[:10]
        productos.append({
            "titulo": titulo,
            "destino": destino,
            "texto": rep.get("texto") or "",
            "anuncios": n_ads,
            "vendedores": n_vend,
            "dias_activo": dias_max,
            "varias_versiones": any(a.get("versiones") for a in gads),
            "detalle": detalle,
            "creativos": list(dict.fromkeys(
                [a.get("img") for a in gads if a.get("img")]))[:6],
            "tiendas": [{k: (sorted(v)[:3] if k == "anunciantes" else v)
                         for k, v in ti.items()} for ti in tiendas],
            "_img_url": rep.get("img"),
            "img": None,
            "link": ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
                     f"&country={PAIS[0]}&q={kw_dom.replace(' ', '%20')}"
                     "&media_type=all&search_type=keyword_unordered"),
            "score": score,
        })

    # Un producto "ganador" tiene duplicados (≥2 anuncios) o corre hace mucho.
    productos = [p for p in productos if p["anuncios"] >= 2 or (p["dias_activo"] or 0) >= 120]
    # …y tiene que ser un PRODUCTO: el anuncio debe llevar a una tienda, no a una
    # app ni a un formulario. Sin tienda no hay nada que revender.
    productos = [p for p in productos if p.get("destino")]
    productos.sort(key=lambda p: -p["score"])
    productos = productos[:args.tope * 2]   # margen: después filtramos por precio

    # Precio al que se vende en la tienda del anunciante. Con eso y el costo del
    # mayorista sale el margen real de quien ya lo está vendiendo.
    print(f"Buscando precio de venta en {len(productos)} tiendas…")
    def poner_precio(p):
        ficha = ficha_tienda_producto(p["destino"])
        # Guarda: hay anunciantes que mandan a otro producto de su tienda (un
        # anuncio de proyector que linkea a un shampoo). Si el nombre del producto
        # en la tienda no tiene nada que ver con la categoría del anuncio, no se
        # usa ni su nombre ni sus fotos: estaríamos mostrando otra cosa.
        cat = set(_norm(p.get("titulo")).split())
        nom = set(_norm(ficha.get("nombre_tienda")).split())
        coincide = bool({w for w in cat if len(w) > 3} & {w for w in nom if len(w) > 3})
        if ficha.get("nombre_tienda") and coincide:
            p["nombre_en_tienda"] = ficha["nombre_tienda"]
            p["fotos_tienda"] = ficha.get("fotos_tienda") or []
        else:
            p["fotos_tienda"] = []
            p["destino_ajeno"] = bool(ficha.get("nombre_tienda"))
        precio, moneda = (ficha.get("precio_tienda"), "ARS") if ficha.get("precio_tienda") \
            else precio_landing(p["destino"])
        p["precio_venta"] = precio
        p["moneda_venta"] = moneda
        try:
            p["tienda"] = re.sub(r"^www\.", "", urllib.parse.urlparse(p["destino"]).netloc)
        except Exception:
            p["tienda"] = None
        return p
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        productos = list(ex.map(poner_precio, productos))

    # Perfil de cada tienda (catálogo y tipo). Una consulta por dominio.
    doms = {ti["dominio"] for p in productos for ti in p.get("tiendas", [])}
    print(f"Investigando {len(doms)} tiendas…")
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(perfil_tienda, doms))
    for p in productos:
        for ti in p.get("tiendas", []):
            ti.update(perfil_tienda(ti["dominio"]))
    con_precio = [p for p in productos if p.get("precio_venta")]
    if args.solo_con_precio and con_precio:
        productos = con_precio
    productos = productos[:args.tope]

    # Recién ahora bajamos la imagen grande (redimensionada) de los productos finales.
    print(f"Bajando imágenes de {len(productos)} productos…")
    def poner_img(p):
        url = p.pop("_img_url", None)
        p["img"] = imagen_display(url) if url else None
        return p
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        productos = list(ex.map(poner_img, productos))

    salida = {
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "fuente": f"Biblioteca de anuncios de Meta — {PAIS[0]} (productos por imagen)",
        "categorias": kws,
        "productos": productos,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(salida, ensure_ascii=False), encoding="utf-8")
    print(f"\nOK: {len(productos)} productos ganadores → {args.out}")
    dup = sum(1 for p in productos if p["vendedores"] >= 2)
    print(f"   ({dup} vendidos por 2+ cuentas distintas)")


if __name__ == "__main__":
    main()
