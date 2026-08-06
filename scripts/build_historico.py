#!/usr/bin/env python3
"""
Histórico y crecimiento de productos ganadores (CIET).

La señal más valiosa no es cuántos anuncios tiene hoy un producto, sino si esa
cantidad está SUBIENDO. Un producto que pasó de 20 a 80 anuncios en una semana
está escalando ahora; uno estancado en 80 hace meses ya está saturado.

Eso no se puede reconstruir hacia atrás: hay que ir guardando una foto por día.
Este script toma el ranking del día, lo archiva y calcula la variación contra la
foto de hace 7 días (o la más cercana disponible).

Uso:
    python3 scripts/build_historico.py --productos /tmp/productos_ar.json \\
        --dir ~/.ciet-ecommerce/historico --pais ar
"""
import argparse
import datetime
import json
import unicodedata
from pathlib import Path


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def resumen(productos):
    """Anuncios y tiendas por categoría de producto (la unidad comparable)."""
    agg = {}
    for p in productos:
        k = norm(p.get("titulo"))
        if not k:
            continue
        a = agg.setdefault(k, {"titulo": p.get("titulo"), "anuncios": 0, "tiendas": set()})
        a["anuncios"] += p.get("anuncios") or 0
        for ti in p.get("tiendas") or []:
            if ti.get("dominio"):
                a["tiendas"].add(ti["dominio"])
    return {k: {"titulo": v["titulo"], "anuncios": v["anuncios"], "tiendas": len(v["tiendas"])}
            for k, v in agg.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--productos", required=True)
    ap.add_argument("--dir", required=True, help="carpeta del histórico (una foto por día)")
    ap.add_argument("--pais", default="ar")
    ap.add_argument("--dias", type=int, default=7, help="contra cuántos días atrás comparar")
    args = ap.parse_args()

    d = json.loads(Path(args.productos).read_text(encoding="utf-8"))
    hoy = datetime.date.today()
    carpeta = Path(args.dir).expanduser() / args.pais
    carpeta.mkdir(parents=True, exist_ok=True)

    actual = resumen(d.get("productos", []))
    (carpeta / f"{hoy.isoformat()}.json").write_text(
        json.dumps({"fecha": hoy.isoformat(), "resumen": actual}, ensure_ascii=False),
        encoding="utf-8")

    # Foto de referencia: la más cercana a `dias` atrás que exista.
    fotos = sorted(f for f in carpeta.glob("*.json") if f.stem != hoy.isoformat())
    ref, ref_fecha = None, None
    objetivo = hoy - datetime.timedelta(days=args.dias)
    for f in fotos:
        try:
            fecha = datetime.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if fecha <= objetivo or ref is None:
            ref = json.loads(f.read_text(encoding="utf-8")).get("resumen", {})
            ref_fecha = fecha
            if fecha <= objetivo:
                break

    creci = []
    if ref:
        dias_reales = (hoy - ref_fecha).days
        for k, v in actual.items():
            antes = ref.get(k)
            if not antes:
                creci.append({"producto": v["titulo"], "anuncios": v["anuncios"],
                              "antes": 0, "var": None, "nuevo": True,
                              "tiendas": v["tiendas"]})
                continue
            a0 = antes.get("anuncios") or 0
            var = ((v["anuncios"] - a0) / a0 * 100) if a0 else None
            creci.append({"producto": v["titulo"], "anuncios": v["anuncios"],
                          "antes": a0, "var": round(var, 1) if var is not None else None,
                          "nuevo": False, "tiendas": v["tiendas"],
                          "tiendas_antes": antes.get("tiendas")})
        creci.sort(key=lambda c: -(c["var"] if c["var"] is not None else -999))
        print(f"Comparado contra {ref_fecha} ({dias_reales} días):")
        for c in creci[:8]:
            if c["nuevo"]:
                print(f"   NUEVO   {c['producto'][:24]:24} {c['anuncios']:>4} anuncios")
            elif c["var"] is not None:
                print(f"   {c['var']:+7.1f}% {c['producto'][:24]:24} "
                      f"{c['antes']:>4} → {c['anuncios']:<4} anuncios")
    else:
        print("Primera foto del histórico: todavía no hay con qué comparar.")

    salida = carpeta.parent / f"crecimiento_{args.pais}.json"
    salida.write_text(json.dumps({
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "pais": args.pais,
        "fotos": len(fotos) + 1,
        "referencia": ref_fecha.isoformat() if ref_fecha else None,
        "crecimiento": creci,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"\nOK: {len(actual)} categorías archivadas → {salida}")


if __name__ == "__main__":
    main()
