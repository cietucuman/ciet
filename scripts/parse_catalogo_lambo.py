#!/usr/bin/env python3
"""
Catálogo de LamboTech (CIET) — importador directo, catálogos en PDF.

LamboTech no tiene catálogo web: publica PDFs por rubro en Drive. Este script los
baja, extrae los productos y arma un catálogo buscable, para poder cruzarlo con
los productos ganadores igual que a los mayoristas con tienda online.

Los PDFs vienen tabulados así (una fila por producto):
    FOTO | CODIGO | 品名 | DESCRIPCION | UNID/B | PRECIO/U
o sea: código, nombre en chino, descripción en español, unidades por bulto y
precio unitario en dólares. Ese precio unitario es el dato importante: es costo
de importador directo, sin intermediarios.

Uso:
    python3 scripts/parse_catalogo_lambo.py                 # usa el caché local
    python3 scripts/parse_catalogo_lambo.py --refrescar     # vuelve a bajar los PDFs
    python3 scripts/parse_catalogo_lambo.py -o /tmp/catalogo_lambo.json
"""
import argparse
import datetime
import json
import re
import sys
import urllib.request
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    sys.exit("Falta pypdf: pip3 install --user pypdf")

CACHE = Path.home() / ".ciet-lambo"        # PDFs bajados (pesan; no van al repo)

# Catálogos publicados por LamboTech (Drive). id -> nombre de archivo.
CATALOGOS = [
    ("1Ohb4aU6HI_iMOlkDrVEOM6yDDLo_5LAF", "2026 Electronico 22.07.pdf", "Electrónica"),
    ("1U6iTGkDgsyBHgOvNgsDwszo-hQwT66dO", "2026 Ferreteria 22.07.pdf", "Ferretería"),
    ("17uHEVfAl8CBz00yQMmsCbcGnLhczvrja", "2026 Surtido 22.07.pdf", "Surtido"),
    ("1hw6k2gEBvxRW2aaIeS4QnaqXqeU8vvGw", "2026 Art. Belleza 22.07.pdf", "Belleza"),
    ("1Nyaq6TBT5asEUmCldS1DWccAwq7caAxC", "2025 Cosmetico 30.06.pdf", "Cosmética"),
    ("1WYbpch6zXn5eJv7-E0SziMZzKw-0O_ux", "Cortapelos 09.07.pdf", "Cortapelos"),
    ("1GIXuuqHgbtLm3iaOvPZfMNNmvaqCgQaS", "Depiladora 09.07.pdf", "Depiladoras"),
    ("1xxjZRfAmfFJgnRnnU96i_gNzD2vYq4LS", "Luces 14.04.pdf", "Luces"),
    ("1UiWcrRc7g3Pm-thLbhl99RW670BdNU3-", "Perfume 29.07.pdf", "Perfumes"),
    ("1w3KfXsK9qvA2emXCb3zsrch6ozYG4GKD", "Juguetes nuevos 28.07.pdf", "Juguetes"),
    ("1TZX78jICvdbpwwK0SWs7nHaGYtg-f_dR", "Anteojos -5% 16.06.pdf", "Anteojos"),
    ("1gI_shlELPMZ6Fh2TefjRLMujSKdeLH0g", "Invierno 09.07.pdf", "Invierno"),
    ("1E_3p7uq-zrzwTnTz0ZfmvhXaWR6_X9Bl", "D08 30.07.pdf", "Varios D08"),
    ("1IGQCvmDEFs802QSXFsoaOyt_WEyvmGh6", "D13 23.07.pdf", "Varios D13"),
]

# Código de producto: A-W243035, C-54508, D-09102, C-ZZ202506-2…
RE_CODIGO = re.compile(r"^([A-Z]{1,3}-[A-Z0-9]{3,}(?:-[0-9]+)?)\s*(.*)$")
# Cierre de fila: unidades por bulto y precio unitario.
RE_CIERRE = re.compile(r"(\d{1,5})\s+(\d{1,5}(?:[.,]\d{1,2})?)\s*$")


def bajar_pdf(fid, destino):
    url = f"https://drive.google.com/uc?export=download&id={fid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(destino, "wb") as f:
        f.write(r.read())
    return destino.stat().st_size


def limpiar(desc):
    """Deja la descripción en español: saca chino, marcas repetidas y ruido."""
    desc = re.sub(r"[一-鿿　-〿]+", " ", desc)   # caracteres chinos
    desc = re.sub(r"(?i)\blambo\s*tech\b", " ", desc)
    desc = re.sub(r"\s+", " ", desc).strip(" -–—·,")
    return desc


def productos_de(pdf, rubro):
    """Saca los productos de un catálogo."""
    try:
        lector = PdfReader(str(pdf))
    except Exception as e:
        print(f"    ! no pude leer {pdf.name}: {e}", file=sys.stderr)
        return []

    out = []
    for pagina in lector.pages:
        try:
            texto = pagina.extract_text() or ""
        except Exception:
            continue
        # Cada producto arranca con su código; se corta ahí y se lee el bloque.
        lineas = texto.split("\n")
        bloques, actual = [], None
        for ln in lineas:
            m = RE_CODIGO.match(ln.strip())
            if m:
                if actual:
                    bloques.append(actual)
                actual = [m.group(1), m.group(2)]
            elif actual is not None:
                actual.append(ln)
        if actual:
            bloques.append(actual)

        for b in bloques:
            codigo, resto = b[0], " ".join(b[1:])
            m = RE_CIERRE.search(resto.strip())
            if not m:
                continue
            try:
                unidades = int(m.group(1))
                precio = float(m.group(2).replace(",", "."))
            except ValueError:
                continue
            if precio <= 0 or precio > 5000:
                continue
            desc = limpiar(resto[:m.start()])
            if len(desc) < 6:
                continue
            out.append({
                "codigo": codigo, "descripcion": desc[:120],
                "unidades_bulto": unidades, "precio_usd": precio, "rubro": rubro,
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="/tmp/catalogo_lambo.json")
    ap.add_argument("--refrescar", action="store_true",
                    help="vuelve a bajar los PDFs aunque estén en caché")
    args = ap.parse_args()

    CACHE.mkdir(exist_ok=True)
    todos, vistos = [], set()
    for fid, nombre, rubro in CATALOGOS:
        destino = CACHE / nombre
        if args.refrescar or not destino.exists():
            try:
                mb = bajar_pdf(fid, destino) / 1e6
                print(f"· bajado {nombre} ({mb:.0f} MB)")
            except Exception as e:
                print(f"  ! no pude bajar {nombre}: {e}", file=sys.stderr)
                continue
        prods = productos_de(destino, rubro)
        nuevos = 0
        for p in prods:
            if p["codigo"] in vistos:
                continue
            vistos.add(p["codigo"])
            todos.append(p)
            nuevos += 1
        print(f"  {nombre}: {nuevos} productos")

    todos.sort(key=lambda p: -p["precio_usd"])
    salida = {
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "proveedor": "LamboTech (importador directo)",
        "fuente": "Catálogos PDF publicados por el proveedor",
        "productos": todos,
    }
    Path(args.out).write_text(json.dumps(salida, ensure_ascii=False), encoding="utf-8")
    print(f"\nOK: {len(todos)} productos → {args.out}")
    if todos:
        caros = [p for p in todos if p["precio_usd"] >= 30]
        print(f"   {len(caros)} productos de USD 30+ (candidatos a ticket alto)")


if __name__ == "__main__":
    main()
