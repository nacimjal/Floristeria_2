"""Importa el Excel de la florería (hojas 'Costeo' y 'Costeo Final') a SQLite."""
import re, sys, warnings
from datetime import datetime
from openpyxl import load_workbook
from db import conectar, DB_PATH

warnings.filterwarnings("ignore")

# Alias: nombre en hoja Costeo (normalizado) -> insumo canónico
ALIAS = {
    "rosas": "Rosas", "girasoles": "Girasoles", "hortensias": "Hortensias",
    "hortensias de vivero": "Hortensias - Vivero", "astromelias": "Astromelias",
    "claveles en boton": "Claveles", "claveles rosados": "Claveles",
    "dolar (eucalipto)": "Dólar - Eucalipto", "dolar": "Dólar - Eucalipto",
    "eucalipto": "Dólar - Eucalipto", "montecasino": "Montecasino", "ruda": "Ruda",
    "siempre viva": "Siempre viva", "lluvia": "Lluvia", "helecho de cuero": "Helecho de cuero",
    "mirto": "Mirto", "papel coreano": "Papel coreano",
    "papel coreano especial": "Papel coreano especial (escarchado)",
    "box": "Box", "box (pote)": "Box", "capullo para rosa": "Capullo para rosas",
    "espuma oasis": "Oasis", "espuma": "Oasis", "porta tarjetas": "Porta tarjeta",
    "5": "Tarjeta dedicatoria", "cintas": "Cintas", "ligas": "Ligas", "baldes": "Baldes",
    "centro de mesa": "Centro de mesa", "pliego cartulina": "Pliego de cartulina",
}

# Columnas (0-index) de 'Costeo Final': (insumo, col_cantidad, col_precio_unitario)
GRUPOS = [
    ("Rosas", 1, 2), ("Girasoles", 4, 5), ("Claveles", 7, 8), ("Hortensias", 10, 11),
    ("Hortensias - Vivero", 13, 14), ("Astromelias", 16, 17),
    ("Dólar - Eucalipto", 23, 24), ("Montecasino", 26, 27), ("Ruda", 29, 30),
    ("Siempre viva", 32, 33), ("Lluvia", 35, 36), ("Helecho de cuero", 38, 39), ("Mirto", 41, 42),
    ("Papel coreano", 48, 49), ("Box", 51, 52), ("Capullo para rosas", 54, 55),
    ("Listones para gerberas", 57, 58), ("Alambre para gerberas", 60, 61),
    ("Cintas delgadas", 63, 64), ("Cintas gruesas", 66, 67),
    ("Tarjeta dedicatoria", 69, 70), ("Porta tarjeta", 72, 73), ("Oasis", 75, 76),
]
COL_COSTO, COL_PRECIO = 83, 84
CAT_RECETA = {"Listones para gerberas": "Complementos", "Alambre para gerberas": "Complementos",
              "Cintas delgadas": "Complementos", "Cintas gruesas": "Complementos"}


def num(v, default=None):
    return float(v) if isinstance(v, (int, float)) else default


def canon(item):
    s = str(item).split(" / ")[0].strip().lower()
    s = re.sub(r"\s*\(teñida\)", "", s)
    return ALIAS.get(s, str(item).split(" / ")[0].strip().capitalize())


def importar(xlsx, db_path=DB_PATH, reemplazar=False):
    if db_path.exists():
        if not reemplazar:
            raise SystemExit(f'{db_path.name} ya existe. Usa --reemplazar para BORRARLA y reimportar.')
        db_path.unlink()
    con = conectar(db_path)
    wb_v = load_workbook(xlsx, data_only=True)
    cos, fin = wb_v["Costeo"], wb_v["Costeo Final"]
    avisos = []

    def cat_id(nombre):
        if not nombre:
            return None
        r = con.execute("SELECT id FROM categorias WHERE nombre=?", (nombre,)).fetchone()
        return r["id"] if r else con.execute("INSERT INTO categorias(nombre) VALUES (?)", (nombre,)).lastrowid

    def insumo_id(nombre, categoria=None, costo=None):
        r = con.execute("SELECT id FROM insumos WHERE nombre=?", (nombre,)).fetchone()
        if r:
            if costo is not None:
                con.execute("UPDATE insumos SET precio_unitario=? WHERE id=?", (costo, r["id"]))
            if categoria:
                con.execute("UPDATE insumos SET categoria_id=COALESCE(categoria_id,?) WHERE id=?", (cat_id(categoria), r["id"]))
            return r["id"]
        return con.execute("INSERT INTO insumos(nombre,categoria_id,precio_unitario) VALUES (?,?,?)",
                           (nombre, cat_id(categoria), costo or 0)).lastrowid

    # ---- 1) Hoja Costeo: compras reales (con fecha) y precios de referencia ('Ref') ----
    n_compras = 0
    for r in cos.iter_rows(min_row=4, max_col=12, values_only=True):
        fecha, item, cat, cant, e, f, _, _, desc, prov, costo_paq, _ = r
        if item is None or cat == "Categorías":
            continue
        nombre = canon(item)
        paquetes = num(cant, 1)
        # unidades por paquete: col E; si viene 1 pero F trae el dato real (p.ej. ramal=10), usar F
        upp = num(e, 1) or 1
        if upp == 1 and num(f, 1) and num(f, 1) > 1:
            upp = num(f)
        costo_paq = num(costo_paq, 0)
        iid = insumo_id(nombre, cat)
        if isinstance(fecha, datetime):
            con.execute("""INSERT INTO compras(fecha,insumo_id,paquetes,unidades_por_paquete,costo_paquete,proveedor,notas,precio_costo)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (fecha.date().isoformat(), iid, paquetes, upp, costo_paq, prov, desc, costo_paq / upp))
            n_compras += 1
            con.execute("UPDATE insumos SET precio_unitario=? WHERE id=?", (costo_paq / upp, iid))
        elif con.execute("SELECT precio_unitario FROM insumos WHERE id=?", (iid,)).fetchone()[0] == 0:
            con.execute("UPDATE insumos SET precio_unitario=? WHERE id=?", (costo_paq / upp, iid))  # precio 'Ref'

    # ---- 2) Hoja Costeo Final: productos y recetas ----
    n_prod = 0
    for fila, r in enumerate(fin.iter_rows(min_row=3, values_only=True), start=3):
        nombre = r[0]
        if not nombre:
            continue
        receta = []
        for ins, cq, cp in GRUPOS:
            q, p = num(r[cq], 0), num(r[cp])
            if q > 0 and p is not None:
                receta.append((ins, q, p))
        if not receta:
            avisos.append(f"Fila {fila} '{nombre}': sin cantidades, no se importó")
            continue
        precio = num(r[COL_PRECIO], 0)
        flores_foll = {g[0] for g in GRUPOS[:13]}
        incompleto = not any(x[0] in flores_foll for x in receta)  # sin flores ni follaje
        if incompleto:
            avisos.append(f"Fila {fila} '{nombre}': sin flores/follaje en el Excel, se importó DESACTIVADO")
        pid = con.execute("INSERT INTO productos(nombre,precio_venta,activo) VALUES (?,?,?)",
                          (nombre, round(precio, 2), 0 if incompleto else 1)).lastrowid
        for ins, q, p in receta:
            iid = insumo_id(ins, categoria=CAT_RECETA.get(ins), costo=p)  # el precio unitario usado en 'Costeo Final' pasa a ser el costo vigente
            con.execute("INSERT INTO receta VALUES (?,?,?)", (pid, iid, q))
        n_prod += 1
        # verificación contra el costo total del Excel
        calc = con.execute("SELECT costo FROM v_costo_producto WHERE producto_id=?", (pid,)).fetchone()[0]
        if precio > 0 and calc > 0:  # el margen se deduce del precio de venta del Excel
            con.execute("UPDATE productos SET margen=? WHERE id=?",
                        (min(max((precio - calc) / precio * 100, 0), 99.9), pid))
        xl = num(r[COL_COSTO], 0)
        if abs(calc - xl) > 0.01:
            avisos.append(f"'{nombre}': costo app {calc:.2f} vs Excel {xl:.2f}")

    con.execute("""UPDATE insumos SET cantidad = COALESCE(
        (SELECT SUM(paquetes*unidades_por_paquete) FROM compras WHERE insumo_id=insumos.id), 0)""")
    con.execute("""UPDATE productos SET categoria_id=(SELECT id FROM categorias_producto WHERE nombre='Ramos')
                   WHERE categoria_id IS NULL""")
    con.commit()
    print(f"Insumos: {con.execute('SELECT COUNT(*) FROM insumos').fetchone()[0]} | "
          f"Compras: {n_compras} | Productos: {n_prod}")
    for a in avisos:
        print("AVISO:", a)
    return con


if __name__ == "__main__":
    importar(sys.argv[1], reemplazar='--reemplazar' in sys.argv)
