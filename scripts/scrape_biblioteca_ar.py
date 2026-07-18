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
import concurrent.futures
import datetime
import io
import json
import sys
import time
import unicodedata
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
       "&country=AR&q={q}&media_type=all&search_type=keyword_unordered")

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
    const im=c.querySelector('img[src*="t39.35426"]');
    out.push({id,adv:adv?adv.trim():null,dias,versiones:/varias versiones/.test(t),texto,img:im?im.src:null});
  }
  return out;
}"""


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
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    except Exception:
        return None


def scrape_keyword(page, kw, scrolls, espera_ms=8000):
    try:
        page.goto(URL.format(q=kw.replace(" ", "%20")), wait_until="domcontentloaded", timeout=45000)
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
    ap.add_argument("--scrolls", type=int, default=12, help="scrolls por producto (más = más anuncios)")
    ap.add_argument("--umbral", type=int, default=8, help="bits de tolerancia para 'misma imagen' (0-64)")
    ap.add_argument("--tope", type=int, default=60, help="máximo de productos en la salida")
    ap.add_argument("--pausa", type=float, default=4.0)
    args = ap.parse_args()

    if args.keywords:
        kws = [l.strip() for l in Path(args.keywords).read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith("#")]
    else:
        kws = KEYWORDS_DEFAULT

    print(f"Buscando anuncios de {len(kws)} categorías (Argentina)…")
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

    # Bajar miniaturas y calcular la huella de cada anuncio (en paralelo).
    print(f"Comparando imágenes de {len(ads)} anuncios…")
    def procesar(a):
        data = bajar(a["img"])
        a["_bytes"] = data
        a["_hash"] = dhash(data) if data else None
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
        img_b64 = None
        if rep.get("_bytes"):
            import base64
            img_b64 = "data:image/jpeg;base64," + base64.b64encode(rep["_bytes"]).decode("ascii")
        productos.append({
            "texto": rep.get("texto") or "",
            "anuncios": n_ads,
            "vendedores": n_vend,
            "vendedores_lista": vendedores[:8],
            "dias_activo": dias_max,
            "varias_versiones": any(a.get("versiones") for a in gads),
            "keywords": sorted({a["keyword"] for a in gads}),
            "img": img_b64,
            "link": ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
                     f"&country=AR&q={(gads[0]['keyword']).replace(' ', '%20')}"
                     "&media_type=all&search_type=keyword_unordered"),
            "score": score,
        })

    # Un producto "ganador" tiene duplicados (≥2 anuncios) o corre hace mucho.
    productos = [p for p in productos if p["anuncios"] >= 2 or (p["dias_activo"] or 0) >= 120]
    productos.sort(key=lambda p: -p["score"])
    productos = productos[:args.tope]

    salida = {
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "fuente": "Biblioteca de anuncios de Meta — Argentina (productos por imagen)",
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
