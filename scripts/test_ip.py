#!/usr/bin/env python3
"""Test de conectividad para decidir si el buscador puede correr en un SERVIDOR.

Corre una mini-captura (pocos términos) usando el MISMO código que el scraper real y
reporta, por cadena, si las APIs de los supermercados responden desde la IP donde corre
(en GitHub Actions = IP de datacenter) o si bloquean (403 típico anti-bot). Si todas las
cadenas devuelven productos con precio, se puede migrar a ese servidor.

Uso: python3 scripts/test_ip.py   (no toca nada, solo consulta)
"""
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_buscador as fb  # noqa: E402

CADENAS = ["Carrefour", "Vea", "Jumbo", "Comodín", "ChangoMás", "Tuchanguito"]


def ip_publica():
    try:
        return urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode()
    except Exception as e:
        return f"(no se pudo obtener: {e})"


def main():
    print(f"IP pública del runner: {ip_publica()}\n", flush=True)

    # mini-captura: pocos términos, tope bajo → rápido, pero ejercita TODO el camino real
    # (cookie de segmento, región, checkout con seller de sucursal, promos, agrupación).
    out = str(HERE.parent / "test_ip_out.json")
    sys.argv = ["fetch_buscador.py", "--terminos",
                "coca cola,leche la serenisima,yerba,aceite", "--tope", "20", "-o", out]
    try:
        fb.main()
    except SystemExit:
        pass

    data = json.loads(Path(out).read_text(encoding="utf-8"))
    cuenta = Counter()
    ejemplo = {}
    for p in data.get("productos", []):
        for cad, o in p["pr"].items():
            cuenta[cad] += 1
            ejemplo.setdefault(cad, (p["n"][:32], o[0]))

    print("\n== Productos capturados por cadena ==")
    bloqueadas = []
    for cad in CADENAS:
        n = cuenta.get(cad, 0)
        if n == 0:
            bloqueadas.append(cad)
        ej = ejemplo.get(cad)
        estado = "OK" if n else "❌ SIN DATOS (¿bloqueada por IP?)"
        muestra = f"  ej: {ej[0]} ${ej[1]}" if ej else ""
        print(f"  {cad:12} {n:4} productos   {estado}{muestra}")

    print("\n== RESULTADO ==")
    if not bloqueadas:
        print("✅ TODAS las cadenas responden desde esta IP → SE PUEDE migrar a este servidor.")
        sys.exit(0)
    print(f"⚠️  Sin datos de: {', '.join(bloqueadas)} → probablemente bloquean IP de datacenter.")
    print("   (Si es GitHub Actions, probar otro proveedor —Oracle Free— o una compu en casa.)")
    sys.exit(1)


if __name__ == "__main__":
    main()
