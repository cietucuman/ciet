# CIET · Sección E-commerce (privada)

Sección oculta de investigación de **productos ganadores**, con acceso por clave.
Entrada discreta: un `·` casi invisible al final del footer del `index.html`, que
lleva a `ecommerce.html`. Cada fuente de datos es un **motor independiente** (mismo
patrón que buscador / yerba / canasta: un script arma un JSON, se publica en una
rama de datos, y la página lo lee del lado del navegador).

## Cómo funciona el acceso por clave

GitHub Pages es estático (no hay servidor donde validar contraseñas), así que la
página se **cifra del lado del cliente** (AES-256-GCM + PBKDF2, vía Web Crypto):

- `ecommerce.src.html` — la página **en claro**. Vive SÓLO en tu máquina
  (`.gitignore` la excluye). Es el archivo que editás.
- `ecommerce.html` — la versión **cifrada** que se sube y sirve. Sin la clave no se
  ve nada, ni en "ver código fuente".
- `scripts/cifrar_pagina.py` — convierte una en la otra.

Sin la clave correcta, `crypto.subtle.decrypt` falla y no se descifra nada. La
clave no está en ningún lado del repo.

### Generar / regenerar la página cifrada

```bash
python3 scripts/cifrar_pagina.py ecommerce.src.html ecommerce.html
# pide tu clave dos veces. Elegí una y no la pierdas (no hay "recuperar clave").
```

Hacé esto **cada vez que edites `ecommerce.src.html`**, y commiteá el
`ecommerce.html` resultante. (El `ecommerce.html` no existe hasta que lo generes:
lo borré a propósito porque el que probé usaba una clave de descarte.)

## Motor 1 — Biblioteca de anuncios AR  ✅ funcionando

Detecta qué anunciantes están corriendo **muchos anuncios activos** de un producto
en Argentina (la señal de "duplicados / está escalando").

- Meta bloquea todo lo que no sea un navegador real (curl da 403), por eso el motor
  usa **Playwright** y **corre en tu Mac**, no en GitHub Actions (IP de datacenter
  bloqueada). Lee la respuesta `dynamic_filter_options` que la propia página pide:
  no fabrica requests firmadas, así que es más durable que un scraper de `requests`.
- Productos a rastrear: `data/ecommerce/keywords.txt` (una por línea, editá libre).

Instalación (una sola vez):
```bash
pip3 install --user playwright && python3 -m playwright install chromium
```

Correr + publicar (a diario, cuando quieras):
```bash
bash scripts/publicar_ganadores_ar.sh
# o sólo generar el JSON sin publicar, viendo la ventana del navegador:
python3 scripts/scrape_biblioteca_ar.py --keywords data/ecommerce/keywords.txt -o /tmp/ganadores_ar.json
```

El JSON se publica en la rama **`ecommerce-datos`** (huérfana, sólo datos), de donde
la página lo lee. Para automatizar: agregá `bash scripts/publicar_ganadores_ar.sh`
a la misma automatización local que ya usás para el buscador / las ofertas.

**Puntaje** = anuncios activos × boost por multi-producto × factor de antigüedad.
La antigüedad sale de leer las tarjetas: para los anunciantes topeados (que no
salen en el feed general) el motor entra a la página de cada uno y mide su anuncio
activo más viejo (pasada dirigida, limitada por `--enriquecer`). También incluye
"long-runners": anunciantes con 1 solo anuncio pero corriendo hace ≥120 días
(`--dias-min`), que suelen ser productos probados.

**Límites conocidos:** el conteo por anunciante viene topeado en 10 (no distingue
10 de 50). La antigüedad es la del anuncio *activo* más viejo: los que rotan
creativos seguido muestran pocos días aunque lleven meses en el rubro (los
anuncios viejos ya vencidos no aparecen en la vista de "activos").

## Motor 2 — Biblioteca · España (API oficial)  ⏳ pendiente de token

La API oficial de Meta sólo devuelve anuncios comerciales que tocaron la UE. Usando
**España** (mismo idioma, suele adelantar tendencias) da alcance real y demografía,
y corre gratis en GitHub Actions. Falta: verificar identidad en Meta para el token
(tarda unos días; conviene arrancarlo ya en developers.facebook.com).

## Motor 3 — MercadoLibre (tendencias AR)  ⏳ pendiente de app

MercadoLibre **cerró su API pública** (todo da 403): ahora hace falta una app de
desarrollador y token OAuth. Da qué están **buscando** los compradores en AR.

### Pasos para crear la app de ML (hacelo cuando puedas, ~10 min)

1. Entrá a https://developers.mercadolibre.com.ar/ y logueate con tu cuenta de ML.
2. "Crear aplicación". Nombre y descripción cualquiera (uso propio).
3. Redirect URI: poné `https://cietucuman.github.io/ciet/` (no se usa para leer,
   pero el form lo pide).
4. Permisos: alcanza con lectura.
5. Guardá el **App ID** y el **Secret Key** y pasámelos (o cargálos como secrets del
   repo). Con eso armo el motor de tendencias.

## Roadmap

- ~~v2 del motor AR: antigüedad~~ ✅ hecho (pasada dirigida + long-runners).
- **Cruce de fuentes:** producto que escala en España + se anuncia en AR + lo
  buscan en MercadoLibre = ventana de oportunidad. Es el objetivo final.
- Más fuentes (TikTok Creative Center, Google Trends AR) como motores extra.

## Checklist de despliegue (primera vez)

1. `python3 scripts/cifrar_pagina.py ecommerce.src.html ecommerce.html` (tu clave).
2. `bash scripts/publicar_ganadores_ar.sh` (crea la rama `ecommerce-datos` y sube datos).
3. Commit + push de `ecommerce.html`, `index.html`, `scripts/*`, `data/ecommerce/keywords.txt`.
   (`ecommerce.src.html` NO se sube: está en `.gitignore`.)
4. Entrá a `https://cietucuman.github.io/ciet/ecommerce.html`, probá tu clave.
