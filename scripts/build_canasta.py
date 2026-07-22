#!/usr/bin/env python3
"""
Canasta Básica Alimentaria de Tucumán (CIET), valorizada con precios online.

QUÉ ES
  La CBA que el INDEC define para la región Noroeste (Metodología Nº 22, cuadro
  7.2: composición en gramos/ml por mes por adulto equivalente), valorizada todos
  los días con los precios de los supermercados online de Tucumán que releva el
  buscador del CIET.

METODOLOGÍA (espejo de la del INDEC hasta donde los datos lo permiten)
  · Composición: CBA regional Noroeste, tal cual el cuadro oficial (57 productos;
    acá se agrupan en los rubros valorizables de abajo). No inventamos canasta.
  · Valorización: el INDEC valoriza con los PRECIOS MEDIOS relevados por el IPC.
    Acá, para cada rubro y cada cadena se toma el PROMEDIO SIMPLE de $/kg (o $/l)
    de todos los artículos que cumplen la especificación del rubro.
  · Especificación por rubro: qué artículos representan al producto de la canasta
    (ej. "arroz blanco simple": excluye integrales, risottos y snacks de arroz).
    Igual que las "variedades" del IPC.
  · Precio del rubro (general) = promedio de los promedios por cadena (cada
    cadena pesa igual, como un relevamiento con un local por cadena).
  · Valor de la canasta = Σ cantidad_mensual × precio del rubro.
  · Familia tipo = hogar de 4 (varón de 35, mujer de 31, niña de 8 y niño de 6)
    = 1,00 + 0,77 + 0,68 + 0,64 = 3,09 adultos equivalentes (tabla oficial).
  · Outliers: dentro de cada rubro se descartan artículos cuyo $/kg queda a más
    de 2,5× (o menos de 1/2,5×) del precio del rubro en la CORRIDA ANTERIOR
    (ancla histórica; el primer día, la mediana del día). La mediana sola falla
    cuando los artículos mal colados son mayoría (papa, 22/7/2026); el precio de
    ayer no. Un salto de ±50% contra el ancla deja AVISO en el log.
  · Si una cadena no tiene artículos de un rubro, su total se completa con el
    promedio de las demás cadenas (se informa cuántos rubros se imputaron).

Uso:
    python3 build_canasta.py [--buscador data/buscador.json] [-o data/canasta]
    python3 build_canasta.py --audit [rubro]     # inspección de qué matchea
"""
import argparse
import json
import re
import statistics
import sys
import unicodedata
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_buscador import _norm  # noqa: E402

# Hogar "familia tipo" del INDEC (hogar 2 de sus informes): varón de 35 (1,00) +
# mujer de 31 (0,77) + niña de 8 (0,68) + niño de 6 (0,64) = 3,09 adultos
# equivalentes (Tabla 1 de equivalencias, Dirección de IPC del INDEC).
FAMILIA_AE = 3.09

# Un artículo se descarta si su $/kg está a más de este factor de la mediana
# del rubro (error de gramaje o producto fuera de especificación).
OUTLIER_FACTOR = 2.5

# Exclusión global para frutas, verduras y carnes frescas: cualquier producto
# elaborado, bebida o artículo de limpieza/perfumería que nombra la fruta o
# verdura como sabor/aroma ("shampoo de banana", "bizcochuelo de naranja").
EXC_FRESCO = (r"shampoo|jabon|acondicionador|desodorante|limpiador|lavandina|"
              r"insecticida|latex|pintura|lava ?coche|talco|perfume|bebida|"
              r"isotonic|powerade|gatorade|full sport|baggio|ades|cepita|nectar|"
              r"jugo|gaseosa|\bagua\b|licor|snack|papas fritas|pringles|chips|"
              r"barra|bite|galletita|oblea|bizcochuelo|budin|magdalena|torta|"
              r"caramelo|chicle|gomita|yogur|postre|flan|pulpa|desecad|"
              r"deshidratad|mermelada|dulce|jalea|compota|almibar|en mitades|"
              r"esencia|aroma|saborizad|sabor|\bte\b|cereal|tostada|medallon|"
              r"milanesa|hamburguesa|sopa|\bmix\b|tarta|pure|semilla|alimento|"
              r"mayonesa|aderezo|salsa|vinagre|\bmani\b|chocolate|helado|"
              r"\bpan\b|fideo|ñoqui|noqui|miel|sidra|vino|cerveza|caldo|"
              r"condimento|granola|smoothie|licuado|batidor|kinder|ravioles|"
              r"sorrentino|cappelletti|premezcla|falafel|tallarin|empanada|gelatina|"
              r"lava (coche|auto)s?|silisur|siliconado|jarro|\bmug\b|taza|ceramica|"
              r"\bkrea\b|tic tac|pastilla|detox|lustramuebles|entonador|aerosol|"
              r"vela\b|velas\b|sahumerio|difusor|fragancia|ambiente")

# ---------------------------------------------------------------------------
# COMPOSICIÓN CBA NOROESTE (INDEC, Metodología Nº 22, cuadro 7.2)
# g/ml por mes por adulto equivalente. Cada rubro lleva su especificación:
#   inc: regex que debe aparecer en el nombre normalizado
#   exc: regex que lo descarta (productos que no son la variedad de la canasta)
#   unidad: "kg" o "l" (sólo para mostrar; todo se calcula en $/g o $/ml)
#   por_unidad: peso en g de la unidad, para artículos que se venden por unidad
#               (huevos "x 12 un"). Si no, el gramaje se lee del nombre.
# Los productos del cuadro que en NOA valen "-" (batata, paleta, mortadela,
# salchichón, salame, margarina, dulce de batata) no se incluyen.
# ---------------------------------------------------------------------------
RUBROS = [
    # --- Panificados y cereales ---
    dict(id="pan", nombre="Pan francés", grupo="Panificados y cereales", g=7350, unidad="kg",
         espec="Pan tipo francés (flauta, felipe, criollo), por kilo o fraccionado. Se excluyen lactal, integral, de salvado, árabe, congelados y aptos celíacos.",
         inc=r"pan (frances|flauta|felipe|criollo)|(frances|flauta|felipe|criollo).*\bpan\b",
         exc=r"lactal|salvado|hamburgues|pancho|arabe|pita|rallado|congelad|schar|sin tacc|integral|molde|budin|centeno|masa madre|batata"),
    dict(id="gall_dulces", nombre="Galletitas dulces", grupo="Panificados y cereales", g=90, unidad="kg",
         espec="Galletitas dulces simples o surtidas (vainilla, coco, miel, leche). Se excluyen rellenas, bañadas, obleas, alfajores y versiones sin azúcar o light.",
         inc=r"galletita.*(dulce|vainilla|surtida|leche|coco|miel|limon)|\b(vocacion|surtido bagley)\b",
         exc=r"rellen|oblea|chocolate|bañad|glasead|alfajor|arroz|avena|salvado|agua|crackers|sin azucar|light"),
    dict(id="gall_agua", nombre="Galletitas de agua", grupo="Panificados y cereales", g=360, unidad="kg",
         espec="Galletitas de agua tipo cracker (Criollitas, Traviata y similares). Se excluyen integrales, con salvado, con semillas y saborizadas.",
         inc=r"galletita.*\bagua\b|crackers?\b|\bcriollitas\b|\btraviata\b",
         exc=r"dulce|rellen|arroz|salvado|integral|sesamo|semilla|queso|sabor"),
    dict(id="harina_trigo", nombre="Harina de trigo", grupo="Panificados y cereales", g=2190, unidad="kg",
         espec="Harina de trigo común 000 o 0000. Se excluyen leudante, integral, premezclas y otras harinas.",
         inc=r"harina.*(trigo|000|0000)|harina comun",
         exc=r"leudante|integral|maiz|arroz|garbanzo|almendra|centeno|preparad|semola|con salvado"),
    dict(id="harina_maiz", nombre="Harina de maíz", grupo="Panificados y cereales", g=210, unidad="kg",
         espec="Harina de maíz / polenta común. Se excluyen saborizadas y listas con queso.",
         inc=r"harina de maiz|polenta|\bsemola de maiz\b",
         exc=r"con queso|lista|saborizad|snack"),
    dict(id="arroz", nombre="Arroz blanco", grupo="Panificados y cereales", g=1050, unidad="kg",
         espec="Arroz blanco simple (largo fino, doble carolina). Se excluyen integral, yamaní, parboil, exóticos (basmati, carnaroli), preparados y snacks, galletitas o cereales de arroz.",
         inc=r"\barroz\b",
         exc=r"integral|yamani|parboil|risotto|preparad|harina|galleta|fideo|leche|bebida|tostad|cereal|sopa|saborizador|sazon|palito|crisp|crowie|snuks|snack|inflad|con queso|carnaroli|salvaje|negro|rojo|con vegetales|alimento|listo|valenciana|basmati|sushi|japones|akitakomachi|medallon|veggie|barra|crocante|vinagre|aceite|postre|budin|oblea|tutuca"),
    dict(id="fideos", nombre="Fideos secos", grupo="Panificados y cereales", g=1050, unidad="kg",
         espec="Fideos secos de trigo (guiseros, spaghetti, mostachol, tallarín, al huevo). Se excluyen frescos, rellenos, instantáneos, integrales y proteicos.",
         inc=r"\bfideo",
         exc=r"arroz|instantane|ramen|fresco|relleno|salsa|sopa|integral|sin tacc|proteic|espinaca|morron"),
    # --- Carnes ---
    dict(id="asado", nombre="Asado", grupo="Carnes", g=1050, unidad="kg",
         espec="Asado vacuno (tira, tapa, plancha), por kilo o envasado al vacío. Se excluyen cordero y cerdo, y todo producto 'sabor asado'.",
         inc=r"\basado\b|tira de asado",
         exc=r"sal |sal de|condimento|carbon|sabor|salsa|para asado|queso|banana|plato|snack|aderezo|arroz|mani|pollo asado|cordero|cerdo|papas|lays|provoleta"),
    dict(id="carnaza", nombre="Carnaza común", grupo="Carnes", g=630, unidad="kg",
         espec="Carnaza común vacuna.",
         inc=r"carnaza",
         exc=r"perro|gato|mascota"),
    dict(id="hueso", nombre="Hueso con carne (osobuco, caracú)", grupo="Carnes", g=1050, unidad="kg",
         espec="Cortes vacunos con hueso para puchero: osobuco, caracú, hueso con carne.",
         inc=r"osobuco|ossobuco|caracu|hueso con carne|puchero",
         exc=r"perro|gato|mascota|caldo|sopa"),
    dict(id="picada", nombre="Carne picada", grupo="Carnes", g=480, unidad="kg",
         espec="Carne vacuna picada (común, magra o especial), fresca o congelada. Se excluye la condimentada.",
         inc=r"(carne|vacuna).*(picada)|picada.*(vacuna|especial|comun|magra)",
         exc=r"perro|gato|condimentada|hamburguesa|cerdo|pollo"),
    dict(id="nalga", nombre="Nalga", grupo="Carnes", g=1260, unidad="kg",
         espec="Nalga y tapa de nalga vacuna. Se excluyen las milanesas rebozadas.",
         inc=r"\bnalga\b",
         exc=r"milanesa de nalga congelada|rebozad"),
    dict(id="higado", nombre="Hígado", grupo="Carnes", g=270, unidad="kg",
         espec="Hígado vacuno fresco o congelado. Se excluyen los patés.",
         inc=r"\bhigado\b",
         exc=r"perro|gato|pate|paté"),
    dict(id="cerdo", nombre="Pechito de cerdo", grupo="Carnes", g=60, unidad="kg",
         espec="Pechito o costilla de cerdo. Se excluyen ahumados y condimentados.",
         inc=r"pechito.*cerdo|cerdo.*pechito|costilla.*cerdo|cerdo.*costilla",
         exc=r"ahumad|condimentad"),
    dict(id="pollo", nombre="Pollo entero", grupo="Carnes", g=1800, unidad="kg",
         espec="Pollo entero fresco o congelado, por kilo o por unidad con peso declarado. Se excluyen trozados, supremas y elaborados.",
         inc=r"pollo (entero|fresco)|pollo x kg|pollo por kg",
         exc=r"perro|gato|milanesa|nugget|salchicha|sopa|caldo|trozado|pata|suprema|relleno"),
    dict(id="pescado", nombre="Pescado (filet de merluza)", grupo="Carnes", g=120, unidad="kg",
         espec="Filet de merluza fresco o congelado sin rebozar. Se excluyen milanesas, medallones, bastones y demás elaborados.",
         inc=r"merluza",
         exc=r"perro|gato|rebozad|milanesa|croqueta|medallon|formita|apanad|barrita|bastoncito|romana|al natural|lata|nugget|finguer|finger|empanad|paty|burguer|hamburguesa|formas|supremita|cuadradito|espinaca|queso"),
    # --- Lácteos y huevos ---
    dict(id="aceite", nombre="Aceite de girasol", grupo="Almacén", g=1050, unidad="l",
         espec="Aceite de girasol puro, en botella. Se excluyen oliva, mezcla, maíz, alto oleico y aerosoles.",
         inc=r"aceite.*girasol",
         exc=r"oliva|mezcla|spray|maiz|alto oleico"),
    dict(id="leche", nombre="Leche fluida entera", grupo="Lácteos y huevos", g=6900, unidad="l",
         espec="Leche entera fluida (sachet o larga vida). Se excluyen descremada, deslactosada, chocolatada, infantiles y bebidas vegetales.",
         inc=r"leche entera|entera.*\bleche\b|leche.*larga vida",
         exc=r"polvo|descremada|parcialmente|deslactosada|chocolatada|infantil|condensada|almendra|coco|soja|cabra|sin lactosa|vainilla|serenito|dulce"),
    dict(id="leche_polvo", nombre="Leche en polvo entera", grupo="Lácteos y huevos", g=390, unidad="kg",
         espec="Leche en polvo entera común. Se excluyen descremadas, fórmulas infantiles y fortificadas especiales.",
         inc=r"leche.*polvo",
         exc=r"descremada|infantil|\bnan\b|nutrilon|vital|crecimiento|nido|fortificada|chocolatada|bebe|nidia|etapa|advanced|sancor bebe|kid"),
    dict(id="q_crema", nombre="Queso crema", grupo="Lácteos y huevos", g=60, unidad="kg",
         espec="Queso crema o untable clásico. Se excluyen light, saborizados y en hebras.",
         inc=r"queso (crema|untable)|casancrem|finlandia|mendicrim",
         exc=r"light|descremado|saborizad|jamon|salame|cheddar|dulce|snack|cheetos|hebras|mozzarella|parmesano|chef|sachet grande"),
    dict(id="q_cuartirolo", nombre="Queso cuartirolo", grupo="Lácteos y huevos", g=120, unidad="kg",
         espec="Queso cuartirolo o cremoso, en horma, trozado o al vacío. Se excluyen light y fundidos.",
         inc=r"cuartirolo|queso.*cremoso|cremoso.*queso",
         exc=r"choclo|light|untable|fundido|limpiador|jabon|rallado"),
    dict(id="q_rallar", nombre="Queso de rallar", grupo="Lácteos y huevos", g=60, unidad="kg",
         espec="Queso de rallar (reggianito, sardo, parmesano), en horma o rallado. Se excluyen los 'alimentos a base de queso' y los aderezos.",
         inc=r"queso rallado|reggianito|sardo|parmesano|queso de rallar",
         exc=r"en hebras|light|alimento|procesado|untable|snack|aderezo|kuhne|cesar|caesar|salsa"),
    dict(id="manteca", nombre="Manteca", grupo="Lácteos y huevos", g=60, unidad="kg",
         espec="Manteca común en pan. Se excluyen light, untables especiales y productos 'sabor manteca'.",
         inc=r"\bmanteca\b",
         exc=r"mani|cacao|light|untable|vegetal|galletita|aerosol|aceite|sabor|maruca|mini|bizcoch"),
    dict(id="yogur", nombre="Yogur", grupo="Lácteos y huevos", g=510, unidad="kg",
         espec="Yogur entero o bebible común (firme, batido o sachet). Se excluyen griego, con agregados, proteicos e infantiles en envase chico.",
         inc=r"yogur",
         exc=r"griego|colchon|cereal|granola|proteic|kefir|helado|bebe|danonino|peppa|casancrem|licuado"),
    dict(id="ddl", nombre="Dulce de leche", grupo="Lácteos y huevos", g=70, unidad="kg",
         espec="Dulce de leche clásico o familiar. Se excluyen repostero y la repostería o golosinas que lo llevan.",
         inc=r"dulce de leche",
         exc=r"repostero|alfajor|helado|light|sin azucar|golosina|oblea|galletita|chocolate|postre|magdalena|flan|sobre|bizcochuelo|exquisita|budin|torta|bombon|cubanito|turron|barra|cereal|yogur|licor|cusenier|condensada"),
    dict(id="huevo", nombre="Huevos", grupo="Lácteos y huevos", g=390, unidad="kg", por_unidad=60,
         espec="Huevos de gallina blancos o de color, por media docena, docena o maple (convertidos a kilo a razón de 60 g por huevo).",
         inc=r"\bhuevos?\b",
         exc=r"fideo|pascua|chocolate|codorniz|kinder|revuelto|licor|sabor|sabo |alimento|perro|gato|shampoo|repelente|kg\b|incubadora|organizador|huevera|molde|contenedor|planchetta|atma|gadnic|sorpresa|smasher|papas"),
    # --- Frutas y verduras ---
    dict(id="manzana", nombre="Manzana", grupo="Frutas y verduras", g=997, unidad="kg",
         espec="Manzana fresca (roja, verde, Pink Lady), por kilo o bolsa.",
         inc=r"\bmanzanas?\b",
         exc=r"jugo|gaseosa|sabor|postre|pure|compota|vinagre|te\b|aroma|deshidratada|barra|cereal|chips"),
    dict(id="mandarina", nombre="Mandarina", grupo="Frutas y verduras", g=1230, unidad="kg",
         espec="Mandarina fresca, por kilo.",
         inc=r"mandarina",
         exc=r"jugo|gaseosa|sabor|aroma|chicle|caramelo|jabon|shampoo|te\b|esencia|desodorante"),
    dict(id="naranja", nombre="Naranja", grupo="Frutas y verduras", g=1710, unidad="kg",
         espec="Naranja fresca (de jugo, de ombligo, valencia), por kilo o bolsa.",
         inc=r"\bnaranjas?\b.*(kg|kilo|bolsa|malla)\b", sin_exc_global=True,
         exc=r"exprimid|licuad|yerba|detergente|\bpure\b|zummy|jugo (de|exprimido)|desodorante|esencia|jabon|shampoo|lustramuebles|entonador|jarro|pastilla"),
    dict(id="banana", nombre="Banana", grupo="Frutas y verduras", g=1410, unidad="kg",
         espec="Banana fresca, por kilo.",
         inc=r"\bbananas?\b",
         exc=r"jugo|sabor|postre|chips|budin|leche|yogur|gomita"),
    dict(id="pera", nombre="Pera", grupo="Frutas y verduras", g=137, unidad="kg",
         espec="Pera fresca (Williams, Packham y otras), por kilo.",
         inc=r"\bperas?\b",
         exc=r"jugo|sabor|lata|almibar|compota|aroma|opera|agua|sidra|batidor|licuado|yogur|gomita|te\b"),
    dict(id="papa", nombre="Papa", grupo="Frutas y verduras", g=6870, unidad="kg",
         espec="Papa fresca (blanca, negra), por kilo o bolsa. Se excluyen fritas, congeladas, puré instantáneo y snacks.",
         inc=r"\bpapas?\b",
         exc=r"frita|pure|ñoqui|noqui|congelad|baston|chips|snack|semilla|pay|croqueta|rustica|espanol|sabor|mc ?cain|noisette|golazo|air fryer|caritas|smile|rejilla|fargo|pancho|batata|fecula|ondeada|acanalada|crinkle|pehuamar|lays|krachitos|kesitas|tubo"),
    dict(id="acelga", nombre="Acelga", grupo="Frutas y verduras", g=360, unidad="kg",
         espec="Acelga lavada o congelada en bolsa (el atado fresco casi no se vende online).",
         inc=r"acelga",
         exc=r"semilla|tarta|pascualina|lucchetti|fideo|tallarin|ravioles|mix"),
    dict(id="cebolla", nombre="Cebolla", grupo="Frutas y verduras", g=1530, unidad="kg",
         espec="Cebolla fresca (blanca, morada), por kilo o bolsa. Se excluye la de verdeo.",
         inc=r"\bcebollas?\b",
         exc=r"verdeo|polvo|semilla|encurtida|snack|sabor|aros|deshidratada|morada premium"),
    dict(id="choclo", nombre="Choclo", grupo="Frutas y verduras", g=300, unidad="kg",
         espec="Choclo fresco en espiga. Se excluyen enlatados (en granos o cremosos) y congelados.",
         inc=r"\bchoclos?\b",
         exc=r"lata|granos|cremoso|\bcrem\b|crema|sopa|congelad|pochoclo|sabor|humita|inalpa|pupa|jardinera|arvejas|desgranado|en conserva|natural|green life|granja del sol|minivert|arcor|campagnola|noel|al vapor|cuisine|entero"),
    dict(id="lechuga", nombre="Lechuga", grupo="Frutas y verduras", g=420, unidad="kg",
         espec="Lechuga fresca por kilo (crespa, mantecosa, repollada). Se excluyen las hidropónicas vendidas por unidad sin peso.",
         inc=r"lechuga",
         exc=r"semilla|hidroponic"),
    dict(id="tomate", nombre="Tomate fresco (perita/redondo)", grupo="Frutas y verduras", g=2160, unidad="kg",
         espec="Tomate fresco perita o redondo, por kilo. Se excluyen enlatados, purés y triturados.",
         inc=r"tomate (perita|redondo|fresco)|tomate x|tomate por kg",
         exc=r"lata|pure|triturado|salsa|pelado|seco|deshidratado|cherry|envasado|conserva|extracto|jugo|arcor|cuisine|campagnola|inca\b|marolio|alco\b|molto|canale|sin tacc|dos hermanas|cubetead|noel|entero"),
    dict(id="zanahoria", nombre="Zanahoria", grupo="Frutas y verduras", g=840, unidad="kg",
         espec="Zanahoria fresca, por kilo. Se excluyen baby y enlatadas.",
         inc=r"zanahoria",
         exc=r"jugo|semilla|rallada|snack|torta|baby|lata|enteras carrefour"),
    dict(id="zapallo", nombre="Zapallo", grupo="Frutas y verduras", g=1050, unidad="kg",
         espec="Zapallo o calabaza (anco, criollo, coreano, inglés), por kilo.",
         inc=r"\bzapallos?\b|\banco\b|calabaza",
         exc=r"semilla|mermelada|almibar|sopa|snack|zapallito|pure listo"),
    # --- Almacén ---
    dict(id="tomate_env", nombre="Tomate envasado", grupo="Almacén", g=180, unidad="kg",
         espec="Tomate envasado: perita en lata, triturado o puré. Se excluyen salsas listas y condimentadas.",
         inc=r"(pure de tomate|tomate triturado|tomate perita.*(lata|envasad|conserva)|tomate pelado)",
         exc=r"salsa lista|con albahaca|condimentad|ketchup|deshidratado"),
    dict(id="arvejas", nombre="Arvejas", grupo="Almacén", g=120, unidad="kg",
         espec="Arvejas en lata o brick. Se excluyen secas partidas, congeladas y premezclas.",
         inc=r"arveja",
         exc=r"seca|partida|harina|congelad|con choclo|premezcla|falafel|quinoa"),
    dict(id="lentejas", nombre="Lentejas secas", grupo="Almacén", g=120, unidad="kg",
         espec="Lentejas secas en bolsa. Se excluyen en lata y elaborados.",
         inc=r"lenteja",
         exc=r"lata|conserva|hamburguesa|harina|guiso listo|snack"),
    dict(id="azucar", nombre="Azúcar", grupo="Almacén", g=1290, unidad="kg",
         espec="Azúcar común tipo A. Se excluyen mascabo, orgánica, impalpable y edulcorantes.",
         inc=r"\bazucar\b",
         exc=r"sin azucar|mascabo|rubia|organica|impalpable|flor|gaseosa|jugo|yogur|leche|mermelada|edulcorante|chocolate|galletita|light|negra|cafe|cacao|nesquik|cappuccino|caramel|alfajor|caña|terrasana|algodon|golosina|barra"),
    dict(id="mermelada", nombre="Mermelada", grupo="Almacén", g=70, unidad="kg",
         espec="Mermelada común de cualquier sabor. Se excluyen light o sin azúcar e importadas premium.",
         inc=r"mermelada",
         exc=r"sin azucar|light|diet|zapallo|organica|importad|dalfour"),
    dict(id="sal", nombre="Sal fina", grupo="Almacén", g=120, unidad="kg",
         espec="Sal fina de mesa. Se excluyen saborizadas, marinas y rosadas.",
         inc=r"sal fina",
         exc=r"ajo|apio|cebolla|marina|light|saborizada|rosada|himalaya"),
    dict(id="mayonesa", nombre="Mayonesa", grupo="Almacén", g=60, unidad="kg",
         espec="Mayonesa clásica. Se excluyen light, livianas y saborizadas.",
         inc=r"mayonesa",
         exc=r"light|liviana|vegana|sin huevo|picante|chimichurri|ahumada|con limon|doypack mini"),
    dict(id="vinagre", nombre="Vinagre", grupo="Almacén", g=60, unidad="l",
         espec="Vinagre de alcohol. Se excluyen aceto balsámico, de manzana y los encurtidos 'en vinagre'.",
         inc=r"vinagre",
         exc=r"manzana|balsamic|aceto|arroz|sidra|pepinillo|pickle|aceituna|encurtido|escabeche|alcaparra|cebollitas"),
    dict(id="caldo", nombre="Caldo concentrado", grupo="Almacén", g=30, unidad="kg", por_unidad=10,
         espec="Caldo concentrado en cubitos o sobres (convertidos a kilo a razón de 10 g por unidad).",
         inc=r"\bcaldos?\b",
         exc=r"deshidratada|sopa|reducido en sodio|r/sodio|organic"),
    # --- Bebidas e infusiones ---
    dict(id="gaseosa", nombre="Gaseosa base cola", grupo="Bebidas e infusiones", g=1500, unidad="l", min_g=1250,
         espec="Gaseosa base cola regular, en envase de 1,25 litro o más. Se excluyen zero/sin azúcar y los aperitivos con cola.",
         inc=r"(gaseosa.*cola|coca cola|pepsi|\bcola\b)",
         exc=r"sin azucar|zero|light|black|caramelo|chupetin|jarra|vaso|sabor limon|fernet|aperitivo|con cola|manaos.*(naranja|limon|pomelo|uva)"),
    dict(id="jugo", nombre="Jugo concentrado", grupo="Bebidas e infusiones", g=900, unidad="l",
         espec="Jugo concentrado líquido para diluir. Se excluyen en polvo y listos para tomar.",
         inc=r"jugo.*(concentrado|para diluir)|(concentrado|para diluir).*jugo",
         exc=r"polvo|sin azucar|light|diet"),
    dict(id="soda", nombre="Soda", grupo="Bebidas e infusiones", g=2400, unidad="l", min_g=1500,
         espec="Soda en sifón o botella de 1,5 litro o más. Se excluyen las saborizadas.",
         inc=r"\bsoda\b",
         exc=r"sodastream|gasificador|cilindro|botella termica|sabor|limonada|menos sodio"),
    dict(id="cerveza", nombre="Cerveza", grupo="Bebidas e infusiones", g=300, unidad="l", min_g=730,
         espec="Cerveza rubia común en envase de un litro aproximado. Se excluyen artesanales, importadas, especialidades y latas chicas.",
         inc=r"cerveza",
         exc=r"sin alcohol|ipa|apa|honey|stout|porter|roja|negra|artesanal|importada|corona|stella|patagonia|heineken|barril"),
    dict(id="vino", nombre="Vino común de mesa", grupo="Bebidas e infusiones", g=510, unidad="l", min_g=700,
         espec="Vino común de mesa (tetra brik o botella de marcas de mesa: Toro, Termidor, Resero). Se excluyen varietales finos, reservas y espumantes.",
         inc=r"vino.*(mesa|tetra|brik|brick|carton)|\b(toro|termidor|resero|uvita|vasco viejo|crespi|talacasto|valderrobles|michel torino tetra)\b",
         exc=r"espumante|champagne|sidra|reserva|roble|premium|fino|organico|blend"),
    dict(id="cafe", nombre="Café molido", grupo="Bebidas e infusiones", g=30, unidad="kg",
         espec="Café molido, torrado o tostado. Se excluyen instantáneo, en cápsulas y en grano.",
         inc=r"cafe.*molido|molido.*cafe|cafe torrado",
         exc=r"capsula|instantane|soluble|dolca|nescafe|cappuccino|latte|yogur|en grano|descafeinado"),
    dict(id="yerba", nombre="Yerba mate", grupo="Bebidas e infusiones", g=360, unidad="kg",
         espec="Yerba mate con o sin palo, de todas las marcas. Se excluyen compuestas premium y orgánicas.",
         inc=r"yerba",
         exc=r"organica|compuesta premium|barbacua premium|despalada premium|matcha"),
    dict(id="te", nombre="Té en saquitos", grupo="Bebidas e infusiones", g=30, unidad="kg", por_unidad=2,
         espec="Té negro común en saquitos (convertidos a kilo a razón de 2 g por saquito). Se excluyen verdes, saborizados y en hebras.",
         inc=r"\bte\b.*(saquito|bolsita|caja|negro|clasico)|\bte (comun|negro)\b",
         exc=r"verde|rojo|blanco|hierbas|frutal|manzanilla|boldo|tilo|digestivo|matcha|chai|limon|helado|galletita|chocolate|alfajor|barrita|mandarina|naranja|pomelo|durazno|hebras|bergamota|organic|earl|sabor"),
]


# ---------------------------------------------------------------------------
# Lectura de gramaje / volumen / unidades desde el nombre del artículo
# ---------------------------------------------------------------------------
def _num(s):
    return float(s.replace(",", "."))


def contenido_g(nombre, por_unidad=None):
    """Contenido del artículo en gramos (o ml), leído del nombre.
    Devuelve (gramos, es_por_peso) o (None, _) si no se puede leer."""
    s = _norm(nombre).replace(",", ".")

    # venta a granel por kg: "x kg", "por kg", "x 1 kg", "1 kg a granel"
    if re.search(r"\b(x|por)\s*(1\s*)?(kg|kilo)\b", s):
        return 1000.0, True

    # packs "4x10 gr" / "4 sob. x 7.5 gr": el contenido total es N×M
    m = re.search(r"(\d+)\s*(?:un|u|sob|sobres|cubos?|cubitos?|saquitos?)?\.?\s*"
                  r"x\s*(\d+(?:\.\d+)?)\s*(g|gr|grs|ml|cc)\b", s)
    if m:
        return int(m.group(1)) * _num(m.group(2)), True

    # unidades explícitas (huevos "x 12 un", caldos "x 6 cubos", té "25 saquitos")
    if por_unidad:
        if "docena" in s:
            n = 6 if re.search(r"(1/2|media)\s*docena", s) else 12
            return n * por_unidad, False
        m = re.search(r"x\s*(\d+)\s*(un\b|u\b|unidades|cubos?|cubitos?|sobres?|saquitos?)", s)
        if m:
            return int(m.group(1)) * por_unidad, False
        m = re.search(r"(\d+)\s*(cubos?|cubitos?|sobres?|saquitos?|unidades|uni\b|un\b|u\b)", s)
        if m:
            return int(m.group(1)) * por_unidad, False
        m = re.search(r"x\s*(\d+)\b", s)   # "Huevos x 30" a secas
        if m:
            return int(m.group(1)) * por_unidad, False

    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|kilo)\b", s)
    if m:
        return _num(m.group(1)) * 1000, True
    m = re.search(r"(\d+(?:\.\d+)?)\s*(l|lt|lts|litros?)\b", s)
    if m:
        return _num(m.group(1)) * 1000, True
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|cc|cm3)\b", s)
    if m:
        return _num(m.group(1)), True
    m = re.search(r"(\d+(?:\.\d+)?)\s*(g|gr|grs|gramos)\b", s)
    if m:
        return _num(m.group(1)), True
    return None, False


def es_multipack(nombre):
    """Packs (x2, 3x, 'pack 6')... el gramaje unitario del nombre engaña."""
    s = _norm(nombre)
    return bool(re.search(r"\b(pack|x\s*[2-9]\d?\s*(un|u\b|bot|lat)|[2-9]\s*x)\b", s))


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------
def articulos_del_rubro(rubro, productos, ancla=None):
    """[(nombre, marca, cadena, precio, $/g, url)] que cumplen la especificación.
    `ancla`: $/g del rubro en la corrida anterior; si existe, el filtro de
    outliers se centra ahí en vez de en la mediana del día (más robusto)."""
    inc = re.compile(rubro["inc"])
    patron_exc = rubro.get("exc", "")
    if rubro["grupo"] in ("Frutas y verduras", "Carnes") and not rubro.get("sin_exc_global"):
        patron_exc = (patron_exc + "|" if patron_exc else "") + EXC_FRESCO
    exc = re.compile(patron_exc) if patron_exc else None
    out = []
    for p in productos:
        nombre = p.get("n", "")
        s = _norm(nombre)
        if not inc.search(s):
            continue
        if exc and exc.search(s):
            continue
        if not rubro.get("por_unidad") and es_multipack(nombre):
            continue
        g, _ = contenido_g(nombre, rubro.get("por_unidad"))
        if not g or g <= 0:
            continue
        if rubro.get("min_g") and g < rubro["min_g"]:
            continue
        for cad, o in (p.get("pr") or {}).items():
            try:
                precio = float(o[0])
                url = o[1] if len(o) > 1 else None
            except (TypeError, ValueError, IndexError):
                continue
            if precio and precio > 0:
                out.append(dict(n=nombre, m=p.get("m", ""), cad=cad,
                                precio=precio, por_g=precio / g, url=url))
    # filtro de outliers: centrado en el precio del rubro de la corrida anterior
    # (ancla histórica) o, si no hay, en la mediana del día (todas las cadenas).
    centro = ancla
    if centro is None and len(out) >= 3:
        centro = statistics.median(a["por_g"] for a in out)
    if centro is not None:
        out = [a for a in out
               if centro / OUTLIER_FACTOR <= a["por_g"] <= centro * OUTLIER_FACTOR]
    return out


def valorizar(productos, cadenas, anclas=None):
    """Valoriza la canasta completa. Devuelve el dict listo para publicar.
    `anclas`: {id_rubro: $/g de la corrida anterior} para el filtro de outliers."""
    anclas = anclas or {}
    items = []
    por_cadena_tot = {c: 0.0 for c in cadenas}
    por_cadena_imputados = {c: 0 for c in cadenas}
    sin_datos = []

    for r in RUBROS:
        arts = articulos_del_rubro(r, productos, anclas.get(r["id"]))
        por_cad = {}
        for c in cadenas:
            de_c = [a["por_g"] for a in arts if a["cad"] == c]
            if de_c:
                mejores = sorted((a for a in arts if a["cad"] == c),
                                 key=lambda a: a["por_g"])
                b = mejores[0]
                por_cad[c] = dict(
                    precio_kg=round(statistics.mean(de_c) * 1000, 2),
                    n=len(de_c),
                    barato=dict(n=b["n"], m=b["m"],
                                precio_kg=round(b["por_g"] * 1000, 2),
                                url=b["url"]),
                )
        if not por_cad:
            sin_datos.append(r["id"])
            items.append(dict(id=r["id"], nombre=r["nombre"], grupo=r["grupo"],
                              g=r["g"], unidad=r["unidad"], espec=r.get("espec", ""),
                              precio_kg=None, costo=None, n_articulos=0, por_cadena={}))
            continue

        # precio general del rubro = promedio de los promedios por cadena
        precio_g = statistics.mean(v["precio_kg"] for v in por_cad.values()) / 1000
        costo = precio_g * r["g"]
        items.append(dict(id=r["id"], nombre=r["nombre"], grupo=r["grupo"],
                          g=r["g"], unidad=r["unidad"], espec=r.get("espec", ""),
                          precio_kg=round(precio_g * 1000, 2),
                          costo=round(costo, 2),
                          n_articulos=len(arts),
                          por_cadena=por_cad))

        if r["id"] in anclas and anclas[r["id"]]:
            ratio = precio_g / anclas[r["id"]]
            if ratio > 1.5 or ratio < 1 / 1.5:
                print(f"  AVISO {r['id']}: precio salta {ratio:.2f}x vs corrida anterior "
                      f"(${anclas[r['id']]*1000:,.0f} -> ${precio_g*1000:,.0f} /kg)", file=sys.stderr)

        for c in cadenas:
            if c in por_cad:
                por_cadena_tot[c] += por_cad[c]["precio_kg"] / 1000 * r["g"]
            else:
                # imputación: promedio de las cadenas que sí lo tienen
                por_cadena_tot[c] += costo
                por_cadena_imputados[c] += 1

    total = sum(i["costo"] for i in items if i["costo"])
    n_val = sum(1 for i in items if i["costo"])
    return dict(
        region="Noroeste",
        ae=round(total, 2),
        familia=round(total * FAMILIA_AE, 2),
        familia_ae=FAMILIA_AE,
        n_rubros=len(RUBROS),
        n_valorizados=n_val,
        sin_datos=sin_datos,
        por_cadena={c: dict(total=round(por_cadena_tot[c], 2),
                            imputados=por_cadena_imputados[c])
                    for c in cadenas},
        items=items,
    )


# ---------------------------------------------------------------------------
def audit(productos, cadenas, solo=None):
    for r in RUBROS:
        if solo and r["id"] != solo and solo not in r["nombre"].lower():
            continue
        arts = articulos_del_rubro(r, productos)
        print(f"\n=== {r['nombre']} [{r['id']}] · {r['g']} g/mes · {len(arts)} artículos")
        por_cad = {}
        for a in arts:
            por_cad.setdefault(a["cad"], []).append(a)
        for c in cadenas:
            de_c = por_cad.get(c, [])
            if not de_c:
                print(f"  {c:12s}  — sin artículos")
                continue
            vals = [a["por_g"] * 1000 for a in de_c]
            print(f"  {c:12s}  n={len(de_c):3d}  media=${statistics.mean(vals):,.0f}/kg  "
                  f"rango=${min(vals):,.0f}–${max(vals):,.0f}")
        detalle = sorted(arts, key=lambda a: a["por_g"])
        for a in detalle[:4] + ([] if len(detalle) <= 8 else detalle[-2:]):
            print(f"      ${a['por_g']*1000:9,.0f}/kg  ${a['precio']:>9,.0f}  [{a['cad']}] {a['n'][:64]}")


def main():
    ap = argparse.ArgumentParser()
    base = Path(__file__).resolve().parent.parent
    ap.add_argument("--buscador", default=str(base / "data" / "buscador.json"))
    ap.add_argument("-o", "--out", default=str(base / "data" / "canasta"),
                    help="directorio de salida (canasta.json + serie.json)")
    ap.add_argument("--audit", nargs="?", const="__all__", default=None,
                    help="modo inspección: qué artículos matchean cada rubro")
    ap.add_argument("--fecha", default=None, help="fecha de la captura (AAAA-MM-DD)")
    args = ap.parse_args()

    d = json.loads(Path(args.buscador).read_text())
    productos = d["productos"]
    cadenas = sorted(d.get("cadenas", {}).keys()) or \
        sorted({c for p in productos for c in (p.get("pr") or {})})
    fecha = args.fecha or d.get("fecha") or date.today().isoformat()

    if args.audit:
        audit(productos, cadenas, None if args.audit == "__all__" else args.audit)
        return

    anclas = {}
    prev_p = Path(args.out) / "canasta.json"
    if prev_p.exists():
        try:
            prev = json.loads(prev_p.read_text())
            anclas = {i["id"]: i["precio_kg"] / 1000
                      for i in prev.get("items", []) if i.get("precio_kg")}
            print(f"[ancla] {len(anclas)} rubros anclados a la corrida anterior "
                  f"({prev.get('fecha')})", file=sys.stderr)
        except Exception:
            pass

    res = valorizar(productos, cadenas, anclas)
    res["fecha"] = fecha
    res["actualizado"] = d.get("actualizado", fecha)
    res["cadenas"] = cadenas

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "canasta.json").write_text(
        json.dumps(res, ensure_ascii=False, separators=(",", ":")))

    # serie histórica: un punto por día (se reescribe si se recalcula el mismo día)
    serie_p = out / "serie.json"
    serie = json.loads(serie_p.read_text()) if serie_p.exists() else {"puntos": []}
    serie["puntos"] = [pt for pt in serie["puntos"] if pt["fecha"] != fecha]
    serie["puntos"].append(dict(
        fecha=fecha, ae=res["ae"], familia=res["familia"],
        por_cadena={c: v["total"] for c, v in res["por_cadena"].items()},
        n_valorizados=res["n_valorizados"],
    ))
    serie["puntos"].sort(key=lambda x: x["fecha"])
    serie["actualizado"] = fecha
    serie_p.write_text(json.dumps(serie, ensure_ascii=False, separators=(",", ":")))

    print(f"CBA Tucumán {fecha}: ${res['ae']:,.0f} por adulto equivalente · "
          f"${res['familia']:,.0f} familia tipo · {res['n_valorizados']}/{res['n_rubros']} rubros",
          file=sys.stderr)
    if res["sin_datos"]:
        print(f"  sin datos: {', '.join(res['sin_datos'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
