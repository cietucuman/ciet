#!/usr/bin/env python3
"""
Motor de productos ganadores — Biblioteca de anuncios de Meta, Argentina (CIET).

Meta bloquea todo lo que no sea un navegador real (curl/requests dan 403), así
que este motor maneja un Chromium de verdad con Playwright y CORRE EN TU MÁQUINA
(no en GitHub Actions: los runners tienen IP de datacenter y Meta los bloquea).

Para cada palabra clave (un producto) abre la biblioteca filtrada a Argentina y
anuncios ACTIVOS, e intercepta la respuesta GraphQL que la propia página pide
(`dynamic_filter_options`). Esa respuesta trae, por anunciante, cuántos anuncios
activos tiene para ese producto — el conteo está topeado en 10, y justamente los
que llegan al tope son los que están escalando (la señal de "duplicados").

No fabricamos ninguna request firmada: sólo leemos lo que el navegador ya trae.
Eso lo hace mucho más durable que los scrapers de requests, que se rompen seguido.

Uso:
    python3 scripts/scrape_biblioteca_ar.py -o /tmp/ganadores_ar.json
    python3 scripts/scrape_biblioteca_ar.py --keywords data/ecommerce/keywords.txt
    python3 scripts/scrape_biblioteca_ar.py --headless        # sin ventana (más detectable)
    python3 scripts/scrape_biblioteca_ar.py --min 3 --tope 60 # filtros de salida

Requisitos (una sola vez):
    pip3 install --user playwright && python3 -m playwright install chromium
"""
import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Falta Playwright. Instalá:\n"
             "  pip3 install --user playwright && python3 -m playwright install chromium")

URL = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
       "&country=AR&q={q}&media_type=all&search_type=keyword_unordered")

# JS que corre dentro de la página: extrae una fila por tarjeta de anuncio
# (anunciante + antigüedad + si tiene varias versiones). Ancla en el texto
# "Identificador de la biblioteca" (estable) y sube al contenedor más chico que
# tenga UNA sola tarjeta; el anunciante es la línea justo antes de "Publicidad".
JS_TARJETAS = r"""() => {
  const meses={ene:0,feb:1,mar:2,abr:3,may:4,jun:5,jul:6,ago:7,sep:8,oct:9,nov:10,dic:11};
  const cnt=s=>(s.match(/Identificador de la biblioteca/g)||[]).length;
  const marks=[...document.querySelectorAll('div')].filter(el=>/Identificador de la biblioteca/.test(el.textContent)&&cnt(el.textContent)===1&&el.querySelectorAll('div').length<3);
  const cards=[];const seen=new Set();
  for(const m of marks){let c=m;while(c.parentElement&&cnt(c.parentElement.textContent)===1){c=c.parentElement;}
    if(seen.has(c))continue;seen.add(c);
    const t=c.innerText||'';const id=(t.match(/biblioteca:\s*([0-9]+)/)||[])[1];
    const adv=(t.match(/([^\n]+)\n\s*Publicidad/)||[])[1];
    const dm=t.match(/desde el (\d{1,2}) (\w{3})\.?\s*(\d{4})/);let dias=null;
    if(dm&&meses[dm[2].toLowerCase()]!=null){const dt=new Date(+dm[3],meses[dm[2].toLowerCase()],+dm[1]);dias=Math.round((Date.now()-dt)/864e5);}
    cards.push({id,adv:adv?adv.trim():null,dias,versiones:/varias versiones/.test(t)});}
  return cards;
}"""


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())

# Lista semilla de productos típicos de e-commerce/dropshipping en AR.
# Editá data/ecommerce/keywords.txt (una por línea) para tu propia lista.
KEYWORDS_DEFAULT = [
    "freidora de aire", "proyector", "mini proyector", "cepillo alisador",
    "masajeador", "lampara de luna", "organizador", "aspiradora inalambrica",
    "reloj inteligente", "auriculares inalambricos", "camara seguridad wifi",
    "depiladora laser", "purificador de aire", "humidificador",
]


def extraer_pages(texto: str):
    """Saca la lista `pages` (anunciante + conteo) del cuerpo GraphQL.

    El endpoint a veces responde varios objetos JSON (uno por línea, streaming),
    así que probamos línea por línea y nos quedamos con la que traiga los datos.
    """
    candidatos = [texto] + texto.splitlines()
    for trozo in candidatos:
        trozo = trozo.strip()
        if "dynamic_filter_options" not in trozo:
            continue
        try:
            obj = json.loads(trozo)
        except Exception:
            continue
        pages = (obj.get("data", {}).get("ad_library_main", {})
                    .get("dynamic_filter_options", {}).get("pages"))
        if pages:
            return pages
    return None


def scrape_keyword(page, kw: str, scrolls: int = 5, espera_ms: int = 8000):
    capt = {"pages": None}

    def on_resp(resp):
        if capt["pages"] is not None or "/api/graphql/" not in resp.url:
            return
        try:
            txt = resp.text()
        except Exception:
            return
        if "dynamic_filter_options" in txt:
            p = extraer_pages(txt)
            if p:
                capt["pages"] = p

    page.on("response", on_resp)
    try:
        page.goto(URL.format(q=kw.replace(" ", "%20")),
                  wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"    ! error navegando '{kw}': {e}", file=sys.stderr)

    t0 = time.time()
    while capt["pages"] is None and (time.time() - t0) * 1000 < espera_ms:
        page.wait_for_timeout(300)

    total = None
    try:
        m = re.search(r'~?\s*([\d.]+)\s+resultados', page.inner_text("body"))
        if m:
            total = int(m.group(1).replace(".", ""))
    except Exception:
        pass

    # Scroll para cargar más tarjetas y así medir la antigüedad de más anunciantes.
    for _ in range(scrolls):
        try:
            page.mouse.wheel(0, 4200)
            page.wait_for_timeout(1100)
        except Exception:
            break
    try:
        tarjetas = page.evaluate(JS_TARJETAS) or []
    except Exception:
        tarjetas = []

    try:
        page.remove_listener("response", on_resp)
    except Exception:
        pass
    return capt["pages"] or [], total, tarjetas


def antiguedad_pagina(page, pid: str, scrolls: int = 2):
    """Antigüedad del anuncio activo más viejo de un anunciante (en días).

    Los anunciantes topeados casi nunca aparecen en el feed general, así que para
    medir su antigüedad hay que entrar a SU página de anuncios y leer ahí.
    """
    url = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
           f"&country=AR&view_all_page_id={pid}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        for _ in range(scrolls):
            page.mouse.wheel(0, 4200)
            page.wait_for_timeout(1000)
        tarjetas = page.evaluate(JS_TARJETAS) or []
    except Exception:
        return None
    dias = [t["dias"] for t in tarjetas if t.get("dias") is not None]
    return max(dias) if dias else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="/tmp/ganadores_ar.json")
    ap.add_argument("--keywords", help="archivo con una palabra clave por línea")
    ap.add_argument("--headless", action="store_true",
                    help="sin ventana (más rápido pero más detectable por Meta)")
    ap.add_argument("--min", type=int, default=2,
                    help="mínimo de anuncios activos para incluir (default 2)")
    ap.add_argument("--dias-min", type=int, default=120, dest="dias_min",
                    help="incluir aunque tenga 1 anuncio si lleva ≥ estos días (default 120)")
    ap.add_argument("--enriquecer", type=int, default=25,
                    help="cuántos anunciantes ganadores medir en antigüedad (default 25)")
    ap.add_argument("--tope", type=int, default=80,
                    help="máximo de filas en la salida (default 80)")
    ap.add_argument("--pausa", type=float, default=5.0,
                    help="segundos entre keywords (cortesía anti-bloqueo)")
    args = ap.parse_args()

    if args.keywords:
        kws = [l.strip() for l in Path(args.keywords).read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith("#")]
    else:
        kws = KEYWORDS_DEFAULT
    if not kws:
        sys.exit("Sin palabras clave.")

    print(f"Buscando {len(kws)} productos en la biblioteca (Argentina)…")
    items = []
    por_pagina = {}   # page_id -> set(keywords) para el boost cruzado
    resumen_kw = []

    perfil = Path.home() / ".ciet_playwright"   # sesión persistente = menos captchas
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(perfil),
            headless=args.headless,
            locale="es-AR",
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        for i, kw in enumerate(kws, 1):
            pages, total, tarjetas = scrape_keyword(page, kw)
            # Antigüedad por anunciante (de las tarjetas visibles): el anuncio más
            # viejo que le vimos = hace cuánto viene empujando ese producto.
            edad = {}   # norm(anunciante) -> {"dias": max, "versiones": bool}
            for t in tarjetas:
                clave = _norm(t.get("adv"))
                if not clave:
                    continue
                e = edad.setdefault(clave, {"dias": None, "versiones": False})
                if t.get("dias") is not None:
                    e["dias"] = t["dias"] if e["dias"] is None else max(e["dias"], t["dias"])
                if t.get("versiones"):
                    e["versiones"] = True
            n_tope = sum(1 for p in pages if p.get("count", 0) >= 10)
            print(f"  [{i}/{len(kws)}] {kw!r}: {len(pages)} anunciantes"
                  f"{f', ~{total} anuncios' if total else ''}"
                  f"{f', {n_tope} en el tope' if n_tope else ''}"
                  f", {len(tarjetas)} tarjetas con fecha")
            resumen_kw.append({"kw": kw, "anunciantes": len(pages), "total_aprox": total})
            for p in pages:
                pid = str(p.get("key"))
                cnt = int(p.get("count", 0))
                e = edad.get(_norm(p.get("display_name")), {})
                # Se incluye si corre varios anuncios, o si corre uno pero hace mucho.
                largo = e.get("dias") is not None and e["dias"] >= args.dias_min
                if cnt < args.min and not largo:
                    continue
                items.append({
                    "keyword": kw,
                    "anunciante": (p.get("display_name") or "").strip(),
                    "page_id": pid,
                    "anuncios_activos": cnt,
                    "tope": cnt >= 10,
                    "dias_activo": e.get("dias"),
                    "varias_versiones": e.get("versiones", False),
                })
                por_pagina.setdefault(pid, set()).add(kw)
            if i < len(kws):
                time.sleep(args.pausa)

        # Pasada dirigida: los ganadores topeados no aparecen en el feed, así que
        # entramos a la página de cada uno a medir su antigüedad. Se prioriza por
        # cantidad de anuncios y se limita a --enriquecer para acotar el tiempo.
        faltan = {}
        for it in items:
            if it.get("dias_activo") is None and it["tope"]:
                prev = faltan.get(it["page_id"], (0, ""))
                if it["anuncios_activos"] >= prev[0]:
                    faltan[it["page_id"]] = (it["anuncios_activos"], it["anunciante"])
        objetivo = sorted(faltan.items(), key=lambda x: -x[1][0])[:args.enriquecer]
        if objetivo:
            print(f"· Midiendo antigüedad de {len(objetivo)} anunciantes ganadores…")
        edad_pagina = {}
        for j, (pid, (cnt, nom)) in enumerate(objetivo, 1):
            d = antiguedad_pagina(page, pid)
            edad_pagina[pid] = d
            print(f"    ({j}/{len(objetivo)}) {nom[:30]}: {str(d) + 'd' if d else 's/d'}")
            time.sleep(1.5)
        for it in items:
            if it.get("dias_activo") is None:
                it["dias_activo"] = edad_pagina.get(it["page_id"])
        ctx.close()

    # Puntaje = anuncios activos × boost por multi-producto × factor de antigüedad.
    # El anunciante que corre muchos anuncios, de varios productos y hace meses es
    # el ganador más sólido.
    for it in items:
        n_kw = len(por_pagina.get(it["page_id"], {it["keyword"]}))
        boost = 1 + 0.5 * (n_kw - 1)
        dias = it.get("dias_activo")
        f_edad = 1 + min(dias, 365) / 365 if dias else 1
        it["score"] = round(it["anuncios_activos"] * boost * f_edad, 1)
        it["multi_producto"] = n_kw
        it["link"] = ("https://www.facebook.com/ads/library/?active_status=active"
                      f"&ad_type=all&country=AR&view_all_page_id={it['page_id']}")

    items.sort(key=lambda x: -x["score"])
    items = items[:args.tope]

    salida = {
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "fuente": "Biblioteca de anuncios de Meta — Argentina (anuncios activos)",
        "keywords": resumen_kw,
        "items": items,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nOK: {len(items)} productos ganadores → {args.out}")


if __name__ == "__main__":
    main()
