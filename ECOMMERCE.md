# CIET · Sección E-commerce (privada)

Sección oculta para investigar **productos ganadores**, con acceso por clave.
Entrada discreta: un `·` casi invisible al final del footer del `index.html`, que
lleva a `ecommerce.html`. Cada fuente de datos es un **motor independiente** (mismo
patrón que buscador / yerba / canasta: un script arma un JSON, se publica en una
rama de datos, y la página lo lee del lado del navegador).

## Acceso (candado light)

`ecommerce.html` es un archivo normal con un candado simple en JavaScript: pide una
clave y compara su SHA-256 con el valor guardado en `CLAVE_HASH`. No es cifrado
fuerte (alguien decidido puede ver el HTML), pero el contenido son datos públicos
agregados, así que alcanza para mantener afuera a los curiosos.

- **Clave actual:** `ciet2026`
- **Para cambiarla:** generá el hash de la nueva y reemplazá `CLAVE_HASH` en
  `ecommerce.html`:
  ```bash
  printf '%s' 'MI-NUEVA-CLAVE' | shasum -a 256
  ```
  Copiás el resultado (64 caracteres) en la línea `const CLAVE_HASH = "…"`.

## Motor 1 — Productos ganadores (Biblioteca AR)  ✅ funcionando

Rankea **productos**, no anunciantes. Agrupa los anuncios por su **imagen** (huella
perceptual): el mismo producto, aunque lo vendan cuentas distintas, cae en un solo
grupo. Un producto ganador tiene muchos anuncios duplicados, de varios vendedores, y
hace tiempo al aire.

- Meta bloquea todo lo que no sea navegador real (curl da 403), así que usa
  **Playwright** y **corre en tu Mac**, no en GitHub Actions.
- Baja las miniaturas (60×60) para comparar imágenes; guarda la del producto como
  imagen incrustada en el JSON, así la página la muestra sin depender de Meta.
- Categorías a rastrear: `data/ecommerce/keywords.txt` (editá libre).

Instalación (una sola vez):
```bash
pip3 install --user playwright pillow && python3 -m playwright install chromium
```

Correr + publicar:
```bash
bash scripts/publicar_ganadores_ar.sh
```
Publica `productos_ar.json` en la rama **`ecommerce-datos`**, de donde lee la página.
Se puede automatizar sumándolo a la automatización local del buscador / las ofertas.

Parámetros útiles: `--scrolls 15` (más anuncios por producto), `--umbral 6` (más
estricto al considerar "misma imagen"), `--headless` (sin ventana).

**Límites conocidos:** la antigüedad es la del anuncio *activo* más viejo (subestima
a los que rotan creativos); el agrupado por imagen puede juntar de más si dos
productos comparten foto, o separar si un vendedor editó la imagen.

## Motor 2 — Biblioteca · España (API oficial)  ⏳ pendiente de token

La API oficial de Meta sólo devuelve anuncios comerciales que tocaron la UE. Usando
España (mismo idioma, suele adelantar tendencias) da alcance real y corre gratis en
GitHub Actions. Falta: verificar identidad en Meta para el token (tarda unos días;
arrancalo en developers.facebook.com).

## Motor 3 — MercadoLibre (tendencias AR)  ⏳ pendiente de app

MercadoLibre cerró su API pública (todo da 403): ahora hace falta app de
desarrollador y token OAuth. Da qué buscan los compradores en AR.

### Pasos para crear la app de ML (~10 min)

1. https://developers.mercadolibre.com.ar/ → logueate con tu cuenta de ML.
2. "Crear aplicación". Nombre/descripción cualquiera (uso propio).
3. Redirect URI: `https://cietucuman.github.io/ciet/` (el form lo pide).
4. Permisos: alcanza con lectura.
5. Guardá **App ID** y **Secret Key** y pasálos para armar el motor.

## Roadmap

- **Cruce de fuentes:** producto que escala en España + se anuncia en AR + lo buscan
  en MercadoLibre = ventana de oportunidad. Objetivo final.
- Más fuentes (TikTok Creative Center, Google Trends AR) como motores extra.

## Actualizar los datos (día a día)

Con correr esto alcanza (hace scrape + publica):
```bash
bash scripts/publicar_ganadores_ar.sh
```
La página se actualiza sola al ratito (lee el JSON de la rama `ecommerce-datos`).
