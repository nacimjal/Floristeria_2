"""Capa de datos (backend): SQLite + operaciones CRUD y consultas."""
import sqlite3
import sys
from pathlib import Path

# Junto al .py, o junto al .exe cuando se empaqueta con PyInstaller
BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
DB_PATH = BASE / "floreria.db"

CATEGORIAS_INICIALES = ["Flores", "Follaje", "Complementos"]
CATEGORIAS_GASTO_INICIALES = ["Alquiler", "Servicios", "Transporte", "Otros"]
AHORA = "strftime('%Y-%m-%d %H:%M:%f','now','localtime')"

SCHEMA = """
-- excluir_productos = 1: los insumos de esa categoría (p. ej. «Gasto Operativo») no se ofrecen al armar un producto
CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
    excluir_productos INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categorias_producto (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS insumos (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    descripcion TEXT NOT NULL DEFAULT '',
    categoria_id INTEGER REFERENCES categorias(id),
    cantidad REAL NOT NULL DEFAULT 0,
    precio_unitario REAL NOT NULL DEFAULT 0,
    actualizado TEXT          -- última vez que se creó/editó (para mostrarlo primero)
);

-- Histórico de compras por insumo. precio_costo = precio por unidad.
-- (paquetes / unidades_por_paquete / costo_paquete vienen del Excel; las compras nuevas usan 1 y 1)
CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    insumo_id INTEGER NOT NULL REFERENCES insumos(id),
    paquetes REAL NOT NULL,
    unidades_por_paquete REAL NOT NULL DEFAULT 1,
    costo_paquete REAL NOT NULL,
    proveedor TEXT,
    notas TEXT,
    precio_costo REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categorias_gasto (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE COLLATE NOCASE
);

-- Gasto operativo: renta, transporte, servicios… (lo que no es insumo de un producto).
-- «categoria» (texto) es un dato antiguo sin uso; ahora se usa categoria_id.
CREATE TABLE IF NOT EXISTS gastos (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    concepto TEXT NOT NULL,
    categoria TEXT,
    monto REAL NOT NULL,
    categoria_id INTEGER REFERENCES categorias_gasto(id),
    notas TEXT
);

-- margen = % de ganancia sobre el precio de venta; el precio de venta se CALCULA (ver vista).
-- precio_venta queda como dato antiguo, ya no se usa.
CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE,
    descripcion TEXT NOT NULL DEFAULT '',
    categoria_id INTEGER REFERENCES categorias_producto(id),
    precio_venta REAL NOT NULL DEFAULT 0,
    margen REAL NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1,
    imagen BLOB          -- foto del producto (JPEG/PNG ya reducida); se lee aparte para no cargarla en los listados
);

CREATE TABLE IF NOT EXISTS receta (
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    insumo_id INTEGER NOT NULL REFERENCES insumos(id),
    cantidad REAL NOT NULL,
    PRIMARY KEY (producto_id, insumo_id)
);

-- Sin descuento de inventario: la venta solo guarda precio y costo del momento
CREATE TABLE IF NOT EXISTS ventas (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad INTEGER NOT NULL DEFAULT 1,
    precio_unitario REAL NOT NULL,
    costo_unitario REAL NOT NULL,
    cliente TEXT,
    notas TEXT
);

DROP VIEW IF EXISTS v_costo_producto;
CREATE VIEW v_costo_producto AS
SELECT producto_id, nombre, margen, costo, precio_venta,
       ROUND(precio_venta - costo, 4) AS ganancia
FROM (
    SELECT p.id AS producto_id, p.nombre, p.margen,
           ROUND(COALESCE(SUM(r.cantidad * i.precio_unitario), 0), 4) AS costo,
           ROUND(CASE WHEN p.margen < 100
                      THEN COALESCE(SUM(r.cantidad * i.precio_unitario), 0) / (1 - p.margen / 100.0)
                      ELSE 0 END, 2) AS precio_venta
    FROM productos p
    LEFT JOIN receta r ON r.producto_id = p.id
    LEFT JOIN insumos i ON i.id = r.insumo_id
    GROUP BY p.id
);
"""


def _columnas(con, tabla):
    return [r["name"] for r in con.execute(f"PRAGMA table_info({tabla})")]


def _migrar(con):
    """Actualiza bases creadas con versiones anteriores, sin perder datos."""
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver < 1:  # v1: categoría de productos
        if "categoria_id" not in _columnas(con, "productos"):
            con.execute("ALTER TABLE productos ADD COLUMN categoria_id INTEGER REFERENCES categorias_producto(id)")
        if con.execute("SELECT COUNT(*) FROM categorias_producto").fetchone()[0] == 0:
            cid = con.execute("INSERT INTO categorias_producto(nombre) VALUES ('Ramos')").lastrowid
            con.execute("UPDATE productos SET categoria_id=? WHERE categoria_id IS NULL", (cid,))
    if ver < 2:  # v2: insumos.actualizado, compras.precio_costo, productos.margen
        if "actualizado" not in _columnas(con, "insumos"):
            con.execute("ALTER TABLE insumos ADD COLUMN actualizado TEXT")
        if "precio_costo" not in _columnas(con, "compras"):
            con.execute("ALTER TABLE compras ADD COLUMN precio_costo REAL NOT NULL DEFAULT 0")
            con.execute("UPDATE compras SET precio_costo = costo_paquete / unidades_por_paquete "
                        "WHERE unidades_por_paquete > 0")
        if "margen" not in _columnas(con, "productos"):
            con.execute("ALTER TABLE productos ADD COLUMN margen REAL NOT NULL DEFAULT 0")
            filas = con.execute("""SELECT p.id, p.precio_venta, COALESCE(SUM(r.cantidad*i.precio_unitario),0) AS costo
                                   FROM productos p LEFT JOIN receta r ON r.producto_id=p.id
                                   LEFT JOIN insumos i ON i.id=r.insumo_id GROUP BY p.id""").fetchall()
            for f in filas:  # el margen sale del precio de venta que ya tenías guardado
                m = (f["precio_venta"] - f["costo"]) / f["precio_venta"] * 100 if f["precio_venta"] > 0 else 0
                con.execute("UPDATE productos SET margen=? WHERE id=?", (min(max(m, 0), 99.9), f["id"]))
    if ver < 3:  # v3: categorías de insumo que no se usan en productos (p. ej. «Gasto Operativo»)
        if "excluir_productos" not in _columnas(con, "categorias"):
            con.execute("ALTER TABLE categorias ADD COLUMN excluir_productos INTEGER NOT NULL DEFAULT 0")
            con.execute("UPDATE categorias SET excluir_productos = 1 WHERE nombre = 'Gasto Operativo'")
    if ver < 4:  # v4: pestaña Gasto Operativo (categorías de gasto + columnas nuevas en gastos)
        cols = _columnas(con, "gastos")
        if "categoria_id" not in cols:
            con.execute("ALTER TABLE gastos ADD COLUMN categoria_id INTEGER REFERENCES categorias_gasto(id)")
        if "notas" not in cols:
            con.execute("ALTER TABLE gastos ADD COLUMN notas TEXT")
        if con.execute("SELECT COUNT(*) FROM categorias_gasto").fetchone()[0] == 0:
            con.executemany("INSERT INTO categorias_gasto(nombre) VALUES (?)",
                            [(c,) for c in CATEGORIAS_GASTO_INICIALES])
    if ver < 5:  # v5: imagen del producto
        if "imagen" not in _columnas(con, "productos"):
            con.execute("ALTER TABLE productos ADD COLUMN imagen BLOB")
    con.execute("PRAGMA user_version = 5")


def conectar(path=DB_PATH):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 0:
        con.executemany("INSERT INTO categorias(nombre) VALUES (?)", [(c,) for c in CATEGORIAS_INICIALES])
    _migrar(con)
    con.commit()
    return con


# ======================= CATEGORÍAS =======================
# tipo "insumo" -> categorias | tipo "producto" -> categorias_producto
_TIPOS_CAT = {
    "insumo": ("categorias", "insumos", "insumo(s)"),
    "producto": ("categorias_producto", "productos", "producto(s)"),
    "gasto": ("categorias_gasto", "gastos", "gasto(s)"),
}


def listar_categorias(con, tipo="insumo"):
    if tipo == "insumo":
        return con.execute("SELECT id, nombre, excluir_productos FROM categorias ORDER BY nombre").fetchall()
    return con.execute(f"SELECT id, nombre, 0 AS excluir_productos FROM {_TIPOS_CAT[tipo][0]} ORDER BY nombre").fetchall()


def ocultar_categoria_en_productos(con, cat_id, ocultar):
    """Las categorías de insumo ocultas no ofrecen sus insumos al armar un producto."""
    with con:
        con.execute("UPDATE categorias SET excluir_productos=? WHERE id=?", (1 if ocultar else 0, cat_id))


def crear_categoria(con, nombre, tipo="insumo"):
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre de la categoría no puede estar vacío.")
    try:
        with con:
            return con.execute(f"INSERT INTO {_TIPOS_CAT[tipo][0]}(nombre) VALUES (?)", (nombre,)).lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Ya existe la categoría «{nombre}».")


def renombrar_categoria(con, cat_id, nombre, tipo="insumo"):
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre de la categoría no puede estar vacío.")
    try:
        with con:
            con.execute(f"UPDATE {_TIPOS_CAT[tipo][0]} SET nombre=? WHERE id=?", (nombre, cat_id))
    except sqlite3.IntegrityError:
        raise ValueError(f"Ya existe la categoría «{nombre}».")


def eliminar_categoria(con, cat_id, tipo="insumo"):
    tabla, usuarios, etiqueta = _TIPOS_CAT[tipo]
    n = con.execute(f"SELECT COUNT(*) FROM {usuarios} WHERE categoria_id=?", (cat_id,)).fetchone()[0]
    if n:
        raise ValueError(f"No se puede eliminar: {n} {etiqueta} usan esta categoría.")
    with con:
        con.execute(f"DELETE FROM {tabla} WHERE id=?", (cat_id,))


# ======================= INSUMOS =======================
_SEL_INSUMO = """SELECT i.id, i.nombre, i.descripcion, i.categoria_id, COALESCE(c.nombre,'') AS categoria,
                        i.cantidad, i.precio_unitario,
                        ROUND(i.cantidad * i.precio_unitario, 4) AS precio_total, i.actualizado
                 FROM insumos i LEFT JOIN categorias c ON c.id = i.categoria_id"""

_ORDEN_INSUMOS = {  # columnas por las que se puede ordenar desde la cabecera
    "nombre": "LOWER(i.nombre)",
    "descripcion": "LOWER(i.descripcion)",
    "categoria": "LOWER(COALESCE(c.nombre,''))",
    "precio_unitario": "i.precio_unitario",
}


def _order_insumos(orden, asc):
    if orden in _ORDEN_INSUMOS:
        return f"{_ORDEN_INSUMOS[orden]} {'ASC' if asc else 'DESC'}, i.id"
    # por defecto: lo creado/editado más recientemente primero
    return "COALESCE(i.actualizado,'') DESC, LOWER(COALESCE(c.nombre,'')), LOWER(i.nombre)"


def _where_insumos(texto, categoria_id):
    w, p = " WHERE 1=1", []
    if texto:
        w += " AND (i.nombre LIKE ? OR i.descripcion LIKE ?)"
        p += [f"%{texto}%"] * 2
    if categoria_id:
        w += " AND i.categoria_id = ?"
        p.append(categoria_id)
    return w, p


def listar_insumos(con, texto="", categoria_id=None, orden=None, asc=True, limite=None, offset=0):
    w, p = _where_insumos(texto, categoria_id)
    q = _SEL_INSUMO + w + " ORDER BY " + _order_insumos(orden, asc)
    if limite:
        q += " LIMIT ? OFFSET ?"
        p += [limite, offset]
    return con.execute(q, p).fetchall()


def listar_insumos_para_producto(con, producto_id=None):
    """Insumos que se ofrecen al armar un producto: se omiten las categorías ocultas (p. ej. Gasto Operativo),
    salvo los que ese producto ya usa (para no perderlos al editarlo)."""
    return con.execute(
        _SEL_INSUMO + """ WHERE COALESCE(c.excluir_productos, 0) = 0
                          OR i.id IN (SELECT insumo_id FROM receta WHERE producto_id = ?)
                          ORDER BY LOWER(i.nombre)""", (producto_id,)).fetchall()


def contar_insumos(con, texto="", categoria_id=None):
    w, p = _where_insumos(texto, categoria_id)
    return con.execute("SELECT COUNT(*) FROM insumos i" + w, p).fetchone()[0]


def obtener_insumo(con, insumo_id):
    return con.execute(_SEL_INSUMO + " WHERE i.id=?", (insumo_id,)).fetchone()


def guardar_insumo(con, nombre, descripcion, categoria_id, cantidad, precio_total, insumo_id=None):
    """cantidad = unidades que trae el paquete; precio_total = lo que cuesta el paquete.
    El precio unitario se calcula: precio_total / cantidad (se guarda sin redondear)."""
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del insumo no puede estar vacío.")
    if categoria_id is None:
        raise ValueError("La categoría es obligatoria.")
    if cantidad is None or cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que 0.")
    if precio_total is None or precio_total < 0:
        raise ValueError("El precio total es obligatorio y no puede ser negativo.")
    precio_unitario = precio_total / cantidad
    with con:
        if insumo_id is None:
            return con.execute(
                f"INSERT INTO insumos(nombre,descripcion,categoria_id,cantidad,precio_unitario,actualizado) "
                f"VALUES (?,?,?,?,?,{AHORA})",
                (nombre, descripcion, categoria_id, cantidad, precio_unitario)).lastrowid
        con.execute(f"UPDATE insumos SET nombre=?,descripcion=?,categoria_id=?,cantidad=?,precio_unitario=?,"
                    f"actualizado={AHORA} WHERE id=?",
                    (nombre, descripcion, categoria_id, cantidad, precio_unitario, insumo_id))
        return insumo_id


def eliminar_insumo(con, insumo_id):
    usos = con.execute("""SELECT p.nombre FROM receta r JOIN productos p ON p.id=r.producto_id
                          WHERE r.insumo_id=? ORDER BY p.nombre""", (insumo_id,)).fetchall()
    if usos:
        lista = ", ".join(u["nombre"] for u in usos[:5]) + ("…" if len(usos) > 5 else "")
        raise ValueError(f"No se puede eliminar: lo usan {len(usos)} producto(s): {lista}")
    if con.execute("SELECT COUNT(*) FROM compras WHERE insumo_id=?", (insumo_id,)).fetchone()[0]:
        raise ValueError("No se puede eliminar: tiene compras registradas en el histórico.")
    with con:
        con.execute("DELETE FROM insumos WHERE id=?", (insumo_id,))


# ======================= PRODUCTOS =======================
def _where_productos(texto, categoria_id):
    w, p = " WHERE 1=1", []
    if texto:
        w += " AND (p.nombre LIKE ? OR p.descripcion LIKE ?)"
        p += [f"%{texto}%"] * 2
    if categoria_id:
        w += " AND p.categoria_id = ?"
        p.append(categoria_id)
    return w, p


def listar_productos(con, texto="", categoria_id=None, limite=None, offset=0):
    w, p = _where_productos(texto, categoria_id)
    q = """SELECT p.id, p.nombre, p.descripcion, p.categoria_id, COALESCE(c.nombre,'') AS categoria,
                  p.activo, (p.imagen IS NOT NULL) AS tiene_imagen, v.margen, v.costo, v.precio_venta, v.ganancia,
                  (SELECT COUNT(*) FROM receta r WHERE r.producto_id = p.id) AS n_insumos
           FROM productos p
           JOIN v_costo_producto v ON v.producto_id = p.id
           LEFT JOIN categorias_producto c ON c.id = p.categoria_id""" + w + " ORDER BY LOWER(p.nombre)"
    if limite:
        q += " LIMIT ? OFFSET ?"
        p += [limite, offset]
    return con.execute(q, p).fetchall()


def contar_productos(con, texto="", categoria_id=None):
    w, p = _where_productos(texto, categoria_id)
    return con.execute("SELECT COUNT(*) FROM productos p" + w, p).fetchone()[0]


def obtener_producto(con, producto_id):
    return con.execute("SELECT * FROM productos WHERE id=?", (producto_id,)).fetchone()


def obtener_receta(con, producto_id):
    return con.execute("""SELECT r.insumo_id, i.nombre, i.descripcion, r.cantidad, i.precio_unitario,
                                 ROUND(r.cantidad * i.precio_unitario, 4) AS subtotal
                          FROM receta r JOIN insumos i ON i.id = r.insumo_id
                          WHERE r.producto_id = ? ORDER BY i.nombre""", (producto_id,)).fetchall()


def _validar_margen(margen):
    if not 0 <= margen < 100:
        raise ValueError("El margen debe estar entre 0 y 99.9 %.")


SIN_CAMBIO = object()  # valor para «no modificar la imagen» en guardar_producto


def guardar_producto(con, nombre, descripcion, categoria_id, margen, activo, receta, producto_id=None,
                     imagen=SIN_CAMBIO):
    """receta: lista de (insumo_id, cantidad). imagen: bytes = guardarla, None = quitarla, SIN_CAMBIO = dejarla."""
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del producto no puede estar vacío.")
    if categoria_id is None:
        raise ValueError("La categoría es obligatoria.")
    _validar_margen(margen)
    try:
        with con:
            if producto_id is None:
                producto_id = con.execute(
                    "INSERT INTO productos(nombre,descripcion,categoria_id,margen,activo,imagen) VALUES (?,?,?,?,?,?)",
                    (nombre, descripcion, categoria_id, margen, int(activo),
                     None if imagen is SIN_CAMBIO else imagen)).lastrowid
            else:
                con.execute("UPDATE productos SET nombre=?,descripcion=?,categoria_id=?,margen=?,activo=? WHERE id=?",
                            (nombre, descripcion, categoria_id, margen, int(activo), producto_id))
                con.execute("DELETE FROM receta WHERE producto_id=?", (producto_id,))
                if imagen is not SIN_CAMBIO:
                    con.execute("UPDATE productos SET imagen=? WHERE id=?", (imagen, producto_id))
            con.executemany("INSERT INTO receta(producto_id,insumo_id,cantidad) VALUES (?,?,?)",
                            [(producto_id, iid, q) for iid, q in receta])
    except sqlite3.IntegrityError:
        raise ValueError(f"Ya existe un producto llamado «{nombre}».")
    return producto_id


def obtener_imagen_producto(con, producto_id):
    """Bytes de la imagen del producto, o None si no tiene."""
    r = con.execute("SELECT imagen FROM productos WHERE id=?", (producto_id,)).fetchone()
    return r["imagen"] if r else None


def actualizar_margen(con, producto_id, margen):
    _validar_margen(margen)
    with con:
        con.execute("UPDATE productos SET margen=? WHERE id=?", (margen, producto_id))


def eliminar_producto(con, producto_id):
    if con.execute("SELECT COUNT(*) FROM ventas WHERE producto_id=?", (producto_id,)).fetchone()[0]:
        raise ValueError("No se puede eliminar: tiene ventas registradas. Puedes desactivarlo en su lugar.")
    with con:
        con.execute("DELETE FROM productos WHERE id=?", (producto_id,))


def copiar_producto(con, producto_id):
    """Duplica un producto con todos sus atributos e insumos; al nombre se le agrega «Copiado»."""
    p = obtener_producto(con, producto_id)
    if p is None:
        raise ValueError("El producto ya no existe.")
    base = f"{p['nombre']} Copiado"
    nombre, n = base, 1
    while con.execute("SELECT 1 FROM productos WHERE nombre=?", (nombre,)).fetchone():
        n += 1
        nombre = f"{base} {n}"  # si ya existe una copia: «… Copiado 2», «… Copiado 3»
    with con:
        nuevo = con.execute(
            "INSERT INTO productos(nombre,descripcion,categoria_id,margen,activo,imagen) VALUES (?,?,?,?,?,?)",
            (nombre, p["descripcion"], p["categoria_id"], p["margen"], p["activo"], p["imagen"])).lastrowid
        con.execute("INSERT INTO receta(producto_id,insumo_id,cantidad) "
                    "SELECT ?, insumo_id, cantidad FROM receta WHERE producto_id=?", (nuevo, producto_id))
    return nuevo


def posicion_producto(con, producto_id, texto="", categoria_id=None):
    """Posición (desde 0) del producto en el listado con los filtros dados."""
    w, p = _where_productos(texto, categoria_id)
    return con.execute("SELECT COUNT(*) FROM productos p" + w +
                       " AND LOWER(p.nombre) < (SELECT LOWER(nombre) FROM productos WHERE id=?)",
                       p + [producto_id]).fetchone()[0]


# ======================= HISTÓRICO DE COMPRAS =======================
# cantidad = paquetes comprados; precio = costo de cada uno; costo_total = cantidad x precio
_SEL_COMPRA = """SELECT c.id, c.fecha, c.insumo_id, i.nombre AS insumo, i.descripcion AS descripcion,
                        COALESCE(cat.nombre,'') AS categoria,
                        c.paquetes AS cantidad, c.costo_paquete AS precio,
                        ROUND(c.paquetes * c.costo_paquete, 4) AS costo_total
                 FROM compras c
                 JOIN insumos i ON i.id = c.insumo_id
                 LEFT JOIN categorias cat ON cat.id = i.categoria_id"""


_ORDEN_COMPRAS = {  # columnas por las que se puede ordenar desde la cabecera
    "nombre": "LOWER(i.nombre)",
    "categoria": "LOWER(COALESCE(cat.nombre,''))",
    "fecha": "c.fecha",
    "cantidad": "c.paquetes",
    "precio_costo": "c.costo_paquete",
    "costo_total": "(c.paquetes * c.costo_paquete)",
}


def _order_compras(orden, asc):
    if orden in _ORDEN_COMPRAS:
        d = "ASC" if asc else "DESC"
        return f"{_ORDEN_COMPRAS[orden]} {d}, c.id {d}"
    return "c.fecha DESC, c.id DESC"  # por defecto: lo más reciente primero


def _where_compras(texto, categoria_id, desde=None, hasta=None):
    """desde / hasta: fechas ISO (yyyy-mm-dd), ambas incluidas."""
    w, p = " WHERE 1=1", []
    if texto:
        w += " AND (i.nombre LIKE ? OR i.descripcion LIKE ?)"
        p += [f"%{texto}%"] * 2
    if categoria_id:
        w += " AND i.categoria_id = ?"
        p.append(categoria_id)
    if desde:
        w += " AND c.fecha >= ?"
        p.append(desde)
    if hasta:
        w += " AND c.fecha <= ?"
        p.append(hasta)
    return w, p


def listar_compras(con, texto="", categoria_id=None, desde=None, hasta=None,
                   orden=None, asc=True, limite=None, offset=0):
    w, p = _where_compras(texto, categoria_id, desde, hasta)
    q = _SEL_COMPRA + w + " ORDER BY " + _order_compras(orden, asc)
    if limite:
        q += " LIMIT ? OFFSET ?"
        p += [limite, offset]
    return con.execute(q, p).fetchall()


_FROM_COMPRAS = " FROM compras c JOIN insumos i ON i.id = c.insumo_id"


def contar_compras(con, texto="", categoria_id=None, desde=None, hasta=None):
    w, p = _where_compras(texto, categoria_id, desde, hasta)
    return con.execute("SELECT COUNT(*)" + _FROM_COMPRAS + w, p).fetchone()[0]


def total_compras(con, texto="", categoria_id=None, desde=None, hasta=None):
    """Suma del «Costo Total» (cantidad x precio) de TODAS las compras que cumplen los filtros,
    no solo las de la página visible."""
    w, p = _where_compras(texto, categoria_id, desde, hasta)
    return con.execute("SELECT COALESCE(SUM(c.paquetes * c.costo_paquete), 0)" + _FROM_COMPRAS + w, p).fetchone()[0]


def rango_fechas_compras(con):
    """(fecha más antigua, fecha más reciente) del histórico; (None, None) si está vacío."""
    r = con.execute("SELECT MIN(fecha), MAX(fecha) FROM compras").fetchone()
    return r[0], r[1]


def obtener_compra(con, compra_id):
    return con.execute(_SEL_COMPRA + " WHERE c.id=?", (compra_id,)).fetchone()


def guardar_compra(con, insumo_id, fecha_iso, cantidad, precio, compra_id=None):
    """cantidad = paquetes/unidades compradas; precio = costo de cada una. Costo total = cantidad x precio."""
    if insumo_id is None:
        raise ValueError("Debes elegir un insumo.")
    if not fecha_iso:
        raise ValueError("La fecha de compra es obligatoria.")
    if cantidad is None or cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que 0.")
    if precio is None or precio < 0:
        raise ValueError("El precio de costo es obligatorio y no puede ser negativo.")
    with con:
        if compra_id is None:
            return con.execute(
                "INSERT INTO compras(fecha,insumo_id,paquetes,unidades_por_paquete,costo_paquete,precio_costo) "
                "VALUES (?,?,?,1,?,?)", (fecha_iso, insumo_id, cantidad, precio, precio)).lastrowid
        # precio_costo (columna interna) se mantiene como precio por unidad suelta
        con.execute("UPDATE compras SET fecha=?, insumo_id=?, paquetes=?, costo_paquete=?, "
                    "precio_costo = ? / COALESCE(NULLIF(unidades_por_paquete, 0), 1) WHERE id=?",
                    (fecha_iso, insumo_id, cantidad, precio, precio, compra_id))
        return compra_id


def eliminar_compra(con, compra_id):
    with con:
        con.execute("DELETE FROM compras WHERE id=?", (compra_id,))


# ======================= VENTAS =======================
# Las ventas NO descuentan insumos. costo_unitario es el costo del producto AL MOMENTO de vender
# (así el historial no cambia si después suben los insumos).
_SEL_VENTA = """SELECT v.id, v.fecha, v.producto_id, p.nombre AS producto, COALESCE(c.nombre,'') AS categoria,
                       COALESCE(v.cliente,'') AS cliente, COALESCE(v.notas,'') AS notas,
                       v.cantidad, v.precio_unitario AS precio, v.costo_unitario AS costo,
                       ROUND(v.cantidad * v.precio_unitario, 4) AS total_venta,
                       ROUND(v.cantidad * v.costo_unitario, 4) AS costo_total,
                       ROUND(v.cantidad * (v.precio_unitario - v.costo_unitario), 4) AS ganancia
                FROM ventas v
                JOIN productos p ON p.id = v.producto_id
                LEFT JOIN categorias_producto c ON c.id = p.categoria_id"""
_FROM_VENTAS = """ FROM ventas v JOIN productos p ON p.id = v.producto_id"""

_ORDEN_VENTAS = {
    "fecha": "v.fecha", "producto": "LOWER(p.nombre)", "categoria": "LOWER(COALESCE(c.nombre,''))",
    "cliente": "LOWER(COALESCE(v.cliente,''))", "cantidad": "v.cantidad", "precio": "v.precio_unitario",
    "costo": "v.costo_unitario", "total_venta": "(v.cantidad * v.precio_unitario)",
    "ganancia": "(v.cantidad * (v.precio_unitario - v.costo_unitario))",
}


def _order_ventas(orden, asc):
    if orden in _ORDEN_VENTAS:
        d = "ASC" if asc else "DESC"
        return f"{_ORDEN_VENTAS[orden]} {d}, v.id {d}"
    return "v.fecha DESC, v.id DESC"  # por defecto: lo más reciente primero


def _where_ventas(texto, categoria_id, desde=None, hasta=None):
    w, p = " WHERE 1=1", []
    if texto:
        w += " AND (p.nombre LIKE ? OR v.cliente LIKE ?)"
        p += [f"%{texto}%"] * 2
    if categoria_id:
        w += " AND p.categoria_id = ?"
        p.append(categoria_id)
    if desde:
        w += " AND v.fecha >= ?"
        p.append(desde)
    if hasta:
        w += " AND v.fecha <= ?"
        p.append(hasta)
    return w, p


def listar_ventas(con, texto="", categoria_id=None, desde=None, hasta=None,
                  orden=None, asc=True, limite=None, offset=0):
    w, p = _where_ventas(texto, categoria_id, desde, hasta)
    q = _SEL_VENTA + w + " ORDER BY " + _order_ventas(orden, asc)
    if limite:
        q += " LIMIT ? OFFSET ?"
        p += [limite, offset]
    return con.execute(q, p).fetchall()


def contar_ventas(con, texto="", categoria_id=None, desde=None, hasta=None):
    w, p = _where_ventas(texto, categoria_id, desde, hasta)
    return con.execute("SELECT COUNT(*)" + _FROM_VENTAS + w, p).fetchone()[0]


def totales_ventas(con, texto="", categoria_id=None, desde=None, hasta=None):
    """(total vendido, costo de lo vendido) de TODAS las ventas que cumplen los filtros."""
    w, p = _where_ventas(texto, categoria_id, desde, hasta)
    r = con.execute("SELECT COALESCE(SUM(v.cantidad * v.precio_unitario), 0), "
                    "COALESCE(SUM(v.cantidad * v.costo_unitario), 0)" + _FROM_VENTAS + w, p).fetchone()
    return r[0], r[1]


def rango_fechas_ventas(con):
    r = con.execute("SELECT MIN(fecha), MAX(fecha) FROM ventas").fetchone()
    return r[0], r[1]


def obtener_venta(con, venta_id):
    return con.execute(_SEL_VENTA + " WHERE v.id=?", (venta_id,)).fetchone()


def productos_vendibles(con, incluir_id=None):
    """Productos activos con su precio y costo actuales (más el de la venta que se edita, aunque esté inactivo)."""
    return con.execute("""SELECT p.id, p.nombre, COALESCE(c.nombre,'') AS categoria, v.costo, v.precio_venta
                          FROM productos p
                          JOIN v_costo_producto v ON v.producto_id = p.id
                          LEFT JOIN categorias_producto c ON c.id = p.categoria_id
                          WHERE p.activo = 1 OR p.id = ?
                          ORDER BY LOWER(p.nombre)""", (incluir_id,)).fetchall()


def guardar_venta(con, fecha_iso, producto_id, cantidad, precio, costo, cliente=None, notas=None, venta_id=None):
    if not fecha_iso:
        raise ValueError("La fecha de venta es obligatoria.")
    if producto_id is None:
        raise ValueError("Debes elegir un producto.")
    if cantidad is None or int(cantidad) <= 0:
        raise ValueError("La cantidad debe ser mayor que 0.")
    if precio is None or precio < 0:
        raise ValueError("El precio de venta es obligatorio y no puede ser negativo.")
    datos = (fecha_iso, producto_id, int(cantidad), precio, costo or 0, cliente, notas)
    with con:
        if venta_id is None:
            return con.execute("INSERT INTO ventas(fecha,producto_id,cantidad,precio_unitario,costo_unitario,cliente,notas) "
                               "VALUES (?,?,?,?,?,?,?)", datos).lastrowid
        con.execute("UPDATE ventas SET fecha=?,producto_id=?,cantidad=?,precio_unitario=?,costo_unitario=?,"
                    "cliente=?,notas=? WHERE id=?", datos + (venta_id,))
        return venta_id


def eliminar_venta(con, venta_id):
    with con:
        con.execute("DELETE FROM ventas WHERE id=?", (venta_id,))


# ======================= REPORTES =======================
def rango_fechas_reporte(con):
    r = con.execute("SELECT MIN(f), MAX(f) FROM (SELECT fecha AS f FROM ventas UNION ALL SELECT fecha FROM compras)").fetchone()
    return r[0], r[1]


def _periodo_sql(agrupar, col):
    plantilla = {"mes": "substr({c}, 1, 7)", "semana": "strftime('%Y-S%W', {c})", "dia": "{c}"}[agrupar]
    return plantilla.format(c=col)


def _rango_sql(col, desde, hasta):
    w, p = "", []
    if desde:
        w += f" AND {col} >= ?"
        p.append(desde)
    if hasta:
        w += f" AND {col} <= ?"
        p.append(hasta)
    return w, p


def resumen_periodos(con, desde=None, hasta=None, agrupar="mes"):
    """Por período (mes / semana / día): ingresos, costo de lo vendido, ganancia bruta, gasto en insumos,
    gasto operativo (compras de categorías ocultas en productos) y resultado = ingresos − gastos."""
    wv, pv = _rango_sql("v.fecha", desde, hasta)
    wc, pc = _rango_sql("c.fecha", desde, hasta)
    filas = {}

    def fila(per):
        return filas.setdefault(per, {"periodo": per, "ingresos": 0.0, "costo_ventas": 0.0,
                                      "gasto_insumos": 0.0, "gasto_operativo": 0.0})
    for r in con.execute(f"""SELECT {_periodo_sql(agrupar, 'v.fecha')} AS per,
                                    SUM(v.cantidad * v.precio_unitario) AS ing, SUM(v.cantidad * v.costo_unitario) AS cos
                             FROM ventas v WHERE 1=1 {wv} GROUP BY per""", pv):
        f = fila(r["per"]); f["ingresos"], f["costo_ventas"] = r["ing"], r["cos"]
    for r in con.execute(f"""SELECT {_periodo_sql(agrupar, 'c.fecha')} AS per,
                                    SUM(CASE WHEN COALESCE(cat.excluir_productos,0) = 0 THEN c.paquetes * c.costo_paquete ELSE 0 END) AS ins,
                                    SUM(CASE WHEN COALESCE(cat.excluir_productos,0) = 1 THEN c.paquetes * c.costo_paquete ELSE 0 END) AS ope
                             FROM compras c JOIN insumos i ON i.id = c.insumo_id
                             LEFT JOIN categorias cat ON cat.id = i.categoria_id
                             WHERE 1=1 {wc} GROUP BY per""", pc):
        f = fila(r["per"]); f["gasto_insumos"], f["gasto_operativo"] = r["ins"], r["ope"]
    wg, pg = _rango_sql("g.fecha", desde, hasta)  # + lo registrado en la pestaña Gasto Operativo
    for r in con.execute(f"""SELECT {_periodo_sql(agrupar, 'g.fecha')} AS per, SUM(g.monto) AS m
                             FROM gastos g WHERE 1=1 {wg} GROUP BY per""", pg):
        fila(r["per"])["gasto_operativo"] += r["m"]
    salida = []
    for per in sorted(filas):
        f = filas[per]
        f["ganancia"] = f["ingresos"] - f["costo_ventas"]
        f["resultado"] = f["ingresos"] - f["gasto_insumos"] - f["gasto_operativo"]
        salida.append(f)
    return salida


def productos_top(con, desde=None, hasta=None, limite=8):
    """Productos más rentables (por ganancia) en el rango de fechas."""
    w, p = _rango_sql("v.fecha", desde, hasta)
    return con.execute(f"""SELECT p.nombre, SUM(v.cantidad) AS unidades,
                                  SUM(v.cantidad * v.precio_unitario) AS ingresos,
                                  SUM(v.cantidad * (v.precio_unitario - v.costo_unitario)) AS ganancia
                           FROM ventas v JOIN productos p ON p.id = v.producto_id
                           WHERE 1=1 {w} GROUP BY p.id ORDER BY ganancia DESC LIMIT ?""", p + [limite]).fetchall()


# ======================= MAESTRO DE INSUMOS =======================
def guardar_insumo_maestro(con, nombre, descripcion, categoria_id, insumo_id=None):
    """Alta/edición de la identidad del insumo (nombre, descripción, categoría).
    No toca el costo: un insumo nuevo queda con cantidad 0 y precio 0 hasta definirlo en la pestaña Insumos."""
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del insumo no puede estar vacío.")
    if categoria_id is None:
        raise ValueError("La categoría es obligatoria.")
    with con:
        if insumo_id is None:
            return con.execute(f"INSERT INTO insumos(nombre,descripcion,categoria_id,cantidad,precio_unitario,actualizado) "
                               f"VALUES (?,?,?,0,0,{AHORA})", (nombre, descripcion, categoria_id)).lastrowid
        con.execute(f"UPDATE insumos SET nombre=?,descripcion=?,categoria_id=?,actualizado={AHORA} WHERE id=?",
                    (nombre, descripcion, categoria_id, insumo_id))
        return insumo_id


# ======================= GASTO OPERATIVO =======================
_SEL_GASTO = """SELECT g.id, g.fecha, g.concepto, g.categoria_id, COALESCE(c.nombre,'') AS categoria,
                       g.monto, COALESCE(g.notas,'') AS notas
                FROM gastos g LEFT JOIN categorias_gasto c ON c.id = g.categoria_id"""

_ORDEN_GASTOS = {"fecha": "g.fecha", "concepto": "LOWER(g.concepto)",
                 "categoria": "LOWER(COALESCE(c.nombre,''))", "monto": "g.monto"}


def _order_gastos(orden, asc):
    if orden in _ORDEN_GASTOS:
        d = "ASC" if asc else "DESC"
        return f"{_ORDEN_GASTOS[orden]} {d}, g.id {d}"
    return "g.fecha DESC, g.id DESC"  # por defecto: lo más reciente primero


def _where_gastos(texto, categoria_id, desde=None, hasta=None):
    w, p = " WHERE 1=1", []
    if texto:
        w += " AND (g.concepto LIKE ? OR g.notas LIKE ?)"
        p += [f"%{texto}%"] * 2
    if categoria_id:
        w += " AND g.categoria_id = ?"
        p.append(categoria_id)
    if desde:
        w += " AND g.fecha >= ?"
        p.append(desde)
    if hasta:
        w += " AND g.fecha <= ?"
        p.append(hasta)
    return w, p


def listar_gastos(con, texto="", categoria_id=None, desde=None, hasta=None,
                  orden=None, asc=True, limite=None, offset=0):
    w, p = _where_gastos(texto, categoria_id, desde, hasta)
    q = _SEL_GASTO + w + " ORDER BY " + _order_gastos(orden, asc)
    if limite:
        q += " LIMIT ? OFFSET ?"
        p += [limite, offset]
    return con.execute(q, p).fetchall()


def contar_gastos(con, texto="", categoria_id=None, desde=None, hasta=None):
    w, p = _where_gastos(texto, categoria_id, desde, hasta)
    return con.execute("SELECT COUNT(*) FROM gastos g" + w, p).fetchone()[0]


def total_gastos(con, texto="", categoria_id=None, desde=None, hasta=None):
    """Suma de TODOS los gastos que cumplen los filtros (no solo los de la página visible)."""
    w, p = _where_gastos(texto, categoria_id, desde, hasta)
    return con.execute("SELECT COALESCE(SUM(g.monto), 0) FROM gastos g" + w, p).fetchone()[0]


def rango_fechas_gastos(con):
    r = con.execute("SELECT MIN(fecha), MAX(fecha) FROM gastos").fetchone()
    return r[0], r[1]


def obtener_gasto(con, gasto_id):
    return con.execute(_SEL_GASTO + " WHERE g.id=?", (gasto_id,)).fetchone()


def guardar_gasto(con, fecha_iso, concepto, categoria_id, monto, notas=None, gasto_id=None):
    concepto = (concepto or "").strip()
    if not fecha_iso:
        raise ValueError("La fecha del gasto es obligatoria.")
    if not concepto:
        raise ValueError("El concepto no puede estar vacío.")
    if categoria_id is None:
        raise ValueError("La categoría es obligatoria.")
    if monto is None or monto <= 0:
        raise ValueError("El monto debe ser mayor que 0.")
    datos = (fecha_iso, concepto, categoria_id, monto, notas)
    with con:
        if gasto_id is None:
            return con.execute("INSERT INTO gastos(fecha,concepto,categoria_id,monto,notas) VALUES (?,?,?,?,?)",
                               datos).lastrowid
        con.execute("UPDATE gastos SET fecha=?,concepto=?,categoria_id=?,monto=?,notas=? WHERE id=?",
                    datos + (gasto_id,))
        return gasto_id


def eliminar_gasto(con, gasto_id):
    with con:
        con.execute("DELETE FROM gastos WHERE id=?", (gasto_id,))
