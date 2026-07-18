#!/usr/bin/env bash
# Productos ganadores — Biblioteca de anuncios AR (CIET).
# Corre en TU MÁQUINA (Meta bloquea las IP de datacenter de GitHub Actions).
#
#   1. Scrapea la biblioteca con Playwright        -> /tmp/ganadores_ar.json
#   2. Publica ese JSON en la rama `ecommerce-datos`, que es de donde lee la página.
#
# La rama `ecommerce-datos` es huérfana (sólo datos, historial liviano), igual que
# `yerba-datos` o `canasta-datos`. La primera vez la crea sola.
set -e
cd "$(dirname "$0")/.."

OUT=/tmp/ganadores_ar.json
echo "· Scrapeando biblioteca de anuncios (Argentina)…"
python3 scripts/scrape_biblioteca_ar.py --keywords data/ecommerce/keywords.txt -o "$OUT" "$@"

echo "· Publicando en la rama ecommerce-datos…"
git fetch origin ecommerce-datos 2>/dev/null || true
if git rev-parse --verify origin/ecommerce-datos >/dev/null 2>&1; then
  git worktree add -B ecommerce-datos /tmp/ec-datos origin/ecommerce-datos
else
  git worktree add --detach /tmp/ec-datos
  ( cd /tmp/ec-datos && git checkout --orphan ecommerce-datos && git rm -rf . >/dev/null 2>&1 || true )
fi
cp "$OUT" /tmp/ec-datos/ganadores_ar.json
( cd /tmp/ec-datos
  git add ganadores_ar.json
  if git diff --cached --quiet HEAD 2>/dev/null; then
    echo "  sin cambios"
  else
    git commit -m "Ganadores AR: $(date +%F)" >/dev/null
    git push -f origin ecommerce-datos
    echo "  publicado"
  fi )
git worktree remove /tmp/ec-datos --force 2>/dev/null || true
echo "OK · ganadores AR actualizados."
