# CIET — Centro de Investigación Económica de Tucumán

Sitio educativo y de divulgación económica con dos líneas de trabajo: (1) modelos canónicos de la literatura presentados de forma interactiva, cada uno con simulador + sección formal con ecuaciones y referencias bibliográficas; (2) a futuro, análisis aplicado de la economía tucumana (coyuntura, estructura productiva, finanzas provinciales). La portada del sitio es [`index.html`](index.html); cada modelo vive en su carpeta y es una página HTML autocontenida (sin dependencias ni build), lista para GitHub Pages / Netlify.

## Modelos

| Modelo | Estado | Carpeta |
|---|---|---|
| Solow (crecimiento) | ✅ Listo | [`solow/`](solow/) |
| IS-LM | Idea | — |
| Oferta y demanda / excedentes | Idea | — |
| Telaraña (cobweb) | Idea | — |
| Ventaja comparativa (Ricardo) | Idea | — |

## Cómo ver un modelo localmente

Abrir el `index.html` directamente en el navegador, o servir la carpeta:

```bash
npx serve modelos-interactivos/solow
```

## Convenciones

- Un archivo `index.html` por modelo: HTML + CSS + JS vanilla, canvas para gráficos.
- UI en español, tema oscuro, mobile-friendly (los links se comparten en redes).
- Cada modelo muestra: parámetros con sliders, gráficos que reaccionan en vivo, resultados clave calculados, y 2-3 "intuiciones clave" al pie.
