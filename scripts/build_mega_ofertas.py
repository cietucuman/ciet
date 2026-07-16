#!/usr/bin/env python3
"""
Mega ofertas (CIET) — detecta las ofertas excepcionales del buscador de precios.

Regla (calibrada empíricamente con los snapshots del 12–14 jul 2026, para que
salgan ~10 por día sin imponer un tope):
    1. El producto está en >= 3 cadenas.
    2. Su precio más barato bajó >= 15% respecto de la MEDIANA de los últimos
       7 días en esa misma cadena (no vs la última actualización: así la oferta
       queda visible ~3 días hasta que la mediana la absorbe, y rota sola).
    3. Es >= 25% más barato que la 2.ª cadena más barata del momento.
    4. Excelencia: baja% × brecha% >= 1500 (≈ ambos descuentos promedian 39%).
       Perilla de calibración: 1300 → ~20/día; 1800–2000 → ~5–7/día.

Mantiene su propia historia móvil de precios (historia.json) porque la rama
`datos` se publica con --amend + push -f y sus commits NO son un archivo
histórico confiable.

Uso:
    python3 build_mega_ofertas.py [--buscador ~/.ciet-datos/buscador.json]
                                  [--dir ~/.ciet-ofertas]
    python3 build_mega_ofertas.py --seed snapshot.json --dir ~/.ciet-ofertas
        (--seed: sólo incorpora los precios del snapshot a la historia, con la
         fecha interna del archivo; no genera ofertas. Para precargar días.)

Sólo LEE el buscador.json; nunca escribe ni hace git en ~/.ciet-datos.
"""
import argparse
import json
import re
import statistics
import unicodedata
from datetime import date, timedelta
from pathlib import Path

MIN_CADENAS = 3
MIN_BAJA = 15.0      # % mínimo de baja vs la mediana histórica (misma cadena)
MIN_BRECHA = 25.0    # % mínimo por debajo de la 2.ª cadena más barata
MIN_SCORE = 1500.0   # baja% × brecha%
VENTANA_DIAS = 7     # días previos que forman la referencia (mediana)
RETENER_DIAS = 10    # historia que se conserva antes de podar


def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def clave(p):
    return _norm(p["n"]) + "|" + _norm(p["m"])


def cargar_historia(path):
    if path.exists():
        return json.loads(path.read_text())
    return {}


def actualizar_historia(historia, productos, fecha):
    """Registra los precios de `fecha` (si hay varias corridas en el día, la
    última pisa a las anteriores) y poda lo más viejo que RETENER_DIAS."""
    for p in productos:
        k = clave(p)
        for cad, v in p["pr"].items():
            precio = v[0]
            if precio and precio > 0:
                historia.setdefault(k, {}).setdefault(cad, {})[fecha] = precio
    limite = (date.fromisoformat(fecha) - timedelta(days=RETENER_DIAS)).isoformat()
    for k in list(historia):
        for cad in list(historia[k]):
            historia[k][cad] = {f: v for f, v in historia[k][cad].items() if f >= limite}
            if not historia[k][cad]:
                del historia[k][cad]
        if not historia[k]:
            del historia[k]


def referencia(historia, k, cad, hoy):
    """Mediana del precio en `cad` durante la ventana previa a `hoy`."""
    serie = historia.get(k, {}).get(cad, {})
    desde = (date.fromisoformat(hoy) - timedelta(days=VENTANA_DIAS)).isoformat()
    previos = [v for f, v in serie.items() if desde <= f < hoy]
    return statistics.median(previos) if previos else None


def detectar(productos, historia, fecha):
    ofertas = []
    for p in productos:
        if len(p["pr"]) < MIN_CADENAS:
            continue
        precios = sorted((v[0], cad, v[1]) for cad, v in p["pr"].items())
        p1, cad1, link1 = precios[0]
        p2 = precios[1][0]
        if p1 <= 0 or p2 <= 0:
            continue
        brecha = (1 - p1 / p2) * 100
        if brecha < MIN_BRECHA:
            continue
        ref = referencia(historia, clave(p), cad1, fecha)
        if not ref:
            continue
        baja = (1 - p1 / ref) * 100
        if baja < MIN_BAJA or baja * brecha < MIN_SCORE:
            continue
        ofertas.append({
            "n": p["n"], "m": p["m"], "i": p.get("i"),
            "cadena": cad1, "precio": p1, "link": link1,
            "referencia": round(ref, 2), "precio_2da": p2,
            "baja_pct": round(baja, 1), "brecha_pct": round(brecha, 1),
            "score": round(baja * brecha),
            # todas las cadenas (precio, link), para que la tarjeta muestre la
            # comparación completa igual que un resultado del buscador
            "pr": {cad: [v[0], v[1]] for cad, v in p["pr"].items()},
        })
    ofertas.sort(key=lambda o: -o["score"])
    return ofertas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buscador", default="~/.ciet-datos/buscador.json")
    ap.add_argument("--dir", default="~/.ciet-ofertas")
    ap.add_argument("--seed", help="snapshot a incorporar a la historia (sin generar ofertas)")
    args = ap.parse_args()

    destino = Path(args.dir).expanduser()
    destino.mkdir(parents=True, exist_ok=True)
    hist_path = destino / "historia.json"
    historia = cargar_historia(hist_path)

    fuente = Path(args.seed or args.buscador).expanduser()
    d = json.loads(fuente.read_text())
    fecha = d["fecha"]

    if args.seed:
        actualizar_historia(historia, d["productos"], fecha)
        hist_path.write_text(json.dumps(historia, ensure_ascii=False))
        print(f"historia += {fecha} ({len(d['productos'])} productos)")
        return

    # detectar ANTES de registrar hoy: la referencia son los días previos
    ofertas = detectar(d["productos"], historia, fecha)
    actualizar_historia(historia, d["productos"], fecha)
    hist_path.write_text(json.dumps(historia, ensure_ascii=False))

    dias = sorted({f for k in historia for c in historia[k] for f in historia[k][c]})
    out = {
        "fecha": fecha,
        "actualizado": d.get("actualizado"),
        "regla": {"min_cadenas": MIN_CADENAS, "min_baja_pct": MIN_BAJA,
                  "min_brecha_pct": MIN_BRECHA, "min_score": MIN_SCORE,
                  "ventana_dias": VENTANA_DIAS},
        "dias_historia": dias,
        "total": len(ofertas),
        "ofertas": ofertas,
    }
    (destino / "ofertas.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{fecha}: {len(ofertas)} mega ofertas (historia: {dias[0]}..{dias[-1]})")
    for o in ofertas:
        print(f"  {o['n'][:52]:52s} {o['cadena']:11s} ${o['referencia']:>9,.0f} -> "
              f"${o['precio']:>9,.0f}  2da ${o['precio_2da']:>9,.0f}  "
              f"baja {o['baja_pct']:.0f}% brecha {o['brecha_pct']:.0f}%")


if __name__ == "__main__":
    main()
