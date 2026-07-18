#!/usr/bin/env python3
"""
Cifra una página HTML con clave (CIET) — protección real para GitHub Pages.

GitHub Pages es estático: no hay servidor donde validar una contraseña. La
solución es cifrar el HTML del lado del cliente: el archivo publicado sólo
contiene texto cifrado (AES-256-GCM) y un cargador que pide la clave, deriva
la llave con PBKDF2-SHA256 y descifra en el navegador con Web Crypto. Sin la
clave correcta no se ve NADA del contenido, ni siquiera en el código fuente.

El HTML original (source) NO se sube al repo: queda sólo en tu máquina. Lo que
se commitea es la salida cifrada.

Uso:
    python3 scripts/cifrar_pagina.py entrada.src.html salida.html
    # pide la clave por teclado (no queda en el historial del shell)

    CLAVE_CIET='miclave' python3 scripts/cifrar_pagina.py entrada.src.html salida.html
    # o via variable de entorno, para reusarla sin tipearla
"""
import base64
import getpass
import os
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERACIONES = 200_000  # PBKDF2: mismo número debe usar el navegador al descifrar


def cifrar(texto: str, clave: str) -> dict:
    salt = os.urandom(16)
    iv = os.urandom(12)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=ITERACIONES)
    llave = kdf.derive(clave.encode("utf-8"))
    # AES-GCM: el ciphertext ya incluye el tag de 16 bytes al final,
    # que es exactamente lo que espera Web Crypto (crypto.subtle.decrypt).
    ct = AESGCM(llave).encrypt(iv, texto.encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"salt": b64(salt), "iv": b64(iv), "ct": b64(ct), "iter": ITERACIONES}


PLANTILLA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CIET — Acceso privado</title>
<meta name="robots" content="noindex, nofollow">
<link rel="icon" type="image/svg+xml" href="/ciet/favicon.svg">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{ --paper:#faf8f4; --card:#fff; --line:#e6e1d6; --ink:#1a1a1a; --muted:#6a655c; --accent:#14395f; --accent-soft:#eaf0f6; --serif:"Fraunces",Georgia,serif; --sans:"Inter",-apple-system,sans-serif; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--paper); color:var(--ink); font-family:var(--sans); min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; }}
  .box {{ width:100%; max-width:380px; background:var(--card); border:1px solid var(--line); border-radius:16px; padding:38px 34px; text-align:center; }}
  .lock {{ width:46px; height:46px; margin:0 auto 18px; border-radius:12px; background:var(--accent-soft); display:flex; align-items:center; justify-content:center; }}
  h1 {{ font-family:var(--serif); font-weight:600; font-size:1.32rem; letter-spacing:-.01em; }}
  p.sub {{ color:var(--muted); font-size:.9rem; margin:8px 0 24px; line-height:1.5; }}
  input {{ width:100%; padding:13px 15px; font-size:1rem; font-family:var(--sans); border:1px solid var(--line); border-radius:10px; background:var(--paper); color:var(--ink); text-align:center; letter-spacing:.05em; }}
  input:focus {{ outline:none; border-color:var(--accent); }}
  button {{ width:100%; margin-top:12px; padding:13px; font-size:.98rem; font-weight:600; font-family:var(--sans); color:#fff; background:var(--accent); border:none; border-radius:10px; cursor:pointer; transition:opacity .18s; }}
  button:hover {{ opacity:.9; }}
  button:disabled {{ opacity:.5; cursor:wait; }}
  .err {{ color:#a33; font-size:.85rem; margin-top:14px; min-height:1.1em; }}
</style>
</head>
<body>
  <div class="box">
    <div class="lock">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#14395f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
    </div>
    <h1>Acceso privado</h1>
    <p class="sub">Esta sección del CIET requiere clave.</p>
    <form id="f">
      <input id="c" type="password" placeholder="Clave de acceso" autocomplete="off" autofocus>
      <button id="b" type="submit">Entrar</button>
    </form>
    <div class="err" id="e"></div>
  </div>
<script>
const P = {payload};
const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
async function descifrar(clave) {{
  const salt = b64(P.salt), iv = b64(P.iv), data = b64(P.ct);
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(clave), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey(
    {{ name:"PBKDF2", salt, iterations:P.iter, hash:"SHA-256" }},
    base, {{ name:"AES-GCM", length:256 }}, false, ["decrypt"]);
  const buf = await crypto.subtle.decrypt({{ name:"AES-GCM", iv }}, key, data);
  return new TextDecoder().decode(buf);
}}
const f = document.getElementById("f"), b = document.getElementById("b"), e = document.getElementById("e");
f.addEventListener("submit", async ev => {{
  ev.preventDefault();
  e.textContent = ""; b.disabled = true; b.textContent = "Abriendo…";
  try {{
    const html = await descifrar(document.getElementById("c").value);
    sessionStorage.setItem("ciet_ok", "1");
    document.open(); document.write(html); document.close();
  }} catch (_) {{
    e.textContent = "Clave incorrecta.";
    b.disabled = false; b.textContent = "Entrar";
    document.getElementById("c").select();
  }}
}});
</script>
</body>
</html>
"""


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    src, out = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as fh:
        texto = fh.read()

    clave = os.environ.get("CLAVE_CIET") or getpass.getpass("Clave de acceso: ")
    if not clave:
        print("Clave vacía, aborto.")
        sys.exit(1)
    if not os.environ.get("CLAVE_CIET"):
        if getpass.getpass("Repetir clave: ") != clave:
            print("Las claves no coinciden, aborto.")
            sys.exit(1)

    datos = cifrar(texto, clave)
    import json
    html = PLANTILLA.format(payload=json.dumps(datos))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"OK: {out} ({len(html):,} bytes cifrados desde {src})")


if __name__ == "__main__":
    main()
