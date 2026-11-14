"""Florería - Histórico de compras, Insumos y Productos (PySide6)."""
import json
import math
import sys

from PySide6.QtCore import QDate, QEvent, QPropertyAnimation, QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QImage, QPalette, QPixmap,
                           QRegularExpressionValidator)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QFrame, QGraphicsOpacityEffect, QComboBox, QCompleter, QDateEdit, QDialog,
    QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QRadioButton, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

import db

COL = {}  # colores del tema activo (se llena en aplicar_tema)


# ----------------------------- formato -----------------------------
def fmt_money(v):
    return f"S/ {v:,.2f}"


def fmt_unit(v):
    """Precio unitario: mínimo 2 decimales, hasta 5 si hacen falta (p. ej. 1.04167)."""
    s = f"{v:,.5f}"
    while s.endswith("0") and len(s.split(".")[1]) > 2:
        s = s[:-1]
    return s


def fmt_cant(v):
    """Cantidad sin ceros sobrantes: 2 -> '2', 1.5 -> '1.5'."""
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def fmt_unit_sol(v):
    return "S/ " + fmt_unit(v)


def fmt_precio5(v):
    return f"S/ {v:,.5f}"


def fmt_fecha(iso):
    return QDate.fromString(iso, "yyyy-MM-dd").toString("dd/MM/yyyy")


# ----------------------------- tablas -----------------------------
def celda(texto, derecha=False, dato=None, tooltip=None):
    it = QTableWidgetItem(str(texto))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if derecha:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if dato is not None:
        it.setData(Qt.UserRole, dato)  # la columna N° guarda aquí el ID real del registro
    if tooltip:
        it.setToolTip(tooltip)
    return it


def crear_tabla(columnas):
    t = QTableWidget(0, len(columnas))
    t.setHorizontalHeaderLabels(columnas)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setWordWrap(False)  # el texto largo se corta con "…"
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    return t


def fila_seleccionada(tabla):
    items = tabla.selectedItems()
    return items[0].row() if items else -1


def id_seleccionado(tabla, col=0):
    """ID del registro de la fila seleccionada (se guarda en la celda de la columna N°)."""
    r = fila_seleccionada(tabla)
    if r < 0 or tabla.item(r, col) is None:
        return None
    return tabla.item(r, col).data(Qt.UserRole)


def seleccionar_id(tabla, id_, col=0):
    for r in range(tabla.rowCount()):
        it = tabla.item(r, col)
        if it is not None and it.data(Qt.UserRole) == id_:
            tabla.selectRow(r)
            return


# ----------------------------- mensajes -----------------------------
def aviso(parent, texto):
    QMessageBox.warning(parent, "Florería", texto)


def confirmar(parent, texto, titulo="Confirmar"):
    m = QMessageBox(QMessageBox.Question, titulo, texto, parent=parent)
    si = m.addButton("Sí", QMessageBox.YesRole)
    no = m.addButton("No", QMessageBox.NoRole)
    m.setDefaultButton(no)  # por seguridad, Enter = No
    m.exec()
    return m.clickedButton() is si


def marcar(widget, ok):
    """Borde rojo en los campos obligatorios que faltan."""
    widget.setStyleSheet("" if ok else "border: 1px solid #D32F2F;")


def validar_obligatorios(parent, campos):
    """campos: [(etiqueta, widget, ok)]. Marca los que faltan y avisa. Devuelve True si todo está completo."""
    faltan = []
    for etiqueta, w, ok in campos:
        marcar(w, ok)
        if not ok:
            faltan.append(etiqueta)
    if faltan:
        aviso(parent, "Completa los campos obligatorios:\n\n• " + "\n• ".join(faltan))
    return not faltan


# ----------------------------- widgets reutilizables -----------------------------
class _RuedaSoloConFoco:
    """Mixin: la rueda del mouse solo cambia el valor si el control está activo (lo tocaste con un clic).
    Si no, el evento pasa a la tabla o ventana de abajo y se hace scroll normal."""

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class ComboBuscable(_RuedaSoloConFoco, QComboBox):
    """Desplegable con buscador: se escribe y filtra las opciones que CONTIENEN el texto."""

    def __init__(self, placeholder="Escribe para buscar…", parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)  # pasar el mouse + rueda no activa el control
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        comp = self.completer()
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        self.lineEdit().setPlaceholderText(placeholder)
        self.lineEdit().editingFinished.connect(self._validar)

    def _validar(self):
        """Si lo escrito no coincide con una opción, vuelve a la opción actual."""
        idx = self.findText(self.currentText().strip(), Qt.MatchFixedString)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setEditText(self.itemText(self.currentIndex()) if self.currentIndex() >= 0 else "")


class FechaEdit(_RuedaSoloConFoco, QDateEdit):
    """Selector de fecha con calendario desplegable (dd/MM/yyyy)."""

    def __init__(self, fecha=None):
        super().__init__(fecha or QDate.currentDate())
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd/MM/yyyy")


def resaltar(widget, activo):
    """Pinta el campo de color ladrillo (cambio sin guardar) o lo devuelve a su color normal.
    El color sale de la hoja de estilos (propiedad 'cambiado'), así que se puede cambiar en tema.json."""
    if bool(widget.property("cambiado")) == activo:
        return
    for w in [widget] + widget.findChildren(QLineEdit):  # los combos y fechas llevan un campo de texto dentro
        w.setProperty("cambiado", activo)
        st = w.style()
        st.unpolish(w)
        st.polish(w)
        w.update()


def _senal_cambio(w):
    if isinstance(w, (QLineEdit, QPlainTextEdit)):
        return w.textChanged
    if isinstance(w, QComboBox):
        return w.currentIndexChanged
    if isinstance(w, QDateEdit):
        return w.dateChanged
    if isinstance(w, QCheckBox):
        return w.toggled
    return None


class SeguimientoCambios:
    """Modo EDICIÓN: guarda el valor original de cada campo y pinta de ladrillo los que cambian
    (si vuelves al valor original, se despinta). En 'Nuevo' no se activa, así que allí nunca se resalta."""

    def __init__(self, campos):  # campos: [(widget, función que devuelve su valor)]
        self.campos, self.base = campos, None

    def activar(self):
        self.base = [f() for _, f in self.campos]
        for w, _ in self.campos:
            senal = _senal_cambio(w)
            if senal is not None:
                senal.connect(lambda *_: self.actualizar())

    def actualizar(self):
        if self.base is None:
            return
        for (w, f), b in zip(self.campos, self.base):
            resaltar(w, f() != b)


def corto(texto, n=40):
    t = " ".join(str(texto).split())
    if not t:
        return "(vacío)"
    return "«" + (t if len(t) <= n else t[:n - 1] + "…") + "»"


def _dif(lista, etiqueta, antes, despues, fmt=str):
    if antes != despues:
        lista.append(f"{etiqueta}: {fmt(antes)} → {fmt(despues)}")


def _dif_num(lista, etiqueta, antes, despues, fmt, tol=1e-9):
    if abs((antes or 0) - (despues or 0)) > tol:
        lista.append(f"{etiqueta}: {fmt(antes or 0)} → {fmt(despues or 0)}")


def resumen_final(titulo, nombre, cambios):
    """(título, líneas) para el aviso emergente; si no cambió nada, lo dice."""
    if cambios:
        return f"{titulo}: «{nombre}»", cambios
    return f"Sin cambios: «{nombre}»", ["No modificaste ningún dato."]


class Toast(QFrame):
    """Aviso flotante en la esquina inferior derecha: muestra lo que se modificó, dura 20 segundos
    y se desvanece. Un clic lo cierra antes."""
    DURACION_MS = 20000

    def __init__(self, ventana, titulo, lineas):
        super().__init__(ventana)
        self.ventana = ventana
        self._saliendo = False
        self.setObjectName("toast")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(430)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        t = QLabel(titulo)
        t.setObjectName("toast_titulo")
        t.setWordWrap(True)
        lay.addWidget(t)
        for linea in lineas:
            lb = QLabel("• " + linea)
            lb.setWordWrap(True)
            lay.addWidget(lb)
        pie = QLabel("Se cierra solo en 20 segundos · clic para cerrar")
        pie.setObjectName("toast_pie")
        lay.addWidget(pie)

        self.efecto = QGraphicsOpacityEffect(self)
        self.efecto.setOpacity(0.0)
        self.setGraphicsEffect(self.efecto)
        self.anim = QPropertyAnimation(self.efecto, b"opacity", self)
        self.anim.finished.connect(self._fin_animacion)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._desvanecer)
        ventana.installEventFilter(self)  # para reubicarlo si cambian el tamaño de la ventana

        self.adjustSize()
        self._posicionar()
        self.show()
        self.raise_()
        self._animar(0.0, 1.0, 250)
        self.timer.start(self.DURACION_MS)

    def _animar(self, desde, hasta, ms):
        self.anim.stop()
        self.anim.setDuration(ms)
        self.anim.setStartValue(desde)
        self.anim.setEndValue(hasta)
        self.anim.start()

    def _desvanecer(self):
        self._saliendo = True
        self._animar(1.0, 0.0, 1500)  # desvanecimiento final

    def _fin_animacion(self):
        if self._saliendo:
            self.cerrar()

    def _posicionar(self):
        self.move(self.ventana.width() - self.width() - 24, self.ventana.height() - self.height() - 24)

    def eventFilter(self, obj, ev):
        if obj is self.ventana and ev.type() == QEvent.Resize:
            self._posicionar()
        return False

    def mousePressEvent(self, ev):
        self.cerrar()

    def cerrar(self):
        self.timer.stop()
        self.anim.stop()
        self.ventana.removeEventFilter(self)
        self.hide()
        self.deleteLater()


def mostrar_toast(origen, titulo, lineas):
    ventana = origen.window()
    anterior = getattr(ventana, "_toast", None)
    if anterior is not None:
        try:
            anterior.cerrar()
        except RuntimeError:  # ya se había cerrado y eliminado
            pass
    ventana._toast = Toast(ventana, titulo, lineas)


class CampoNumero(QLineEdit):
    """Campo numérico que distingue 'vacío' de '0' (necesario para campos obligatorios)."""

    def __init__(self, decimales=2, placeholder=""):
        super().__init__()
        self.decimales = decimales
        patron = r"^\d{0,9}$" if decimales == 0 else r"^\d{0,9}([.,]\d{0,%d})?$" % decimales
        self.setValidator(QRegularExpressionValidator(QRegularExpression(patron), self))
        self.setPlaceholderText(placeholder)
        self.setAlignment(Qt.AlignRight)

    def valor(self):
        t = self.text().strip().replace(",", ".")
        try:
            return float(t) if t else None
        except ValueError:
            return None

    def poner(self, v):
        if v is None:
            self.clear()
            return
        s = f"{v:.{self.decimales}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        self.setText(s)


class Paginador(QWidget):
    """Barra de paginado: filas por página (25 por defecto) y navegación."""
    cambiado = Signal()

    def __init__(self, por_pagina=25):
        super().__init__()
        self.pagina, self.total = 1, 0
        self.b_pri, self.b_ant, self.b_sig, self.b_ult = (QPushButton(t) for t in ("«", "‹", "›", "»"))
        for b in (self.b_pri, self.b_ant, self.b_sig, self.b_ult):
            b.setFixedWidth(36)
            b.setStyleSheet("padding: 6px 0;")
        self.lbl_rango, self.lbl_pag = QLabel(), QLabel()
        self.combo = ComboBuscable(placeholder="")
        for n in (10, 25, 50, 100):
            self.combo.addItem(str(n), n)
        self.combo.setCurrentIndex(self.combo.findData(por_pagina))
        self.combo.setFixedWidth(80)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.lbl_rango)
        lay.addStretch()
        lay.addWidget(QLabel("Filas por página:"))
        lay.addWidget(self.combo)
        lay.addSpacing(16)
        for w in (self.b_pri, self.b_ant, self.lbl_pag, self.b_sig, self.b_ult):
            lay.addWidget(w)
        self.b_pri.clicked.connect(lambda: self._ir(1))
        self.b_ant.clicked.connect(lambda: self._ir(self.pagina - 1))
        self.b_sig.clicked.connect(lambda: self._ir(self.pagina + 1))
        self.b_ult.clicked.connect(lambda: self._ir(self.n_paginas()))
        self.combo.currentIndexChanged.connect(self._cambio_tamano)
        self.actualizar(0)

    @property
    def limite(self):
        return self.combo.currentData() or 25

    @property
    def offset(self):
        return (self.pagina - 1) * self.limite

    def n_paginas(self):
        return max(1, math.ceil(self.total / self.limite))

    def reiniciar(self):
        self.pagina = 1

    def actualizar(self, total):
        """Se llama tras contar los registros; corrige la página si ya no existe."""
        self.total = total
        self.pagina = min(self.pagina, self.n_paginas())
        ini = self.offset + 1 if total else 0
        fin = min(self.offset + self.limite, total)
        self.lbl_rango.setText(f"Mostrando {ini}–{fin} de {total}")
        self.lbl_pag.setText(f"Página {self.pagina} de {self.n_paginas()}")
        self.b_pri.setEnabled(self.pagina > 1)
        self.b_ant.setEnabled(self.pagina > 1)
        self.b_sig.setEnabled(self.pagina < self.n_paginas())
        self.b_ult.setEnabled(self.pagina < self.n_paginas())

    def _ir(self, p):
        p = max(1, min(p, self.n_paginas()))
        if p != self.pagina:
            self.pagina = p
            self.cambiado.emit()

    def _cambio_tamano(self, _=None):
        self.pagina = 1
        self.cambiado.emit()


# ----------------------------- categorías -----------------------------
class CategoriasDialog(QDialog):
    """Administra categorías. tipo = 'insumo' o 'producto'."""

    def __init__(self, con, parent=None, tipo="insumo"):
        super().__init__(parent)
        self.con, self.tipo = con, tipo
        self.setWindowTitle(f"Categorías de {tipo}s")
        self.setMinimumSize(400, 400)
        self.lista = QListWidget()
        b_add, b_ren, b_del = QPushButton("Agregar"), QPushButton("Renombrar"), QPushButton("Eliminar")
        b_add.clicked.connect(self.agregar)
        b_ren.clicked.connect(self.renombrar)
        b_del.clicked.connect(self.eliminar)
        fila = QHBoxLayout()
        for b in (b_add, b_ren, b_del):
            fila.addWidget(b)
        cerrar = QDialogButtonBox(QDialogButtonBox.Close)
        cerrar.rejected.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(self.lista)
        lay.addLayout(fila)
        if tipo == "insumo":
            b_vis = QPushButton("Ocultar / mostrar en productos")
            b_vis.clicked.connect(self.alternar_visibilidad)
            nota = QLabel("Las categorías «ocultas en productos» (por ejemplo Gasto Operativo) no ofrecen sus "
                          "insumos al armar un producto. Siguen disponibles en Insumos y en Histórico de Compras.")
            nota.setObjectName("nota")
            nota.setWordWrap(True)
            lay.addWidget(b_vis)
            lay.addWidget(nota)
        lay.addWidget(cerrar)
        self.recargar()

    def recargar(self, seleccionar=None):
        self.lista.clear()
        for c in db.listar_categorias(self.con, self.tipo):
            it = QListWidgetItem(c["nombre"] + ("      (oculta en productos)" if c["excluir_productos"] else ""))
            it.setData(Qt.UserRole, c["id"])
            it.setData(Qt.UserRole + 1, c["nombre"])
            it.setData(Qt.UserRole + 2, bool(c["excluir_productos"]))
            self.lista.addItem(it)
            if c["id"] == seleccionar:
                self.lista.setCurrentItem(it)

    def agregar(self):
        txt, ok = QInputDialog.getText(self, "Nueva categoría", "Nombre:")
        if ok:
            try:
                db.crear_categoria(self.con, txt, self.tipo)
            except ValueError as e:
                return aviso(self, str(e))
            self.recargar()

    def renombrar(self):
        it = self.lista.currentItem()
        if not it:
            return
        txt, ok = QInputDialog.getText(self, "Renombrar categoría", "Nombre:", text=it.data(Qt.UserRole + 1))
        if ok:
            try:
                db.renombrar_categoria(self.con, it.data(Qt.UserRole), txt, self.tipo)
            except ValueError as e:
                return aviso(self, str(e))
            self.recargar(it.data(Qt.UserRole))

    def eliminar(self):
        it = self.lista.currentItem()
        if it and confirmar(self, f"¿Eliminar la categoría «{it.data(Qt.UserRole + 1)}»?"):
            try:
                db.eliminar_categoria(self.con, it.data(Qt.UserRole), self.tipo)
            except ValueError as e:
                return aviso(self, str(e))
            self.recargar()

    def alternar_visibilidad(self):
        it = self.lista.currentItem()
        if not it:
            return aviso(self, "Selecciona una categoría.")
        cid = it.data(Qt.UserRole)
        db.ocultar_categoria_en_productos(self.con, cid, not it.data(Qt.UserRole + 2))
        self.recargar(cid)


def llenar_categorias(combo, con, tipo, seleccionar=None, vacio="", filtro=False):
    """Carga un ComboBuscable con las categorías. vacio = texto de la 1ª opción (sin valor)."""
    combo.clear()
    combo.addItem(vacio, None)
    for c in db.listar_categorias(con, tipo):
        combo.addItem(c["nombre"], c["id"])
    idx = combo.findData(seleccionar) if seleccionar is not None else 0
    combo.setCurrentIndex(max(idx, 0))


# ----------------------------- insumos -----------------------------
class FiltroFechas:
    """Mixin: filtro Desde / Hasta (cada extremo se activa con su casilla). La clase debe definir
    _rango_fechas_datos() -> (fecha mínima, fecha máxima) en ISO, y refrescar(reiniciar)."""

    def _init_fechas(self):
        self.chk_desde, self.chk_hasta = QCheckBox("Desde"), QCheckBox("Hasta")
        self.fecha_desde, self.fecha_hasta = FechaEdit(), FechaEdit()
        self.fecha_desde.setEnabled(False)
        self.fecha_hasta.setEnabled(False)

    def _fila_fechas(self):
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Fecha:"))
        for w in (self.chk_desde, self.fecha_desde, self.chk_hasta, self.fecha_hasta):
            fila.addWidget(w)
        return fila

    def _conectar_fechas(self):
        self.chk_desde.toggled.connect(lambda on: self._activar_fecha(self.fecha_desde, on))
        self.chk_hasta.toggled.connect(lambda on: self._activar_fecha(self.fecha_hasta, on))
        self.fecha_desde.dateChanged.connect(lambda _: self._fecha_cambiada(self.chk_desde))
        self.fecha_hasta.dateChanged.connect(lambda _: self._fecha_cambiada(self.chk_hasta))

    def _fechas_por_defecto(self):
        """Al activar un extremo, parte del rango real de los datos (así no se vacía la lista de golpe)."""
        mn, mx = self._rango_fechas_datos()
        if not self.chk_desde.isChecked():
            d = QDate.fromString(mn or "", "yyyy-MM-dd")
            self.fecha_desde.setDate(d if d.isValid() else QDate.currentDate())
        if not self.chk_hasta.isChecked():
            d = QDate.fromString(mx or "", "yyyy-MM-dd")
            self.fecha_hasta.setDate(d if d.isValid() else QDate.currentDate())

    def _activar_fecha(self, edit, activo):
        edit.setEnabled(activo)
        self.refrescar(True)

    def _fecha_cambiada(self, chk):
        if chk.isChecked():
            self.refrescar(True)

    def _desde_hasta(self):
        desde = self.fecha_desde.date().toString("yyyy-MM-dd") if self.chk_desde.isChecked() else None
        hasta = self.fecha_hasta.date().toString("yyyy-MM-dd") if self.chk_hasta.isChecked() else None
        return desde, hasta

    def _reiniciar_fechas(self):
        """Desactiva ambos extremos (sin refrescar: lo hace quien llama)."""
        for w in (self.chk_desde, self.chk_hasta):
            w.blockSignals(True)
            w.setChecked(False)
            w.blockSignals(False)
        self.fecha_desde.setEnabled(False)
        self.fecha_hasta.setEnabled(False)
        self._fechas_por_defecto()


class OrdenPorCabecera:
    """Mixin: ordena por clic en la cabecera. El orden se aplica en la base de datos, así que vale
    para todas las páginas. La clase define ORDEN_COL = {n° de columna: clave} y usa self.tabla / refrescar()."""
    ORDEN_COL = {}

    def _init_orden(self):
        self.orden, self.asc = None, True
        h = self.tabla.horizontalHeader()
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(False)
        h.sectionClicked.connect(self.ordenar_por)

    def ordenar_por(self, col):
        clave = self.ORDEN_COL.get(col)
        if not clave:
            return
        self.asc = not self.asc if self.orden == clave else True
        self.orden = clave
        h = self.tabla.horizontalHeader()
        h.setSortIndicatorShown(True)
        h.setSortIndicator(col, Qt.AscendingOrder if self.asc else Qt.DescendingOrder)
        self.refrescar(True)

    def restablecer_orden(self):
        self.orden, self.asc = None, True
        self.tabla.horizontalHeader().setSortIndicatorShown(False)


class InsumoDialog(QDialog):
    def __init__(self, con, insumo=None, parent=None):
        super().__init__(parent)
        self.con, self.insumo = con, insumo
        self.guardado_id = None
        self.resumen = None  # (título, líneas) para el aviso emergente cuando se edita
        self.setWindowTitle("Editar insumo" if insumo else "Nuevo insumo")
        self.setMinimumWidth(500)

        self.nombre = QLineEdit()
        self.nombre.setPlaceholderText("Ej. Rosa")
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Ej. Rosas de vivero de mercado central")
        self.desc.setFixedHeight(80)
        self.cat = ComboBuscable("Selecciona o escribe para buscar…")
        b_cat = QPushButton("+ Nueva categoría")
        b_cat.clicked.connect(self.nueva_categoria)
        fila_cat = QHBoxLayout()
        fila_cat.addWidget(self.cat, 1)
        fila_cat.addWidget(b_cat)
        self.cant = CampoNumero(0, "Ej. 24")
        self.cant.setMaximumWidth(160)
        self.total = CampoNumero(2, "Ej. 25.00")
        self.total.setMaximumWidth(160)
        fila_total = QHBoxLayout()
        fila_total.addWidget(QLabel("S/"))
        fila_total.addWidget(self.total)
        fila_total.addStretch()
        self.unitario = QLineEdit()  # solo lectura: se calcula solo
        self.unitario.setReadOnly(True)
        self.unitario.setAlignment(Qt.AlignRight)
        self.unitario.setPlaceholderText("Se calcula solo")
        self.unitario.setMaximumWidth(160)
        fila_unit = QHBoxLayout()
        fila_unit.addWidget(QLabel("S/"))
        fila_unit.addWidget(self.unitario)
        fila_unit.addStretch()

        form = QFormLayout()
        form.addRow("Nombre del insumo *", self.nombre)
        form.addRow("Descripción", self.desc)
        form.addRow("Categoría *", fila_cat)
        form.addRow("Cantidad *", self.cant)
        form.addRow("Precio total *", fila_total)
        form.addRow("Precio unitario (calculado)", fila_unit)
        nota = QLabel("* Campos obligatorios.   Precio unitario = Precio total ÷ Cantidad "
                      "(ej. paquete de 24 rosas a S/ 25).")
        nota.setObjectName("nota")
        nota.setWordWrap(True)
        botones = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(nota)
        lay.addWidget(botones)

        llenar_categorias(self.cat, con, "insumo", insumo["categoria_id"] if insumo else None)
        self.seg = SeguimientoCambios([
            (self.nombre, lambda: self.nombre.text()),
            (self.desc, lambda: self.desc.toPlainText()),
            (self.cat, lambda: self.cat.currentData()),
            (self.cant, lambda: self.cant.text()),
            (self.total, lambda: self.total.text()),
        ])
        self._orig = None
        if insumo:
            self.nombre.setText(insumo["nombre"])
            self.desc.setPlainText(insumo["descripcion"])
            cant = insumo["cantidad"] if insumo["cantidad"] and insumo["cantidad"] > 0 else 1
            self.cant.poner(cant)  # si no tenía cantidad, se toma como 1 unidad para no perder su precio
            self.total.poner(round(cant * insumo["precio_unitario"], 2))
            self._orig = (self.cant.text(), self.total.text())
            self._cat_orig = self.cat.currentText()
        self.cant.textChanged.connect(self.recalcular)
        self.total.textChanged.connect(self.recalcular)
        self.recalcular()
        if insumo:
            self.seg.activar()  # el resaltado ladrillo solo existe al EDITAR

    def nueva_categoria(self):
        txt, ok = QInputDialog.getText(self, "Nueva categoría", "Nombre:")
        if ok:
            try:
                cid = db.crear_categoria(self.con, txt, "insumo")
            except ValueError as e:
                return aviso(self, str(e))
            llenar_categorias(self.cat, self.con, "insumo", cid)

    def recalcular(self, *_):
        c, t = self.cant.valor(), self.total.valor()
        if c and t is not None:  # c solo es "verdadero" si no está vacío ni es 0
            self.unitario.setText(fmt_unit(t / c))
        else:
            self.unitario.clear()

    def accept(self):
        c, t = self.cant.valor(), self.total.valor()
        if not validar_obligatorios(self, [
            ("Nombre", self.nombre, bool(self.nombre.text().strip())),
            ("Categoría", self.cat, self.cat.currentData() is not None),
            ("Cantidad", self.cant, c is not None),
            ("Precio total", self.total, t is not None),
        ]):
            return
        if c <= 0:
            marcar(self.cant, False)
            return aviso(self, "La cantidad debe ser mayor que 0.")
        if self.insumo and not confirmar(
                self, f"Vas a modificar el insumo «{self.insumo['nombre']}».\n\n¿Seguro que deseas guardar los cambios?",
                "Confirmar edición"):
            return
        t_escrito = t
        if self.insumo and self._orig == (self.cant.text(), self.total.text()):
            t = self.insumo["precio_unitario"] * c  # Cantidad y Precio total sin tocar: se conserva el precio exacto
        nombre, desc = self.nombre.text().strip(), self.desc.toPlainText().strip()
        try:
            self.guardado_id = db.guardar_insumo(
                self.con, nombre, desc, self.cat.currentData(), c, t, self.insumo["id"] if self.insumo else None)
        except ValueError as e:
            return aviso(self, str(e))
        if self.insumo:  # qué se modificó, para el aviso emergente
            i, cambios = self.insumo, []
            _dif(cambios, "Nombre", i["nombre"], nombre, corto)
            _dif(cambios, "Descripción", i["descripcion"].strip(), desc, corto)
            _dif(cambios, "Categoría", self._cat_orig, self.cat.currentText())
            _dif_num(cambios, "Cantidad", float(self._orig[0]), c, fmt_cant)
            _dif_num(cambios, "Precio total", float(self._orig[1]), t_escrito, fmt_money, 0.005)
            _dif_num(cambios, "Precio unitario", i["precio_unitario"], t / c, fmt_unit_sol, 1e-7)
            self.resumen = resumen_final("Insumo modificado", nombre, cambios)
        super().accept()


class InsumosTab(OrdenPorCabecera, QWidget):
    cambio = Signal()
    COLS = ["N°", "Nombre", "Descripción", "Categoría", "Precio unitario"]
    ORDEN_COL = {1: "nombre", 2: "descripcion", 3: "categoria", 4: "precio_unitario"}

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("Buscar por nombre o descripción…")
        self.filtro = ComboBuscable("Filtrar por categoría…")
        b_nuevo, b_edit, b_del, b_cats = (QPushButton(t) for t in
                                          ("Nuevo", "Editar", "Eliminar", "Categorías…"))
        b_nuevo.clicked.connect(self.nuevo)
        b_edit.clicked.connect(self.editar)
        b_del.clicked.connect(self.eliminar)
        b_cats.clicked.connect(self.categorias)
        barra = QHBoxLayout()
        barra.addWidget(self.buscar, 2)
        barra.addWidget(self.filtro, 1)
        for b in (b_nuevo, b_edit, b_del, b_cats):
            barra.addWidget(b)

        self.tabla = crear_tabla(self.COLS)
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        self.tabla.setColumnWidth(2, 200)
        self._init_orden()
        self.tabla.doubleClicked.connect(lambda _: self.editar())
        self.pag = Paginador()
        self.pag.cambiado.connect(self.refrescar)

        lay = QVBoxLayout(self)
        lay.addLayout(barra)
        lay.addWidget(self.tabla)
        lay.addWidget(self.pag)
        self.recargar_filtro()
        self.buscar.textChanged.connect(lambda _: self.refrescar(True))
        self.filtro.currentIndexChanged.connect(lambda _: self.refrescar(True))
        self.refrescar()

    def al_mostrar(self):
        self.recargar_filtro()  # por si en el maestro se agregaron categorías
        self.refrescar()

    def recargar_filtro(self):
        actual = self.filtro.currentData()
        self.filtro.blockSignals(True)
        llenar_categorias(self.filtro, self.con, "insumo", actual, "Todas las categorías")
        self.filtro.blockSignals(False)

    def refrescar(self, reiniciar=False, seleccionar=None):
        sel = seleccionar if seleccionar is not None else id_seleccionado(self.tabla)
        if reiniciar:
            self.pag.reiniciar()
        texto, cat = self.buscar.text().strip(), self.filtro.currentData()
        self.pag.actualizar(db.contar_insumos(self.con, texto, cat))
        filas = db.listar_insumos(self.con, texto, cat, self.orden, self.asc, self.pag.limite, self.pag.offset)
        self.tabla.setRowCount(len(filas))
        for r, i in enumerate(filas):
            desc = i["descripcion"]
            celdas = [
                celda(self.pag.offset + r + 1, True, dato=i["id"]),
                celda(i["nombre"]),
                celda(desc.replace("\n", " "), tooltip=desc),  # al pasar el mouse se ve la descripción completa
                celda(i["categoria"]),
                celda(fmt_unit_sol(i["precio_unitario"]), True),
            ]
            for c, it in enumerate(celdas):
                self.tabla.setItem(r, c, it)
        if sel is not None:
            seleccionar_id(self.tabla, sel)

    def _tras_guardar(self, dlg):
        self.restablecer_orden()  # lo recién guardado aparece arriba, en la página 1
        self.refrescar(True, dlg.guardado_id)
        self.cambio.emit()
        if dlg.resumen:
            mostrar_toast(self, *dlg.resumen)

    def nuevo(self):
        dlg = InsumoDialog(self.con, parent=self)
        if dlg.exec():
            self._tras_guardar(dlg)

    def editar(self):
        iid = id_seleccionado(self.tabla)
        if iid is None:
            return aviso(self, "Selecciona un insumo.")
        dlg = InsumoDialog(self.con, db.obtener_insumo(self.con, iid), self)
        if dlg.exec():
            self._tras_guardar(dlg)

    def eliminar(self):
        iid = id_seleccionado(self.tabla)
        if iid is None:
            return aviso(self, "Selecciona un insumo.")
        if confirmar(self, "¿Eliminar el insumo seleccionado?"):
            try:
                db.eliminar_insumo(self.con, iid)
            except ValueError as e:
                return aviso(self, str(e))
            self.refrescar()
            self.cambio.emit()

    def categorias(self):
        CategoriasDialog(self.con, self, "insumo").exec()
        self.recargar_filtro()
        self.refrescar(True)
        self.cambio.emit()


# ----------------------------- productos -----------------------------
class ProductoDialog(QDialog):
    COLS = ["N°", "Insumo", "Cantidad", "Precio unit.", "Subtotal"]

    def __init__(self, con, producto=None, parent=None):
        super().__init__(parent)
        self.con, self.producto = con, producto
        self.resumen = None  # (título, líneas) para el aviso emergente cuando se edita
        self._listo = False
        self._receta_orig = {}
        self.setWindowTitle("Editar producto" if producto else "Nuevo producto")
        self.setMinimumSize(780, 620)
        self.insumos = db.listar_insumos_para_producto(con, producto["id"] if producto else None)
        self.precios = {i["id"]: i["precio_unitario"] for i in self.insumos}
        self.nombres = {i["id"]: i["nombre"] for i in self.insumos}

        self.nombre = QLineEdit()
        self.nombre.setPlaceholderText("Ej. Ramo de Hortensias")
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Ej. Ramo de 24 hortensias de vivero")
        self.desc.setFixedHeight(70)
        self.cat = ComboBuscable("Selecciona o escribe para buscar…")
        b_cat = QPushButton("+ Nueva categoría")
        b_cat.clicked.connect(self.nueva_categoria)
        fila_cat = QHBoxLayout()
        fila_cat.addWidget(self.cat, 1)
        fila_cat.addWidget(b_cat)
        self.margen = CampoNumero(2, "Ej. 55")
        self.margen.setMaximumWidth(120)
        fila_m = QHBoxLayout()
        fila_m.addWidget(self.margen)
        fila_m.addWidget(QLabel("%  (el precio de venta se calcula con este margen)"))
        fila_m.addStretch()
        self.activo = QCheckBox("Activo")
        self.activo.setChecked(True)
        form = QFormLayout()
        form.addRow("Nombre del producto *", self.nombre)
        form.addRow("Descripción", self.desc)
        form.addRow("Categoría *", fila_cat)
        form.addRow("Margen de ganancia", fila_m)
        form.addRow("", self.activo)

        self.tabla = QTableWidget(0, len(self.COLS))
        self.tabla.setHorizontalHeaderLabels(self.COLS)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        b_add, b_del = QPushButton("+ Agregar insumo"), QPushButton("Quitar insumo")
        b_add.clicked.connect(lambda: self.agregar_fila())
        b_del.clicked.connect(self.quitar_fila)
        fila_b = QHBoxLayout()
        fila_b.addWidget(QLabel("<b>Insumos que lleva el producto</b>"))
        fila_b.addStretch()
        fila_b.addWidget(b_add)
        fila_b.addWidget(b_del)

        self.lbl_costo, self.lbl_precio, self.lbl_ganancia = QLabel(), QLabel(), QLabel()
        for lbl in (self.lbl_costo, self.lbl_precio, self.lbl_ganancia):
            lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        fila_t = QHBoxLayout()
        fila_t.addWidget(self.lbl_costo)
        fila_t.addStretch()
        fila_t.addWidget(self.lbl_precio)
        fila_t.addStretch()
        fila_t.addWidget(self.lbl_ganancia)
        nota = QLabel("* Campos obligatorios")
        nota.setObjectName("nota")
        botones = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(fila_b)
        lay.addWidget(self.tabla, 1)
        lay.addLayout(fila_t)
        lay.addWidget(nota)
        lay.addWidget(botones)

        llenar_categorias(self.cat, con, "producto", producto["categoria_id"] if producto else None)
        self.seg = SeguimientoCambios([
            (self.nombre, lambda: self.nombre.text()),
            (self.desc, lambda: self.desc.toPlainText()),
            (self.cat, lambda: self.cat.currentData()),
            (self.margen, lambda: self.margen.text()),
            (self.activo, lambda: self.activo.isChecked()),
        ])
        self._texto_margen0 = ""
        if producto:
            self.nombre.setText(producto["nombre"])
            self.desc.setPlainText(producto["descripcion"])
            self.margen.poner(round(producto["margen"], 2))
            self._texto_margen0 = self.margen.text()
            self.activo.setChecked(bool(producto["activo"]))
            self._cat_orig = self.cat.currentText()
            for r in db.obtener_receta(con, producto["id"]):
                self._receta_orig[r["insumo_id"]] = r["cantidad"]
                self.agregar_fila(r["insumo_id"], r["cantidad"])
        self.margen.textChanged.connect(self.recalcular)
        self._listo = True
        self.recalcular()
        if producto:
            self.seg.activar()  # el resaltado ladrillo solo existe al EDITAR

    def nueva_categoria(self):
        txt, ok = QInputDialog.getText(self, "Nueva categoría de producto", "Nombre:")
        if ok:
            try:
                cid = db.crear_categoria(self.con, txt, "producto")
            except ValueError as e:
                return aviso(self, str(e))
            llenar_categorias(self.cat, self.con, "producto", cid)

    def agregar_fila(self, insumo_id=None, cantidad=1.0):
        if not self.insumos:
            return aviso(self, "Primero crea insumos en la pestaña Insumos.")
        r = self.tabla.rowCount()
        self.tabla.insertRow(r)
        combo = ComboBuscable("Busca un insumo…")
        combo.addItem("", None)
        for i in self.insumos:
            combo.addItem(i["nombre"] + (f" — {i['descripcion']}" if i["descripcion"] else ""), i["id"])
        if insumo_id is not None:
            combo.setCurrentIndex(max(combo.findData(insumo_id), 0))
        sp = CampoNumero(2, "0")
        sp.poner(cantidad)
        self.tabla.setCellWidget(r, 1, combo)
        self.tabla.setCellWidget(r, 2, sp)
        for c in (0, 3, 4):
            self.tabla.setItem(r, c, celda("", derecha=True))
        combo.currentIndexChanged.connect(self.recalcular)
        sp.textChanged.connect(self.recalcular)
        self.recalcular()

    def quitar_fila(self):
        r = self.tabla.currentRow()
        if r >= 0:
            self.tabla.removeRow(r)
            self.recalcular()

    def _marcar_receta(self):
        """Al editar: las filas de insumos nuevas o modificadas se pintan de ladrillo."""
        if self.producto is None or not self._listo:
            return
        orig = {(iid, round(q, 4)) for iid, q in self._receta_orig.items()}
        fondo, texto = QBrush(QColor(COL["cambio"])), QBrush(QColor(COL["cambio_texto"]))
        for r in range(self.tabla.rowCount()):
            combo, sp = self.tabla.cellWidget(r, 1), self.tabla.cellWidget(r, 2)
            if combo is None or sp is None:
                continue
            cambiado = (combo.currentData(), round(sp.valor() or 0, 4)) not in orig
            resaltar(combo, cambiado)
            resaltar(sp, cambiado)
            for c in (0, 3, 4):
                it = self.tabla.item(r, c)
                if it is not None:
                    it.setBackground(fondo if cambiado else QBrush())
                    it.setForeground(texto if cambiado else QBrush())

    def recalcular(self, *_):
        costo = 0.0
        for r in range(self.tabla.rowCount()):
            combo, sp = self.tabla.cellWidget(r, 1), self.tabla.cellWidget(r, 2)
            if combo is None or sp is None or self.tabla.item(r, 0) is None:
                continue
            pu = self.precios.get(combo.currentData(), 0.0)
            sub = pu * (sp.valor() or 0)
            costo += sub
            self.tabla.item(r, 0).setText(str(r + 1))  # N° de fila
            self.tabla.item(r, 3).setText(fmt_unit_sol(pu))
            self.tabla.item(r, 4).setText(fmt_money(sub))
        m = self.margen.valor() or 0
        if m < 100:
            precio = costo / (1 - m / 100)
            self.lbl_precio.setText(f"Precio de venta: {fmt_money(precio)}")
            self.lbl_ganancia.setText(f"Ganancia: {fmt_money(precio - costo)}")
        else:
            self.lbl_precio.setText("Precio de venta: —")
            self.lbl_ganancia.setText("Ganancia: —")
        self.lbl_costo.setText(f"Costo total: {fmt_money(costo)}")
        self._marcar_receta()

    def _resumen_cambios(self, nombre, desc, m, activo, receta):
        p, cambios = self.producto, []
        _dif(cambios, "Nombre", p["nombre"], nombre, corto)
        _dif(cambios, "Descripción", p["descripcion"].strip(), desc, corto)
        _dif(cambios, "Categoría", self._cat_orig, self.cat.currentText())
        _dif_num(cambios, "Margen", p["margen"], m, lambda v: f"{v:.1f}%", 0.005)
        _dif(cambios, "Activo", bool(p["activo"]), activo, lambda v: "Sí" if v else "No")
        orig = self._receta_orig
        for iid, q in receta.items():
            nom = self.nombres.get(iid, f"#{iid}")
            if iid not in orig:
                cambios.append(f"Insumo agregado: {nom} (cantidad {fmt_cant(q)})")
            elif abs(orig[iid] - q) > 1e-9:
                cambios.append(f"Insumo {nom}: cantidad {fmt_cant(orig[iid])} → {fmt_cant(q)}")
        for iid in orig:
            if iid not in receta:
                cambios.append(f"Insumo quitado: {self.nombres.get(iid, f'#{iid}')}")
        costo_ant = sum(q * self.precios.get(iid, 0) for iid, q in orig.items())
        costo_nvo = sum(q * self.precios.get(iid, 0) for iid, q in receta.items())
        pv_ant = costo_ant / (1 - p["margen"] / 100) if p["margen"] < 100 else 0
        pv_nvo = costo_nvo / (1 - m / 100) if m < 100 else 0
        _dif_num(cambios, "Precio de venta", pv_ant, pv_nvo, fmt_money, 0.005)
        return resumen_final("Producto modificado", nombre, cambios)

    def accept(self):
        m = self.margen.valor() or 0
        if not validar_obligatorios(self, [
            ("Nombre", self.nombre, bool(self.nombre.text().strip())),
            ("Categoría", self.cat, self.cat.currentData() is not None),
        ]):
            return
        if m >= 100:
            marcar(self.margen, False)
            return aviso(self, "El margen debe ser menor a 100 %.")
        marcar(self.margen, True)
        if self.producto and not confirmar(
                self, f"Vas a modificar el producto «{self.producto['nombre']}».\n\n¿Seguro que deseas guardar los cambios?",
                "Confirmar edición"):
            return
        if self.producto and self.margen.text() == self._texto_margen0:
            m = self.producto["margen"]  # no tocado: se conserva el valor exacto, sin redondeo
        receta = {}
        for r in range(self.tabla.rowCount()):
            iid, q = self.tabla.cellWidget(r, 1).currentData(), self.tabla.cellWidget(r, 2).valor() or 0
            if iid is not None and q > 0:
                receta[iid] = receta.get(iid, 0) + q  # si repiten un insumo, se suman
        nombre, desc = self.nombre.text().strip(), self.desc.toPlainText().strip()
        try:
            db.guardar_producto(self.con, nombre, desc, self.cat.currentData(), m, self.activo.isChecked(),
                                list(receta.items()), self.producto["id"] if self.producto else None)
        except ValueError as e:
            return aviso(self, str(e))
        if self.producto:
            self.resumen = self._resumen_cambios(nombre, desc, m, self.activo.isChecked(), receta)
        super().accept()


class ProductosTab(QWidget):
    COLS = ["Sel.", "N°", "Nombre", "Descripción", "Categoría", "Cant de Insumos", "Costo",
            "Precio venta", "Ganancia", "Margen", "Activo"]
    C_RADIO, C_NUM, C_NOMBRE, C_DESC, C_MARGEN, C_ACTIVO = 0, 1, 2, 3, 9, 10
    DERECHA = (1, 5, 6, 7, 8, 9)

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.radios, self.grupo = [], None
        self._cargando = False
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("Buscar por nombre o descripción…")
        self.filtro = ComboBuscable("Filtrar por categoría…")
        b_nuevo, b_edit, b_copiar, b_del, b_cats = (QPushButton(t) for t in
                                                    ("Nuevo", "Editar", "Copiar Producto", "Eliminar", "Categorías…"))
        b_nuevo.clicked.connect(self.nuevo)
        b_edit.clicked.connect(self.editar)
        b_copiar.clicked.connect(self.copiar)
        b_del.clicked.connect(self.eliminar)
        b_cats.clicked.connect(self.categorias)
        barra = QHBoxLayout()
        barra.addWidget(self.buscar, 2)
        barra.addWidget(self.filtro, 1)
        for b in (b_nuevo, b_edit, b_copiar, b_del, b_cats):
            barra.addWidget(b)

        self.tabla = crear_tabla(self.COLS)
        self.tabla.setColumnHidden(self.C_ACTIVO, True)
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(self.C_DESC, QHeaderView.Stretch)
        h.setSectionResizeMode(self.C_RADIO, QHeaderView.Fixed)
        self.tabla.setColumnWidth(self.C_RADIO, 46)
        self.tabla.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tabla.doubleClicked.connect(self.doble_clic)
        self.tabla.itemChanged.connect(self.al_cambiar_item)
        self.tabla.itemSelectionChanged.connect(self.al_cambiar_seleccion)
        self.pag = Paginador()
        self.pag.cambiado.connect(self.refrescar)
        self.detalle = crear_tabla(["N°", "Insumo", "Cantidad", "Precio unit.", "Subtotal"])
        self.detalle.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        arriba = QWidget()
        al = QVBoxLayout(arriba)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(self.tabla)
        al.addWidget(self.pag)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(QLabel("<b>Insumos del producto seleccionado</b>"))
        pl.addWidget(self.detalle)
        split = QSplitter(Qt.Vertical)
        split.addWidget(arriba)
        split.addWidget(panel)
        split.setSizes([420, 180])

        lay = QVBoxLayout(self)
        lay.addLayout(barra)
        lay.addWidget(split)
        self.recargar_filtro()
        self.buscar.textChanged.connect(lambda _: self.refrescar(True))
        self.filtro.currentIndexChanged.connect(lambda _: self.refrescar(True))
        self.refrescar()

    def al_mostrar(self):
        self.refrescar()

    def recargar_filtro(self):
        actual = self.filtro.currentData()
        self.filtro.blockSignals(True)
        llenar_categorias(self.filtro, self.con, "producto", actual, "Todas las categorías")
        self.filtro.blockSignals(False)

    # --- radio buttons: el radio marcado y la fila seleccionada siempre coinciden ---
    def _sincronizar_radio(self):
        if self.grupo is None:
            return
        fila = fila_seleccionada(self.tabla)
        if 0 <= fila < len(self.radios):
            self.radios[fila].setChecked(True)  # al ser un grupo exclusivo, desmarca los demás
        else:
            self.grupo.setExclusive(False)
            for rb in self.radios:
                rb.setChecked(False)
            self.grupo.setExclusive(True)

    def al_cambiar_seleccion(self):
        if self._cargando:
            return
        self._sincronizar_radio()
        self.mostrar_detalle()

    def refrescar(self, reiniciar=False, seleccionar=None):
        sel = seleccionar if seleccionar is not None else id_seleccionado(self.tabla, self.C_NUM)
        if reiniciar:
            self.pag.reiniciar()
        texto, cat = self.buscar.text().strip(), self.filtro.currentData()
        self.pag.actualizar(db.contar_productos(self.con, texto, cat))
        filas = db.listar_productos(self.con, texto, cat, self.pag.limite, self.pag.offset)
        self._cargando = True  # llenar la tabla no cuenta como "el usuario editó el margen"
        try:
            if self.grupo is not None:
                self.grupo.deleteLater()
            self.grupo = QButtonGroup(self)
            self.grupo.setExclusive(True)
            self.radios = []
            self.tabla.setRowCount(len(filas))
            for r, p in enumerate(filas):
                rb = QRadioButton()
                rb.clicked.connect(lambda _=False, fila=r: self.tabla.selectRow(fila))
                self.grupo.addButton(rb)
                self.radios.append(rb)
                cont = QWidget()  # contenedor para centrar el radio en la celda
                cl = QHBoxLayout(cont)
                cl.setContentsMargins(0, 0, 0, 0)
                cl.setAlignment(Qt.AlignCenter)
                cl.addWidget(rb)
                self.tabla.setCellWidget(r, self.C_RADIO, cont)

                valores = [self.pag.offset + r + 1, p["nombre"], p["descripcion"].replace("\n", " "),
                           p["categoria"], p["n_insumos"], fmt_money(p["costo"]), fmt_money(p["precio_venta"]),
                           fmt_money(p["ganancia"]), f"{p['margen']:.1f}%", "Sí" if p["activo"] else "No"]
                for c, v in enumerate(valores, start=1):
                    it = celda(v, derecha=c in self.DERECHA, dato=p["id"] if c == self.C_NUM else None)
                    if c == self.C_MARGEN:  # solo el margen es editable
                        it.setFlags(it.flags() | Qt.ItemIsEditable)
                        it.setBackground(QBrush(QColor(COL["margen_editable"])))
                        it.setData(Qt.UserRole, p["costo"])
                        it.setData(Qt.UserRole + 1, p["margen"])
                        it.setData(Qt.UserRole + 2, p["precio_venta"])
                        it.setToolTip("Doble clic para escribir el margen de ganancia deseado.\n"
                                      "Precio de venta = costo ÷ (1 − margen).")
                    self.tabla.setItem(r, c, it)
        finally:
            self._cargando = False
        if sel is not None:
            seleccionar_id(self.tabla, sel, self.C_NUM)
        self._sincronizar_radio()
        self.mostrar_detalle()

    def doble_clic(self, idx):
        if idx.column() != self.C_MARGEN:  # en el margen se edita la celda; en el resto se abre el formulario
            self.editar()

    def al_cambiar_item(self, item):
        if self._cargando or item.column() != self.C_MARGEN:
            return
        fila = item.row()
        datos = (self.tabla.item(fila, self.C_NUM).data(Qt.UserRole), self.tabla.item(fila, self.C_NOMBRE).text(),
                 item.text(), item.data(Qt.UserRole) or 0.0, item.data(Qt.UserRole + 1) or 0.0,
                 item.data(Qt.UserRole + 2) or 0.0)
        self._cargando = True  # cambiar el color de la celda también dispara itemChanged
        item.setBackground(QBrush(QColor(COL["cambio"])))
        item.setForeground(QBrush(QColor(COL["cambio_texto"])))
        self._cargando = False
        QTimer.singleShot(0, lambda: self._procesar_margen(*datos))  # fuera del evento de edición

    def _procesar_margen(self, pid, nombre, texto, costo, viejo, precio_viejo):
        try:
            nuevo = float(texto.replace("%", "").replace(",", ".").strip())
            if not 0 <= nuevo < 100:
                raise ValueError
        except ValueError:
            aviso(self, "Escribe un margen entre 0 y 99.9 (por ejemplo 55 o 55%).")
        else:
            if costo <= 0:
                aviso(self, "Este producto no tiene costo (no tiene insumos), no se puede calcular el precio.")
            elif abs(nuevo - viejo) >= 0.05:
                precio_nuevo = costo / (1 - nuevo / 100)
                if confirmar(self, f"¿Cambiar el margen de «{nombre}» de {viejo:.1f}% a {nuevo:.1f}%?\n\n"
                                   f"El precio de venta pasará de {fmt_money(precio_viejo)} a {fmt_money(precio_nuevo)}.",
                             "Confirmar cambio de margen"):
                    db.actualizar_margen(self.con, pid, nuevo)
                    mostrar_toast(self, f"Producto modificado: «{nombre}»", [
                        f"Margen: {viejo:.1f}% → {nuevo:.1f}%",
                        f"Precio de venta: {fmt_money(precio_viejo)} → {fmt_money(precio_nuevo)}"])
        self.refrescar()

    def mostrar_detalle(self):
        pid = id_seleccionado(self.tabla, self.C_NUM)
        filas = db.obtener_receta(self.con, pid) if pid is not None else []
        self.detalle.setRowCount(len(filas))
        for r, i in enumerate(filas):
            nombre = i["nombre"] + (f" — {i['descripcion']}" if i["descripcion"] else "")
            for c, v in enumerate([r + 1, nombre, f"{i['cantidad']:,.2f}",
                                   fmt_unit_sol(i["precio_unitario"]), fmt_money(i["subtotal"])]):
                self.detalle.setItem(r, c, celda(v, derecha=c != 1))

    def nuevo(self):
        if ProductoDialog(self.con, parent=self).exec():
            self.refrescar()

    def editar(self):
        pid = id_seleccionado(self.tabla, self.C_NUM)
        if pid is None:
            return aviso(self, "Selecciona un producto con el botón de la primera columna.")
        dlg = ProductoDialog(self.con, db.obtener_producto(self.con, pid), self)
        if dlg.exec():
            self.ir_a(pid)  # por si el nombre cambió de lugar en la lista
            if dlg.resumen:
                mostrar_toast(self, *dlg.resumen)

    def copiar(self):
        pid = id_seleccionado(self.tabla, self.C_NUM)  # = producto del radio marcado
        if pid is None:
            return aviso(self, "Selecciona el producto a copiar con el botón de la primera columna.")
        try:
            nuevo = db.copiar_producto(self.con, pid)
        except ValueError as e:
            return aviso(self, str(e))
        self.ir_a(nuevo)

    def ir_a(self, producto_id):
        """Muestra la página donde está el producto y lo deja seleccionado."""
        pos = db.posicion_producto(self.con, producto_id, self.buscar.text().strip(), self.filtro.currentData())
        self.pag.pagina = pos // self.pag.limite + 1
        self.refrescar(seleccionar=producto_id)

    def eliminar(self):
        pid = id_seleccionado(self.tabla, self.C_NUM)
        if pid is None:
            return aviso(self, "Selecciona un producto con el botón de la primera columna.")
        if confirmar(self, "¿Eliminar el producto seleccionado?"):
            try:
                db.eliminar_producto(self.con, pid)
            except ValueError as e:
                return aviso(self, str(e))
            self.refrescar()

    def categorias(self):
        CategoriasDialog(self.con, self, "producto").exec()
        self.recargar_filtro()
        self.refrescar(True)


# ----------------------------- histórico de compras -----------------------------
class CompraDialog(QDialog):
    def __init__(self, con, compra=None, parent=None):
        super().__init__(parent)
        self.con, self.compra = con, compra
        self.hubo_cambios, self.guardado_id = False, None
        self.resumen = None  # (título, líneas) para el aviso emergente cuando se edita
        self.setWindowTitle("Editar compra" if compra else "Nueva compra")
        self.setMinimumWidth(520)
        self.insumos = db.listar_insumos(con, orden="nombre")
        self.cats = {i["id"]: i["categoria"] for i in self.insumos}
        self.nombres = {i["id"]: i["nombre"] for i in self.insumos}

        self.nombre = ComboBuscable("Escribe para buscar un insumo…")
        self.nombre.addItem("", None)
        for i in self.insumos:
            self.nombre.addItem(i["nombre"] + (f" — {i['descripcion']}" if i["descripcion"] else ""), i["id"])
        self.categoria = QLineEdit()
        self.categoria.setReadOnly(True)
        self.categoria.setPlaceholderText("Se completa al elegir el insumo")
        self.fecha = FechaEdit()  # calendario desplegable
        self.cant = CampoNumero(2, "Ej. 2")
        self.precio = CampoNumero(5, "0.00000")
        self.total = QLabel()
        self.total.setStyleSheet("font-weight: bold; font-size: 15px;")

        form = QFormLayout()
        form.addRow("Nombre (insumo) *", self.nombre)
        form.addRow("Categoría", self.categoria)
        form.addRow("Fecha de compra *", self.fecha)
        form.addRow("Cantidad *", self.cant)
        form.addRow("Precio de costo (S/) *", self.precio)
        form.addRow("Costo total de la compra", self.total)
        self.lbl_estado = QLabel()
        self.lbl_estado.setObjectName("estado")
        nota = QLabel("* Campos obligatorios.   Costo total = Cantidad × Precio de costo "
                      "(ej. 2 paquetes de S/ 25 = S/ 50).   Esta compra no modifica el precio unitario "
                      "que tienes en la pestaña Insumos.")
        nota.setObjectName("nota")
        nota.setWordWrap(True)
        botones = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        if not compra:  # para cargar varias compras seguidas
            b_otra = QPushButton("Guardar y agregar otra")
            b_otra.clicked.connect(self.guardar_y_otra)
            botones.addButton(b_otra, QDialogButtonBox.ActionRole)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.lbl_estado)
        lay.addWidget(nota)
        lay.addWidget(botones)

        self.seg = SeguimientoCambios([
            (self.nombre, lambda: self.nombre.currentData()),
            (self.fecha, lambda: self.fecha.date()),
            (self.cant, lambda: self.cant.text()),
            (self.precio, lambda: self.precio.text()),
        ])
        self.nombre.currentIndexChanged.connect(self.al_elegir)
        self.cant.textChanged.connect(self.recalcular)
        self.precio.textChanged.connect(self.recalcular)
        if compra:
            self.nombre.setCurrentIndex(max(self.nombre.findData(compra["insumo_id"]), 0))
            self.fecha.setDate(QDate.fromString(compra["fecha"], "yyyy-MM-dd"))
            self.cant.poner(compra["cantidad"])
            self.precio.poner(compra["precio"])
        else:
            self.cant.setText("1")  # lo más común: una unidad/paquete
        self.al_elegir()
        self.recalcular()
        if compra:
            self.seg.activar()  # el resaltado ladrillo solo existe al EDITAR

    def al_elegir(self, *_):
        self.categoria.setText(self.cats.get(self.nombre.currentData(), ""))

    def recalcular(self, *_):
        self.total.setText(fmt_money((self.cant.valor() or 0) * (self.precio.valor() or 0)))

    def _guardar(self):
        cant, precio = self.cant.valor(), self.precio.valor()
        if not validar_obligatorios(self, [
            ("Nombre (insumo)", self.nombre, self.nombre.currentData() is not None),
            ("Cantidad", self.cant, cant is not None),
            ("Precio de costo", self.precio, precio is not None),
        ]):
            return False
        if cant <= 0:
            marcar(self.cant, False)
            aviso(self, "La cantidad debe ser mayor que 0.")
            return False
        if self.compra and not confirmar(
                self, "Vas a modificar esta compra.\n\n¿Seguro que deseas guardar los cambios?", "Confirmar edición"):
            return False
        iid, iso = self.nombre.currentData(), self.fecha.date().toString("yyyy-MM-dd")
        try:
            self.guardado_id = db.guardar_compra(self.con, iid, iso, cant, precio,
                                                 self.compra["id"] if self.compra else None)
        except ValueError as e:
            aviso(self, str(e))
            return False
        self.hubo_cambios = True
        if self.compra:  # qué se modificó, para el aviso emergente
            c, cambios = self.compra, []
            _dif(cambios, "Insumo", c["insumo"], self.nombres.get(iid, ""), corto)
            _dif(cambios, "Fecha de compra", c["fecha"], iso, fmt_fecha)
            _dif_num(cambios, "Cantidad", c["cantidad"], cant, fmt_cant)
            _dif_num(cambios, "Precio de costo", c["precio"], precio, fmt_precio5, 1e-9)
            _dif_num(cambios, "Costo total", c["cantidad"] * c["precio"], cant * precio, fmt_money, 0.005)
            self.resumen = resumen_final("Compra modificada", self.nombres.get(iid, ""), cambios)
        return True

    def accept(self):
        if self._guardar():
            super().accept()

    def guardar_y_otra(self):
        if self._guardar():
            self.nombre.setCurrentIndex(0)   # la fecha se conserva para la siguiente compra
            self.cant.setText("1")
            self.precio.clear()
            self.lbl_estado.setText("✔ Compra guardada. Puedes agregar otra.")
            self.nombre.setFocus()


class ComprasTab(OrdenPorCabecera, QWidget):
    COLS = ["N°", "Nombre", "Categoría", "Fecha de Compra", "Cantidad", "Precio de Costo", "Costo Total"]
    ORDEN_COL = {1: "nombre", 2: "categoria", 3: "fecha", 4: "cantidad", 5: "precio_costo", 6: "costo_total"}

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("Buscar insumo…")
        self.filtro = ComboBuscable("Filtrar por categoría…")
        b_nuevo, b_edit, b_del = QPushButton("Nueva compra"), QPushButton("Editar"), QPushButton("Eliminar")
        b_nuevo.clicked.connect(self.nuevo)
        b_edit.clicked.connect(self.editar)
        b_del.clicked.connect(self.eliminar)
        barra = QHBoxLayout()
        barra.addWidget(self.buscar, 2)
        barra.addWidget(self.filtro, 1)
        for b in (b_nuevo, b_edit, b_del):
            barra.addWidget(b)

        # filtro por fecha de compra (rango; cada extremo se activa con su casilla)
        self.chk_desde, self.chk_hasta = QCheckBox("Desde"), QCheckBox("Hasta")
        self.fecha_desde, self.fecha_hasta = self._crear_fecha(), self._crear_fecha()
        b_limpiar = QPushButton("Limpiar filtros")
        b_limpiar.clicked.connect(self.limpiar_filtros)
        fila_f = QHBoxLayout()
        fila_f.addWidget(QLabel("Fecha de compra:"))
        for w in (self.chk_desde, self.fecha_desde, self.chk_hasta, self.fecha_hasta):
            fila_f.addWidget(w)
        fila_f.addStretch()
        fila_f.addWidget(b_limpiar)

        self.tabla = crear_tabla(self.COLS)
        self.tabla.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._init_orden()
        self.tabla.doubleClicked.connect(lambda _: self.editar())
        self.lbl_total = QLabel()
        self.lbl_total.setStyleSheet("font-size: 15px; font-weight: bold;")
        fila_total = QHBoxLayout()
        fila_total.addStretch()
        fila_total.addWidget(self.lbl_total)
        self.pag = Paginador()
        self.pag.cambiado.connect(self.refrescar)

        lay = QVBoxLayout(self)
        lay.addLayout(barra)
        lay.addLayout(fila_f)
        lay.addWidget(self.tabla)
        lay.addLayout(fila_total)
        lay.addWidget(self.pag)

        self.recargar_filtro()
        self._fechas_por_defecto()
        self.buscar.textChanged.connect(lambda _: self.refrescar(True))
        self.filtro.currentIndexChanged.connect(lambda _: self.refrescar(True))
        self.chk_desde.toggled.connect(lambda on: self._activar_fecha(self.fecha_desde, on))
        self.chk_hasta.toggled.connect(lambda on: self._activar_fecha(self.fecha_hasta, on))
        self.fecha_desde.dateChanged.connect(lambda _: self._fecha_cambiada(self.chk_desde))
        self.fecha_hasta.dateChanged.connect(lambda _: self._fecha_cambiada(self.chk_hasta))
        self.refrescar()

    @staticmethod
    def _crear_fecha():
        e = FechaEdit()
        e.setEnabled(False)
        return e

    def _fechas_por_defecto(self):
        """Al activar un extremo, parte del rango real del histórico (así no se vacía la lista de golpe)."""
        mn, mx = db.rango_fechas_compras(self.con)
        if not self.chk_desde.isChecked():
            d = QDate.fromString(mn or "", "yyyy-MM-dd")
            self.fecha_desde.setDate(d if d.isValid() else QDate.currentDate())
        if not self.chk_hasta.isChecked():
            d = QDate.fromString(mx or "", "yyyy-MM-dd")
            self.fecha_hasta.setDate(d if d.isValid() else QDate.currentDate())

    def _activar_fecha(self, edit, activo):
        edit.setEnabled(activo)
        self.refrescar(True)

    def _fecha_cambiada(self, chk):
        if chk.isChecked():
            self.refrescar(True)

    def _filtros(self):
        desde = self.fecha_desde.date().toString("yyyy-MM-dd") if self.chk_desde.isChecked() else None
        hasta = self.fecha_hasta.date().toString("yyyy-MM-dd") if self.chk_hasta.isChecked() else None
        return self.buscar.text().strip(), self.filtro.currentData(), desde, hasta

    def limpiar_filtros(self):
        widgets = (self.buscar, self.filtro, self.chk_desde, self.chk_hasta)
        for w in widgets:
            w.blockSignals(True)
        self.buscar.clear()
        self.filtro.setCurrentIndex(0)
        self.chk_desde.setChecked(False)
        self.chk_hasta.setChecked(False)
        self.fecha_desde.setEnabled(False)
        self.fecha_hasta.setEnabled(False)
        for w in widgets:
            w.blockSignals(False)
        self._fechas_por_defecto()
        self.refrescar(True)

    def al_mostrar(self):
        self.recargar_filtro()  # por si cambiaron insumos o categorías en otra pestaña
        self._fechas_por_defecto()
        self.refrescar()

    def recargar_filtro(self):
        actual = self.filtro.currentData()
        self.filtro.blockSignals(True)
        llenar_categorias(self.filtro, self.con, "insumo", actual, "Todas las categorías")
        self.filtro.blockSignals(False)

    def refrescar(self, reiniciar=False, seleccionar=None):
        sel = seleccionar if seleccionar is not None else id_seleccionado(self.tabla)
        if reiniciar:
            self.pag.reiniciar()
        f = self._filtros()
        self.pag.actualizar(db.contar_compras(self.con, *f))
        # suma del Costo Total (cantidad × precio) de TODAS las compras filtradas, no solo las de la página visible
        self.lbl_total.setText(f"Total de Compra: {fmt_money(db.total_compras(self.con, *f))}")
        filas = db.listar_compras(self.con, *f, orden=self.orden, asc=self.asc,
                                  limite=self.pag.limite, offset=self.pag.offset)
        self.tabla.setRowCount(len(filas))
        for r, c in enumerate(filas):
            valores = [self.pag.offset + r + 1, c["insumo"], c["categoria"], fmt_fecha(c["fecha"]),
                       fmt_cant(c["cantidad"]), fmt_money(c["precio"]),  # precio: 2 decimales en pantalla, se guarda con 5
                       fmt_money(c["costo_total"])]
            for k, v in enumerate(valores):
                self.tabla.setItem(r, k, celda(v, derecha=k in (0, 4, 5, 6), dato=c["id"] if k == 0 else None,
                                               tooltip=c["descripcion"] if k == 1 else None))
        if sel is not None:
            seleccionar_id(self.tabla, sel)

    def nuevo(self):
        dlg = CompraDialog(self.con, parent=self)
        dlg.exec()
        if dlg.hubo_cambios:
            self.restablecer_orden()  # vuelve al orden por fecha (lo más reciente primero)
            self._fechas_por_defecto()
            self.refrescar(True, dlg.guardado_id)

    def editar(self):
        cid = id_seleccionado(self.tabla)
        if cid is None:
            return aviso(self, "Selecciona una compra.")
        dlg = CompraDialog(self.con, db.obtener_compra(self.con, cid), self)
        dlg.exec()
        if dlg.hubo_cambios:
            self._fechas_por_defecto()
            self.refrescar(False, dlg.guardado_id)
            if dlg.resumen:
                mostrar_toast(self, *dlg.resumen)

    def eliminar(self):
        cid = id_seleccionado(self.tabla)
        if cid is None:
            return aviso(self, "Selecciona una compra.")
        if confirmar(self, "¿Eliminar la compra seleccionada?"):
            db.eliminar_compra(self.con, cid)
            self._fechas_por_defecto()
            self.refrescar()


# ----------------------------- ventas -----------------------------
class VentaDialog(QDialog):
    def __init__(self, con, venta=None, parent=None):
        super().__init__(parent)
        self.con, self.venta = con, venta
        self.hubo_cambios, self.guardado_id = False, None
        self.resumen = None  # (título, líneas) para el aviso emergente cuando se edita
        self._costo = 0.0    # costo del producto que queda guardado con la venta
        self.setWindowTitle("Editar venta" if venta else "Nueva venta")
        self.setMinimumWidth(580)
        self.prods = {p["id"]: p for p in db.productos_vendibles(con, venta["producto_id"] if venta else None)}

        self.fecha = FechaEdit()
        self.producto = ComboBuscable("Escribe para buscar un producto…")
        self.producto.addItem("", None)
        for p in self.prods.values():
            self.producto.addItem(p["nombre"], p["id"])
        self.categoria = QLineEdit()
        self.categoria.setReadOnly(True)
        self.categoria.setPlaceholderText("Se completa al elegir el producto")
        self.cant = CampoNumero(0, "Ej. 1")
        self.cant.setMaximumWidth(120)
        self.precio = CampoNumero(2, "0.00")
        self.precio.setMaximumWidth(160)
        fila_precio = QHBoxLayout()
        fila_precio.addWidget(QLabel("S/"))
        fila_precio.addWidget(self.precio)
        fila_precio.addStretch()
        self.costo = QLineEdit()  # solo lectura: costo del producto al momento de vender
        self.costo.setReadOnly(True)
        self.costo.setAlignment(Qt.AlignRight)
        self.costo.setPlaceholderText("Del producto")
        self.costo.setMaximumWidth(160)
        fila_costo = QHBoxLayout()
        fila_costo.addWidget(QLabel("S/"))
        fila_costo.addWidget(self.costo)
        fila_costo.addStretch()
        self.total, self.ganancia = QLabel(), QLabel()
        for lbl in (self.total, self.ganancia):
            lbl.setStyleSheet("font-weight: bold; font-size: 15px;")
        self.cliente = QLineEdit()
        self.cliente.setPlaceholderText("Opcional")
        self.notas = QPlainTextEdit()
        self.notas.setPlaceholderText("Opcional")
        self.notas.setFixedHeight(60)

        form = QFormLayout()
        form.addRow("Fecha de venta *", self.fecha)
        form.addRow("Producto *", self.producto)
        form.addRow("Categoría", self.categoria)
        form.addRow("Cantidad *", self.cant)
        form.addRow("Precio de venta (S/) *", fila_precio)
        form.addRow("Costo unitario (S/)", fila_costo)
        form.addRow("Total de la venta", self.total)
        form.addRow("Ganancia", self.ganancia)
        form.addRow("Cliente", self.cliente)
        form.addRow("Notas", self.notas)
        self.lbl_estado = QLabel()
        self.lbl_estado.setObjectName("estado")
        nota = QLabel("* Campos obligatorios.   El precio se rellena con el del producto y puedes cambiarlo "
                      "(por ejemplo, un descuento). El costo del producto queda guardado al vender, así el "
                      "historial no cambia si luego suben los insumos. Las ventas no descuentan insumos.")
        nota.setObjectName("nota")
        nota.setWordWrap(True)
        botones = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        if not venta:  # para cargar varias ventas seguidas
            b_otra = QPushButton("Guardar y agregar otra")
            b_otra.clicked.connect(self.guardar_y_otra)
            botones.addButton(b_otra, QDialogButtonBox.ActionRole)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.lbl_estado)
        lay.addWidget(nota)
        lay.addWidget(botones)

        self.seg = SeguimientoCambios([
            (self.fecha, lambda: self.fecha.date()),
            (self.producto, lambda: self.producto.currentData()),
            (self.cant, lambda: self.cant.text()),
            (self.precio, lambda: self.precio.text()),
            (self.cliente, lambda: self.cliente.text()),
            (self.notas, lambda: self.notas.toPlainText()),
        ])
        if venta:
            self.fecha.setDate(QDate.fromString(venta["fecha"], "yyyy-MM-dd"))
            self.producto.setCurrentIndex(max(self.producto.findData(venta["producto_id"]), 0))
            self.cant.poner(venta["cantidad"])
            self.precio.poner(venta["precio"])
            self._costo = venta["costo"]  # se conserva el costo de cuando se vendió
            self.costo.setText(fmt_unit(self._costo))
            self.cliente.setText(venta["cliente"])
            self.notas.setPlainText(venta["notas"])
        else:
            self.cant.setText("1")
        self._poner_categoria()
        self.producto.currentIndexChanged.connect(self.al_elegir)  # se conecta después de cargar los datos
        self.cant.textChanged.connect(self.recalcular)
        self.precio.textChanged.connect(self.recalcular)
        self.recalcular()
        if venta:
            self.seg.activar()  # el resaltado ladrillo solo existe al EDITAR

    def _poner_categoria(self):
        p = self.prods.get(self.producto.currentData())
        self.categoria.setText(p["categoria"] if p else "")

    def al_elegir(self, *_):
        """Al elegir producto se rellenan su precio de venta y su costo actuales (el precio se puede cambiar)."""
        p = self.prods.get(self.producto.currentData())
        self._poner_categoria()
        if p:
            self.precio.poner(p["precio_venta"])
            self._costo = p["costo"]
            self.costo.setText(fmt_unit(self._costo))
        else:
            self.precio.clear()
            self._costo = 0.0
            self.costo.clear()
        self.recalcular()

    def recalcular(self, *_):
        c, p = self.cant.valor() or 0, self.precio.valor() or 0
        self.total.setText(fmt_money(c * p))
        self.ganancia.setText(fmt_money(c * (p - self._costo)))

    def _guardar(self):
        cant, precio = self.cant.valor(), self.precio.valor()
        if not validar_obligatorios(self, [
            ("Producto", self.producto, self.producto.currentData() is not None),
            ("Cantidad", self.cant, cant is not None),
            ("Precio de venta", self.precio, precio is not None),
        ]):
            return False
        if cant <= 0:
            marcar(self.cant, False)
            aviso(self, "La cantidad debe ser mayor que 0.")
            return False
        if self.venta and not confirmar(
                self, "Vas a modificar esta venta.\n\n¿Seguro que deseas guardar los cambios?", "Confirmar edición"):
            return False
        pid, iso = self.producto.currentData(), self.fecha.date().toString("yyyy-MM-dd")
        cliente, notas = self.cliente.text().strip(), self.notas.toPlainText().strip()
        try:
            self.guardado_id = db.guardar_venta(self.con, iso, pid, int(cant), precio, self._costo,
                                                cliente or None, notas or None,
                                                self.venta["id"] if self.venta else None)
        except ValueError as e:
            aviso(self, str(e))
            return False
        self.hubo_cambios = True
        if self.venta:  # qué se modificó, para el aviso emergente
            v, cambios = self.venta, []
            nombre = self.prods[pid]["nombre"]
            _dif(cambios, "Fecha de venta", v["fecha"], iso, fmt_fecha)
            _dif(cambios, "Producto", v["producto"], nombre, corto)
            _dif_num(cambios, "Cantidad", v["cantidad"], int(cant), fmt_cant)
            _dif_num(cambios, "Precio de venta", v["precio"], precio, fmt_money, 0.005)
            _dif_num(cambios, "Costo unitario", v["costo"], self._costo, fmt_unit_sol, 1e-7)
            _dif(cambios, "Cliente", v["cliente"], cliente, corto)
            _dif(cambios, "Notas", v["notas"].strip(), notas, corto)
            _dif_num(cambios, "Total de la venta", v["total_venta"], int(cant) * precio, fmt_money, 0.005)
            _dif_num(cambios, "Ganancia", v["ganancia"], int(cant) * (precio - self._costo), fmt_money, 0.005)
            self.resumen = resumen_final("Venta modificada", nombre, cambios)
        return True

    def accept(self):
        if self._guardar():
            super().accept()

    def guardar_y_otra(self):
        if self._guardar():
            self.producto.setCurrentIndex(0)  # la fecha se conserva para la siguiente venta
            self.cant.setText("1")
            self.cliente.clear()
            self.notas.clear()
            self.lbl_estado.setText("✔ Venta guardada. Puedes agregar otra.")
            self.producto.setFocus()


class VentasTab(OrdenPorCabecera, FiltroFechas, QWidget):
    COLS = ["N°", "Fecha", "Producto", "Categoría", "Cliente", "Cantidad", "Precio de Venta",
            "Costo", "Total Venta", "Ganancia"]
    ORDEN_COL = {1: "fecha", 2: "producto", 3: "categoria", 4: "cliente", 5: "cantidad", 6: "precio",
                 7: "costo", 8: "total_venta", 9: "ganancia"}

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("Buscar producto o cliente…")
        self.filtro = ComboBuscable("Filtrar por categoría…")
        b_nuevo, b_edit, b_del = QPushButton("Nueva venta"), QPushButton("Editar"), QPushButton("Eliminar")
        b_nuevo.clicked.connect(self.nuevo)
        b_edit.clicked.connect(self.editar)
        b_del.clicked.connect(self.eliminar)
        barra = QHBoxLayout()
        barra.addWidget(self.buscar, 2)
        barra.addWidget(self.filtro, 1)
        for b in (b_nuevo, b_edit, b_del):
            barra.addWidget(b)

        self._init_fechas()
        b_limpiar = QPushButton("Limpiar filtros")
        b_limpiar.clicked.connect(self.limpiar_filtros)
        fila_f = self._fila_fechas()
        fila_f.addStretch()
        fila_f.addWidget(b_limpiar)

        self.tabla = crear_tabla(self.COLS)
        self.tabla.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._init_orden()
        self.tabla.doubleClicked.connect(lambda _: self.editar())
        self.lbl_total = QLabel()
        self.lbl_total.setStyleSheet("font-size: 15px; font-weight: bold;")
        fila_total = QHBoxLayout()
        fila_total.addStretch()
        fila_total.addWidget(self.lbl_total)
        self.pag = Paginador()
        self.pag.cambiado.connect(self.refrescar)

        lay = QVBoxLayout(self)
        lay.addLayout(barra)
        lay.addLayout(fila_f)
        lay.addWidget(self.tabla)
        lay.addLayout(fila_total)
        lay.addWidget(self.pag)

        self.recargar_filtro()
        self._fechas_por_defecto()
        self.buscar.textChanged.connect(lambda _: self.refrescar(True))
        self.filtro.currentIndexChanged.connect(lambda _: self.refrescar(True))
        self._conectar_fechas()
        self.refrescar()

    def _rango_fechas_datos(self):
        return db.rango_fechas_ventas(self.con)

    def _filtros(self):
        desde, hasta = self._desde_hasta()
        return self.buscar.text().strip(), self.filtro.currentData(), desde, hasta

    def limpiar_filtros(self):
        for w in (self.buscar, self.filtro):
            w.blockSignals(True)
        self.buscar.clear()
        self.filtro.setCurrentIndex(0)
        for w in (self.buscar, self.filtro):
            w.blockSignals(False)
        self._reiniciar_fechas()
        self.refrescar(True)

    def al_mostrar(self):
        self.recargar_filtro()  # por si cambiaron categorías o productos en otra pestaña
        self._fechas_por_defecto()
        self.refrescar()

    def recargar_filtro(self):
        actual = self.filtro.currentData()
        self.filtro.blockSignals(True)
        llenar_categorias(self.filtro, self.con, "producto", actual, "Todas las categorías")
        self.filtro.blockSignals(False)

    def refrescar(self, reiniciar=False, seleccionar=None):
        sel = seleccionar if seleccionar is not None else id_seleccionado(self.tabla)
        if reiniciar:
            self.pag.reiniciar()
        f = self._filtros()
        self.pag.actualizar(db.contar_ventas(self.con, *f))
        # totales de TODAS las ventas que cumplen los filtros, no solo las de la página visible
        total, costo = db.totales_ventas(self.con, *f)
        self.lbl_total.setText(f"Total de Ventas: {fmt_money(total)}   ·   Costo: {fmt_money(costo)}   ·   "
                               f"Ganancia: {fmt_money(total - costo)}")
        filas = db.listar_ventas(self.con, *f, orden=self.orden, asc=self.asc,
                                 limite=self.pag.limite, offset=self.pag.offset)
        self.tabla.setRowCount(len(filas))
        for r, v in enumerate(filas):
            valores = [self.pag.offset + r + 1, fmt_fecha(v["fecha"]), v["producto"], v["categoria"], v["cliente"],
                       fmt_cant(v["cantidad"]), fmt_money(v["precio"]), fmt_money(v["costo"]),
                       fmt_money(v["total_venta"]), fmt_money(v["ganancia"])]
            for k, val in enumerate(valores):
                self.tabla.setItem(r, k, celda(val, derecha=k in (0, 5, 6, 7, 8, 9),
                                               dato=v["id"] if k == 0 else None,
                                               tooltip=v["notas"] if k == 2 and v["notas"] else None))
        if sel is not None:
            seleccionar_id(self.tabla, sel)

    def nuevo(self):
        dlg = VentaDialog(self.con, parent=self)
        dlg.exec()
        if dlg.hubo_cambios:
            self.restablecer_orden()  # vuelve al orden por fecha (lo más reciente primero)
            self._fechas_por_defecto()
            self.refrescar(True, dlg.guardado_id)

    def editar(self):
        vid = id_seleccionado(self.tabla)
        if vid is None:
            return aviso(self, "Selecciona una venta.")
        dlg = VentaDialog(self.con, db.obtener_venta(self.con, vid), self)
        dlg.exec()
        if dlg.hubo_cambios:
            self._fechas_por_defecto()
            self.refrescar(False, dlg.guardado_id)
            if dlg.resumen:
                mostrar_toast(self, *dlg.resumen)

    def eliminar(self):
        vid = id_seleccionado(self.tabla)
        if vid is None:
            return aviso(self, "Selecciona una venta.")
        if confirmar(self, "¿Eliminar la venta seleccionada?"):
            db.eliminar_venta(self.con, vid)
            self._fechas_por_defecto()
            self.refrescar()


# ----------------------------- reportes -----------------------------
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def etiqueta_periodo(clave, agrupar, corto=True):
    """'2026-10' -> 'Oct 2026' | '2026-S41' -> 'S41 2026' | '2026-10-15' -> '15/10' (o '15/10/2026')."""
    try:
        if agrupar == "mes":
            a, m = clave.split("-")
            return f"{MESES[int(m) - 1]} {a}"
        if agrupar == "semana":
            a, sem = clave.split("-S")
            return f"S{sem} {a}"
        a, m, d = clave.split("-")
        return f"{d}/{m}" if corto else f"{d}/{m}/{a}"
    except (ValueError, IndexError):
        return clave


def _estilo_ejes(ax, c):
    ax.set_facecolor(c["superficie"])
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(c["borde"])
    ax.tick_params(colors=c["texto"], labelsize=8)
    ax.grid(axis="y", color=c["borde"], alpha=0.5, linewidth=0.6)
    ax.set_axisbelow(True)


def _sin_datos(ax, c):
    ax.set_axis_off()
    ax.text(0.5, 0.5, "Sin datos en el período", ha="center", va="center",
            color=c["texto_secundario"], fontsize=11, transform=ax.transAxes)


def dibujar_periodos(fig, datos, agrupar, c):
    """Barras por período: ingresos, gastos (insumos + operativo) y ganancia bruta. c = colores del tema."""
    from matplotlib.ticker import FuncFormatter
    fig.clear()
    fig.set_facecolor(c["superficie"])
    ax = fig.add_subplot(111)
    _estilo_ejes(ax, c)
    ax.set_title("Ingresos, gastos y ganancia por período", color=c["texto"], fontsize=11, fontweight="bold")
    if not datos:
        _sin_datos(ax, c)
        return
    n, ancho = len(datos), 0.27
    x = list(range(n))
    ax.bar([i - ancho for i in x], [d["ingresos"] for d in datos], ancho, label="Ingresos", color=c["principal"])
    ax.bar(x, [d["gasto_insumos"] + d["gasto_operativo"] for d in datos], ancho,
           label="Gastos (compras)", color=c["texto_secundario"])
    ax.bar([i + ancho for i in x], [d["ganancia"] for d in datos], ancho, label="Ganancia bruta", color=c["encabezado"])
    paso = max(1, n // 12)  # con muchos períodos no se saturan las etiquetas del eje
    etiquetas = [etiqueta_periodo(d["periodo"], agrupar) if i % paso == 0 else "" for i, d in enumerate(datos)]
    ax.set_xticks(x)
    ax.set_xticklabels(etiquetas, rotation=45 if n > 6 else 0, ha="right" if n > 6 else "center")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"S/ {v:,.0f}"))
    ax.axhline(0, color=c["borde"], linewidth=0.8)
    leyenda = ax.legend(frameon=False, fontsize=8)
    for t in leyenda.get_texts():
        t.set_color(c["texto"])
    fig.tight_layout()


def dibujar_top(fig, filas, c):
    """Barras horizontales: productos más rentables (por ganancia)."""
    from textwrap import shorten
    from matplotlib.ticker import FuncFormatter
    fig.clear()
    fig.set_facecolor(c["superficie"])
    ax = fig.add_subplot(111)
    _estilo_ejes(ax, c)
    ax.set_title("Productos más rentables", color=c["texto"], fontsize=11, fontweight="bold")
    ax.set_xlabel("Ganancia", color=c["texto_secundario"], fontsize=8)
    if not filas:
        _sin_datos(ax, c)
        return
    filas = list(reversed(filas))  # el más rentable arriba
    nombres = [shorten(f["nombre"], 28, placeholder="…") for f in filas]
    vals = [f["ganancia"] for f in filas]
    ax.barh(nombres, vals, color=c["encabezado"])
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=c["borde"], alpha=0.5, linewidth=0.6)
    tope = max(vals + [0])
    ax.set_xlim(min(0, min(vals)), tope * 1.3 if tope > 0 else 1)
    for i, v in enumerate(vals):
        ax.text(v, i, f" S/ {v:,.0f}", va="center", ha="left" if v >= 0 else "right", color=c["texto"], fontsize=8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"S/ {v:,.0f}"))
    fig.tight_layout()


def renderizar_rgba(dibujar, ancho, alto, dpr=1.0):
    """Dibuja con matplotlib (backend Agg, no depende de Qt) y devuelve (bytes RGBA, ancho_px, alto_px)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    fig = Figure(figsize=(ancho / 100, alto / 100), dpi=100 * dpr)
    canvas = FigureCanvasAgg(fig)
    dibujar(fig)
    canvas.draw()
    w, h = canvas.get_width_height()
    return bytes(canvas.buffer_rgba()), w, h


class GraficoMpl(QLabel):
    """Muestra un gráfico de matplotlib como imagen y lo vuelve a dibujar cuando cambia el tamaño."""

    def __init__(self, dibujar):
        super().__init__()
        self._dibujar = dibujar  # función(fig) que dibuja el gráfico
        self.setAlignment(Qt.AlignCenter)
        self.setObjectName("nota")
        self.setMinimumSize(260, 180)
        # 'Ignored': la imagen no obliga a la ventana a mantenerse grande
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._espera = QTimer(self)
        self._espera.setSingleShot(True)
        self._espera.timeout.connect(self.repintar)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._espera.start(150)  # espera a que termine de cambiar el tamaño

    def repintar(self):
        try:
            dpr = self.devicePixelRatioF()
            datos, w, h = renderizar_rgba(self._dibujar, max(self.width(), 120), max(self.height(), 120), dpr)
            pix = QPixmap.fromImage(QImage(datos, w, h, 4 * w, QImage.Format_RGBA8888).copy())
            pix.setDevicePixelRatio(dpr)
            self.setPixmap(pix)
        except Exception as e:  # el reporte sigue funcionando aunque falle el dibujo
            import traceback
            traceback.print_exc()
            self.setText(f"No se pudo dibujar el gráfico:\n{type(e).__name__}: {e}")


class ReportesTab(FiltroFechas, QWidget):
    KPIS = [("ingresos", "Ingresos por ventas", ""),
            ("costo", "Costo de lo vendido", "según receta al vender"),
            ("ganancia", "Ganancia bruta", ""),
            ("insumos", "Gasto en insumos", "compras de insumos"),
            ("operativo", "Gasto operativo", "pestaña Gasto Operativo"),
            ("resultado", "Resultado", "ingresos − gastos")]

    def __init__(self, con):
        super().__init__()
        self.con = con
        self._init_fechas()
        self.agrupar = ComboBuscable("Agrupar por…")
        for texto, clave in (("Mes", "mes"), ("Semana", "semana"), ("Día", "dia")):
            self.agrupar.addItem(texto, clave)
        b_limpiar = QPushButton("Limpiar filtros")
        b_limpiar.clicked.connect(self.limpiar_filtros)
        fila_f = self._fila_fechas()
        fila_f.addSpacing(18)
        fila_f.addWidget(QLabel("Agrupar por:"))
        fila_f.addWidget(self.agrupar)
        fila_f.addStretch()
        fila_f.addWidget(b_limpiar)

        fila_k = QHBoxLayout()  # tarjetas con los totales del rango elegido
        self.kpi = {}
        for clave, titulo, sub in self.KPIS:
            marco, valor, lbl_sub = self._crear_kpi(titulo, sub)
            self.kpi[clave] = (valor, lbl_sub)
            fila_k.addWidget(marco)

        self._mpl = False
        self._datos, self._top, self._agrupar = [], [], "mes"
        fila_g = QHBoxLayout()
        try:  # los gráficos usan matplotlib; si falla, el resto del reporte sigue funcionando
            import matplotlib  # noqa: F401
            from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: F401
            from matplotlib.figure import Figure  # noqa: F401
            self.grafico_per = GraficoMpl(lambda fig: dibujar_periodos(fig, self._datos, self._agrupar, COL))
            self.grafico_top = GraficoMpl(lambda fig: dibujar_top(fig, self._top, COL))
            fila_g.addWidget(self.grafico_per, 3)
            fila_g.addWidget(self.grafico_top, 2)
            self._mpl = True
        except Exception as e:
            import traceback
            traceback.print_exc()
            py = sys.executable
            falta = QLabel("No se pudieron cargar los gráficos.\n\n"
                           f"Motivo: {type(e).__name__}: {e}\n\n"
                           f"La aplicación se está ejecutando con este Python:\n{py}\n\n"
                           "Para instalar matplotlib en ESE Python, copia este comando en la terminal (PowerShell):\n"
                           f'& "{py}" -m pip install matplotlib\n\n'
                           "Luego cierra y vuelve a abrir la aplicación.")
            falta.setObjectName("nota")
            falta.setAlignment(Qt.AlignCenter)
            falta.setWordWrap(True)
            falta.setTextInteractionFlags(Qt.TextSelectableByMouse)  # para poder copiar el comando
            fila_g.addWidget(falta)

        self.tabla_per = crear_tabla(["Período", "Ingresos", "Costo de ventas", "Ganancia bruta",
                                      "Gasto insumos", "Gasto operativo", "Resultado"])
        self.tabla_top = crear_tabla(["N°", "Producto", "Unidades", "Ingresos", "Ganancia", "Margen"])
        self.tabla_per.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tabla_top.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        fila_t = QHBoxLayout()
        fila_t.addWidget(self.tabla_per, 3)
        fila_t.addWidget(self.tabla_top, 2)
        nota = QLabel("Ganancia bruta = ventas − costo de los insumos de cada producto al momento de vender.   "
                      "Resultado = ventas − compras (gasto en insumos + gasto operativo).   "
                      "Gasto operativo = lo registrado en la pestaña Gasto Operativo (más las compras de insumos de categorías ocultas en productos, si las hubiera).")
        nota.setObjectName("nota")
        nota.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addLayout(fila_f)
        lay.addLayout(fila_k)
        lay.addLayout(fila_g, 3)
        lay.addLayout(fila_t, 2)
        lay.addWidget(nota)

        self.agrupar.currentIndexChanged.connect(lambda _: self.refrescar())
        self._conectar_fechas()
        self._fechas_por_defecto()
        self.refrescar()

    @staticmethod
    def _crear_kpi(titulo, sub):
        marco = QFrame()
        marco.setObjectName("kpi")
        lay = QVBoxLayout(marco)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        t = QLabel(titulo)
        t.setObjectName("kpi_titulo")
        v = QLabel("—")
        v.setObjectName("kpi_valor")
        s = QLabel(sub)
        s.setObjectName("kpi_sub")
        s.setWordWrap(True)
        for w in (t, v, s):
            lay.addWidget(w)
        return marco, v, s

    def _rango_fechas_datos(self):
        return db.rango_fechas_reporte(self.con)

    def limpiar_filtros(self):
        self._reiniciar_fechas()
        self.refrescar()

    def al_mostrar(self):
        self._fechas_por_defecto()
        self.refrescar()

    def refrescar(self, reiniciar=False, seleccionar=None):
        desde, hasta = self._desde_hasta()
        agrupar = self.agrupar.currentData() or "mes"
        datos = db.resumen_periodos(self.con, desde, hasta, agrupar)
        top = db.productos_top(self.con, desde, hasta, 8)
        claves = ("ingresos", "costo_ventas", "ganancia", "gasto_insumos", "gasto_operativo", "resultado")
        tot = {k: sum(d[k] for d in datos) for k in claves}
        margen = tot["ganancia"] / tot["ingresos"] * 100 if tot["ingresos"] else 0

        for clave, valor in (("ingresos", tot["ingresos"]), ("costo", tot["costo_ventas"]),
                             ("ganancia", tot["ganancia"]), ("insumos", tot["gasto_insumos"]),
                             ("operativo", tot["gasto_operativo"]), ("resultado", tot["resultado"])):
            self.kpi[clave][0].setText(fmt_money(valor))
        self.kpi["ganancia"][1].setText(f"margen {margen:.1f}% sobre ventas")

        self.tabla_per.setRowCount(len(datos) + (1 if datos else 0))
        for r, d in enumerate(datos):
            fila = [etiqueta_periodo(d["periodo"], agrupar, corto=False)] + [fmt_money(d[k]) for k in claves]
            for c, v in enumerate(fila):
                self.tabla_per.setItem(r, c, celda(v, derecha=c > 0))
        if datos:  # fila de totales
            fila = ["TOTAL"] + [fmt_money(tot[k]) for k in claves]
            for c, v in enumerate(fila):
                it = celda(v, derecha=c > 0)
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                self.tabla_per.setItem(len(datos), c, it)

        self.tabla_top.setRowCount(len(top))
        for r, t in enumerate(top):
            m = t["ganancia"] / t["ingresos"] * 100 if t["ingresos"] else 0
            for c, v in enumerate([r + 1, t["nombre"], fmt_cant(t["unidades"]), fmt_money(t["ingresos"]),
                                   fmt_money(t["ganancia"]), f"{m:.1f}%"]):
                self.tabla_top.setItem(r, c, celda(v, derecha=c != 1))

        if self._mpl:
            self._datos, self._top, self._agrupar = datos, top, agrupar
            self.grafico_per.repintar()
            self.grafico_top.repintar()


# ----------------------------- gasto operativo -----------------------------
class GastoDialog(QDialog):
    def __init__(self, con, gasto=None, parent=None):
        super().__init__(parent)
        self.con, self.gasto = con, gasto
        self.hubo_cambios, self.guardado_id = False, None
        self.resumen = None  # (título, líneas) para el aviso emergente cuando se edita
        self.setWindowTitle("Editar gasto operativo" if gasto else "Nuevo gasto operativo")
        self.setMinimumWidth(560)

        self.fecha = FechaEdit()
        self.concepto = QLineEdit()
        self.concepto.setPlaceholderText("Ej. Alquiler del local, taxi al mercado, recibo de luz…")
        self.cat = ComboBuscable("Selecciona o escribe para buscar…")
        b_cat = QPushButton("+ Nueva categoría")
        b_cat.clicked.connect(self.nueva_categoria)
        fila_cat = QHBoxLayout()
        fila_cat.addWidget(self.cat, 1)
        fila_cat.addWidget(b_cat)
        self.monto = CampoNumero(2, "0.00")
        self.monto.setMaximumWidth(160)
        fila_monto = QHBoxLayout()
        fila_monto.addWidget(QLabel("S/"))
        fila_monto.addWidget(self.monto)
        fila_monto.addStretch()
        self.notas = QPlainTextEdit()
        self.notas.setPlaceholderText("Opcional")
        self.notas.setFixedHeight(70)

        form = QFormLayout()
        form.addRow("Fecha del gasto *", self.fecha)
        form.addRow("Concepto *", self.concepto)
        form.addRow("Categoría *", fila_cat)
        form.addRow("Monto (S/) *", fila_monto)
        form.addRow("Notas", self.notas)
        self.lbl_estado = QLabel()
        self.lbl_estado.setObjectName("estado")
        nota = QLabel("* Campos obligatorios.")
        nota.setObjectName("nota")
        botones = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        if not gasto:  # para cargar varios gastos seguidos
            b_otra = QPushButton("Guardar y agregar otro")
            b_otra.clicked.connect(self.guardar_y_otro)
            botones.addButton(b_otra, QDialogButtonBox.ActionRole)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.lbl_estado)
        lay.addWidget(nota)
        lay.addWidget(botones)

        llenar_categorias(self.cat, con, "gasto", gasto["categoria_id"] if gasto else None)
        self.seg = SeguimientoCambios([
            (self.fecha, lambda: self.fecha.date()),
            (self.concepto, lambda: self.concepto.text()),
            (self.cat, lambda: self.cat.currentData()),
            (self.monto, lambda: self.monto.text()),
            (self.notas, lambda: self.notas.toPlainText()),
        ])
        if gasto:
            self.fecha.setDate(QDate.fromString(gasto["fecha"], "yyyy-MM-dd"))
            self.concepto.setText(gasto["concepto"])
            self.monto.poner(gasto["monto"])
            self.notas.setPlainText(gasto["notas"])
            self.seg.activar()  # el resaltado ladrillo solo existe al EDITAR

    def nueva_categoria(self):
        txt, ok = QInputDialog.getText(self, "Nueva categoría de gasto", "Nombre:")
        if ok:
            try:
                cid = db.crear_categoria(self.con, txt, "gasto")
            except ValueError as e:
                return aviso(self, str(e))
            llenar_categorias(self.cat, self.con, "gasto", cid)

    def _guardar(self):
        monto = self.monto.valor()
        if not validar_obligatorios(self, [
            ("Concepto", self.concepto, bool(self.concepto.text().strip())),
            ("Categoría", self.cat, self.cat.currentData() is not None),
            ("Monto", self.monto, monto is not None),
        ]):
            return False
        if monto <= 0:
            marcar(self.monto, False)
            aviso(self, "El monto debe ser mayor que 0.")
            return False
        if self.gasto and not confirmar(
                self, "Vas a modificar este gasto.\n\n¿Seguro que deseas guardar los cambios?", "Confirmar edición"):
            return False
        iso, concepto, notas = (self.fecha.date().toString("yyyy-MM-dd"), self.concepto.text().strip(),
                                self.notas.toPlainText().strip())
        try:
            self.guardado_id = db.guardar_gasto(self.con, iso, concepto, self.cat.currentData(), monto,
                                                notas or None, self.gasto["id"] if self.gasto else None)
        except ValueError as e:
            aviso(self, str(e))
            return False
        self.hubo_cambios = True
        if self.gasto:  # qué se modificó, para el aviso emergente
            g, cambios = self.gasto, []
            _dif(cambios, "Fecha", g["fecha"], iso, fmt_fecha)
            _dif(cambios, "Concepto", g["concepto"], concepto, corto)
            _dif(cambios, "Categoría", g["categoria"], self.cat.currentText())
            _dif_num(cambios, "Monto", g["monto"], monto, fmt_money, 0.005)
            _dif(cambios, "Notas", g["notas"].strip(), notas, corto)
            self.resumen = resumen_final("Gasto modificado", concepto, cambios)
        return True

    def accept(self):
        if self._guardar():
            super().accept()

    def guardar_y_otro(self):
        if self._guardar():
            self.concepto.clear()  # la fecha y la categoría se conservan para el siguiente gasto
            self.monto.clear()
            self.notas.clear()
            self.lbl_estado.setText("✔ Gasto guardado. Puedes agregar otro.")
            self.concepto.setFocus()


class GastosTab(OrdenPorCabecera, FiltroFechas, QWidget):
    COLS = ["N°", "Fecha", "Concepto", "Categoría", "Monto"]
    ORDEN_COL = {1: "fecha", 2: "concepto", 3: "categoria", 4: "monto"}

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("Buscar concepto o nota…")
        self.filtro = ComboBuscable("Filtrar por categoría…")
        b_nuevo, b_edit, b_del, b_cats = (QPushButton(t) for t in
                                          ("Nuevo gasto", "Editar", "Eliminar", "Categorías…"))
        b_nuevo.clicked.connect(self.nuevo)
        b_edit.clicked.connect(self.editar)
        b_del.clicked.connect(self.eliminar)
        b_cats.clicked.connect(self.categorias)
        barra = QHBoxLayout()
        barra.addWidget(self.buscar, 2)
        barra.addWidget(self.filtro, 1)
        for b in (b_nuevo, b_edit, b_del, b_cats):
            barra.addWidget(b)

        self._init_fechas()
        b_limpiar = QPushButton("Limpiar filtros")
        b_limpiar.clicked.connect(self.limpiar_filtros)
        fila_f = self._fila_fechas()
        fila_f.addStretch()
        fila_f.addWidget(b_limpiar)

        self.tabla = crear_tabla(self.COLS)
        self.tabla.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._init_orden()
        self.tabla.doubleClicked.connect(lambda _: self.editar())
        self.lbl_total = QLabel()
        self.lbl_total.setStyleSheet("font-size: 15px; font-weight: bold;")
        fila_total = QHBoxLayout()
        fila_total.addStretch()
        fila_total.addWidget(self.lbl_total)
        self.pag = Paginador()
        self.pag.cambiado.connect(self.refrescar)

        lay = QVBoxLayout(self)
        lay.addLayout(barra)
        lay.addLayout(fila_f)
        lay.addWidget(self.tabla)
        lay.addLayout(fila_total)
        lay.addWidget(self.pag)

        self.recargar_filtro()
        self._fechas_por_defecto()
        self.buscar.textChanged.connect(lambda _: self.refrescar(True))
        self.filtro.currentIndexChanged.connect(lambda _: self.refrescar(True))
        self._conectar_fechas()
        self.refrescar()

    def _rango_fechas_datos(self):
        return db.rango_fechas_gastos(self.con)

    def _filtros(self):
        desde, hasta = self._desde_hasta()
        return self.buscar.text().strip(), self.filtro.currentData(), desde, hasta

    def limpiar_filtros(self):
        for w in (self.buscar, self.filtro):
            w.blockSignals(True)
        self.buscar.clear()
        self.filtro.setCurrentIndex(0)
        for w in (self.buscar, self.filtro):
            w.blockSignals(False)
        self._reiniciar_fechas()
        self.refrescar(True)

    def al_mostrar(self):
        self.recargar_filtro()
        self._fechas_por_defecto()
        self.refrescar()

    def recargar_filtro(self):
        actual = self.filtro.currentData()
        self.filtro.blockSignals(True)
        llenar_categorias(self.filtro, self.con, "gasto", actual, "Todas las categorías")
        self.filtro.blockSignals(False)

    def refrescar(self, reiniciar=False, seleccionar=None):
        sel = seleccionar if seleccionar is not None else id_seleccionado(self.tabla)
        if reiniciar:
            self.pag.reiniciar()
        f = self._filtros()
        self.pag.actualizar(db.contar_gastos(self.con, *f))
        # suma de TODOS los gastos que cumplen los filtros, no solo los de la página visible
        self.lbl_total.setText(f"Total de Gasto Operativo: {fmt_money(db.total_gastos(self.con, *f))}")
        filas = db.listar_gastos(self.con, *f, orden=self.orden, asc=self.asc,
                                 limite=self.pag.limite, offset=self.pag.offset)
        self.tabla.setRowCount(len(filas))
        for r, g in enumerate(filas):
            valores = [self.pag.offset + r + 1, fmt_fecha(g["fecha"]), g["concepto"], g["categoria"],
                       fmt_money(g["monto"])]
            for k, v in enumerate(valores):
                self.tabla.setItem(r, k, celda(v, derecha=k in (0, 4), dato=g["id"] if k == 0 else None,
                                               tooltip=g["notas"] if k == 2 and g["notas"] else None))
        if sel is not None:
            seleccionar_id(self.tabla, sel)

    def nuevo(self):
        dlg = GastoDialog(self.con, parent=self)
        dlg.exec()
        if dlg.hubo_cambios:
            self.restablecer_orden()  # vuelve al orden por fecha (lo más reciente primero)
            self._fechas_por_defecto()
            self.refrescar(True, dlg.guardado_id)

    def editar(self):
        gid = id_seleccionado(self.tabla)
        if gid is None:
            return aviso(self, "Selecciona un gasto.")
        dlg = GastoDialog(self.con, db.obtener_gasto(self.con, gid), self)
        dlg.exec()
        if dlg.hubo_cambios:
            self._fechas_por_defecto()
            self.refrescar(False, dlg.guardado_id)
            if dlg.resumen:
                mostrar_toast(self, *dlg.resumen)

    def eliminar(self):
        gid = id_seleccionado(self.tabla)
        if gid is None:
            return aviso(self, "Selecciona un gasto.")
        if confirmar(self, "¿Eliminar el gasto seleccionado?"):
            db.eliminar_gasto(self.con, gid)
            self._fechas_por_defecto()
            self.refrescar()

    def categorias(self):
        CategoriasDialog(self.con, self, "gasto").exec()
        self.recargar_filtro()
        self.refrescar(True)


# ----------------------------- mantenimiento (maestros) -----------------------------
class MaestroInsumoDialog(QDialog):
    """Alta / edición de un insumo del MAESTRO: nombre, descripción y categoría.
    El costo (cantidad y precio total) se define en la pestaña Insumos."""

    def __init__(self, con, insumo=None, parent=None):
        super().__init__(parent)
        self.con, self.insumo = con, insumo
        self.guardado_id = None
        self.resumen = None  # (título, líneas) para el aviso emergente cuando se edita
        self.setWindowTitle("Editar insumo" if insumo else "Nuevo insumo")
        self.setMinimumWidth(520)
        self.nombre = QLineEdit()
        self.nombre.setPlaceholderText("Ej. Rosa")
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Ej. Rosas de vivero de mercado central")
        self.desc.setFixedHeight(80)
        self.cat = ComboBuscable("Selecciona o escribe para buscar…")
        b_cat = QPushButton("+ Nueva categoría")
        b_cat.clicked.connect(self.nueva_categoria)
        fila_cat = QHBoxLayout()
        fila_cat.addWidget(self.cat, 1)
        fila_cat.addWidget(b_cat)
        form = QFormLayout()
        form.addRow("Nombre del insumo *", self.nombre)
        form.addRow("Descripción", self.desc)
        form.addRow("Categoría *", fila_cat)
        nota = QLabel("* Campos obligatorios.   El costo del insumo (cantidad y precio total) se define "
                      "en la pestaña Insumos.")
        nota.setObjectName("nota")
        nota.setWordWrap(True)
        botones = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(nota)
        lay.addWidget(botones)

        llenar_categorias(self.cat, con, "insumo", insumo["categoria_id"] if insumo else None)
        self.seg = SeguimientoCambios([
            (self.nombre, lambda: self.nombre.text()),
            (self.desc, lambda: self.desc.toPlainText()),
            (self.cat, lambda: self.cat.currentData()),
        ])
        if insumo:
            self.nombre.setText(insumo["nombre"])
            self.desc.setPlainText(insumo["descripcion"])
            self._cat_orig = self.cat.currentText()
            self.seg.activar()  # el resaltado ladrillo solo existe al EDITAR

    def nueva_categoria(self):
        txt, ok = QInputDialog.getText(self, "Nueva categoría", "Nombre:")
        if ok:
            try:
                cid = db.crear_categoria(self.con, txt, "insumo")
            except ValueError as e:
                return aviso(self, str(e))
            llenar_categorias(self.cat, self.con, "insumo", cid)

    def accept(self):
        if not validar_obligatorios(self, [
            ("Nombre", self.nombre, bool(self.nombre.text().strip())),
            ("Categoría", self.cat, self.cat.currentData() is not None),
        ]):
            return
        if self.insumo and not confirmar(
                self, f"Vas a modificar el insumo «{self.insumo['nombre']}».\n\n¿Seguro que deseas guardar los cambios?",
                "Confirmar edición"):
            return
        nombre, desc = self.nombre.text().strip(), self.desc.toPlainText().strip()
        try:
            self.guardado_id = db.guardar_insumo_maestro(self.con, nombre, desc, self.cat.currentData(),
                                                         self.insumo["id"] if self.insumo else None)
        except ValueError as e:
            return aviso(self, str(e))
        if self.insumo:  # qué se modificó, para el aviso emergente
            i, cambios = self.insumo, []
            _dif(cambios, "Nombre", i["nombre"], nombre, corto)
            _dif(cambios, "Descripción", i["descripcion"].strip(), desc, corto)
            _dif(cambios, "Categoría", self._cat_orig, self.cat.currentText())
            self.resumen = resumen_final("Insumo modificado", nombre, cambios)
        super().accept()


class MaestroInsumosDialog(OrdenPorCabecera, QDialog):
    """Maestro de insumos: de aquí salen los insumos que se usan en Histórico de Compras, Insumos y Productos."""
    COLS = ["N°", "Nombre", "Descripción", "Categoría"]
    ORDEN_COL = {1: "nombre", 2: "descripcion", 3: "categoria"}

    def __init__(self, con, parent=None):
        super().__init__(parent)
        self.con = con
        self.setWindowTitle("Mantener Insumos — maestro de insumos")
        self.resize(980, 640)
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("Buscar por nombre o descripción…")
        self.filtro = ComboBuscable("Filtrar por categoría…")
        b_nuevo, b_edit, b_del, b_cats = (QPushButton(t) for t in
                                          ("Nuevo", "Editar", "Eliminar", "Categorías…"))
        b_nuevo.clicked.connect(self.nuevo)
        b_edit.clicked.connect(self.editar)
        b_del.clicked.connect(self.eliminar)
        b_cats.clicked.connect(self.categorias)
        barra = QHBoxLayout()
        barra.addWidget(self.buscar, 2)
        barra.addWidget(self.filtro, 1)
        for b in (b_nuevo, b_edit, b_del, b_cats):
            barra.addWidget(b)

        self.tabla = crear_tabla(self.COLS)
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        self.tabla.setColumnWidth(2, 320)
        self._init_orden()
        self.tabla.doubleClicked.connect(lambda _: self.editar())
        self.pag = Paginador()
        self.pag.cambiado.connect(self.refrescar)
        nota = QLabel("Aquí se crean y mantienen los insumos. Lo que registres alimenta Histórico de Compras, "
                      "Insumos y Productos. El costo de cada insumo se define en la pestaña Insumos.")
        nota.setObjectName("nota")
        nota.setWordWrap(True)
        cerrar = QDialogButtonBox(QDialogButtonBox.Close)
        cerrar.rejected.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addLayout(barra)
        lay.addWidget(self.tabla)
        lay.addWidget(self.pag)
        lay.addWidget(nota)
        lay.addWidget(cerrar)
        self.recargar_filtro()
        self.buscar.textChanged.connect(lambda _: self.refrescar(True))
        self.filtro.currentIndexChanged.connect(lambda _: self.refrescar(True))
        self.refrescar()

    def recargar_filtro(self):
        actual = self.filtro.currentData()
        self.filtro.blockSignals(True)
        llenar_categorias(self.filtro, self.con, "insumo", actual, "Todas las categorías")
        self.filtro.blockSignals(False)

    def refrescar(self, reiniciar=False, seleccionar=None):
        sel = seleccionar if seleccionar is not None else id_seleccionado(self.tabla)
        if reiniciar:
            self.pag.reiniciar()
        texto, cat = self.buscar.text().strip(), self.filtro.currentData()
        self.pag.actualizar(db.contar_insumos(self.con, texto, cat))
        filas = db.listar_insumos(self.con, texto, cat, self.orden, self.asc, self.pag.limite, self.pag.offset)
        self.tabla.setRowCount(len(filas))
        for r, i in enumerate(filas):
            desc = i["descripcion"]
            celdas = [celda(self.pag.offset + r + 1, True, dato=i["id"]), celda(i["nombre"]),
                      celda(desc.replace("\n", " "), tooltip=desc), celda(i["categoria"])]
            for c, it in enumerate(celdas):
                self.tabla.setItem(r, c, it)
        if sel is not None:
            seleccionar_id(self.tabla, sel)

    def _tras_guardar(self, dlg):
        self.restablecer_orden()  # lo recién guardado aparece arriba, en la página 1
        self.refrescar(True, dlg.guardado_id)
        if dlg.resumen:
            mostrar_toast(self, *dlg.resumen)

    def nuevo(self):
        dlg = MaestroInsumoDialog(self.con, parent=self)
        if dlg.exec():
            self._tras_guardar(dlg)

    def editar(self):
        iid = id_seleccionado(self.tabla)
        if iid is None:
            return aviso(self, "Selecciona un insumo.")
        dlg = MaestroInsumoDialog(self.con, db.obtener_insumo(self.con, iid), self)
        if dlg.exec():
            self._tras_guardar(dlg)

    def eliminar(self):
        iid = id_seleccionado(self.tabla)
        if iid is None:
            return aviso(self, "Selecciona un insumo.")
        if confirmar(self, "¿Eliminar el insumo seleccionado?"):
            try:
                db.eliminar_insumo(self.con, iid)
            except ValueError as e:
                return aviso(self, str(e))
            self.refrescar()

    def categorias(self):
        CategoriasDialog(self.con, self, "insumo").exec()
        self.recargar_filtro()
        self.refrescar(True)


class MantenimientoTab(QWidget):
    """Centro de mantenimiento: cada botón abre la pantalla de un maestro de datos.
    Para sumar otro maestro más adelante basta con llamar a agregar_opcion()."""

    def __init__(self, con):
        super().__init__()
        self.con = con
        titulo = QLabel("Mantenimiento")
        titulo.setStyleSheet("font-size: 22px; font-weight: bold;")
        sub = QLabel("Aquí se mantienen los datos maestros. Lo que registres aquí alimenta las demás pestañas.")
        sub.setObjectName("nota")
        self.rejilla = QGridLayout()
        self.rejilla.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._n = 0
        lay = QVBoxLayout(self)
        lay.addWidget(titulo)
        lay.addWidget(sub)
        lay.addSpacing(12)
        lay.addLayout(self.rejilla)
        lay.addStretch()
        self.agregar_opcion("Mantener Insumos",
                            "Maestro de insumos: nombre, descripción y categoría. De aquí salen los insumos "
                            "de Histórico de Compras, Insumos y Productos.", self.abrir_insumos)

    def agregar_opcion(self, texto, descripcion, accion):
        marco = QFrame()
        marco.setObjectName("kpi")
        marco.setFixedWidth(340)
        lay = QVBoxLayout(marco)
        lay.setContentsMargins(14, 12, 14, 12)
        boton = QPushButton(texto)
        boton.setMinimumHeight(46)
        boton.clicked.connect(lambda _=False: accion())
        desc = QLabel(descripcion)
        desc.setObjectName("kpi_sub")
        desc.setWordWrap(True)
        lay.addWidget(boton)
        lay.addWidget(desc)
        self.rejilla.addWidget(marco, self._n // 3, self._n % 3)
        self._n += 1

    def abrir_insumos(self):
        MaestroInsumosDialog(self.con, self).exec()

    def al_mostrar(self):
        pass


# ----------------------------- inicio -----------------------------
class InicioTab(QWidget):
    """Pantalla de inicio: resumen del mes y accesos rápidos a las demás pestañas."""
    MESES_LARGO = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
                   "Septiembre", "Octubre", "Noviembre", "Diciembre"]

    def __init__(self, con, accesos, navegar):
        super().__init__()
        self.con = con
        titulo = QLabel("Florería — Inicio")
        titulo.setStyleSheet("font-size: 22px; font-weight: bold;")
        self.lbl_mes = QLabel()
        self.lbl_mes.setObjectName("nota")
        fila_k = QHBoxLayout()
        self.kpi = {}
        for clave, tit in (("ventas", "Ventas del mes"), ("insumos", "Compras de insumos"),
                           ("operativo", "Gasto operativo"), ("resultado", "Resultado del mes")):
            marco, valor, sub = ReportesTab._crear_kpi(tit, "")
            self.kpi[clave] = (valor, sub)
            fila_k.addWidget(marco)
        rejilla = QGridLayout()
        for n, (texto, widget) in enumerate(accesos):
            b = QPushButton(texto)
            b.setMinimumHeight(46)
            b.clicked.connect(lambda _=False, w=widget: navegar(w))
            rejilla.addWidget(b, n // 4, n % 4)
        lay = QVBoxLayout(self)
        lay.addWidget(titulo)
        lay.addWidget(self.lbl_mes)
        lay.addLayout(fila_k)
        lay.addSpacing(14)
        lay.addWidget(QLabel("<b>Accesos rápidos</b>"))
        lay.addLayout(rejilla)
        lay.addStretch()
        self.refrescar()

    def refrescar(self):
        hoy = QDate.currentDate()
        desde, hasta = f"{hoy.year()}-{hoy.month():02d}-01", f"{hoy.year()}-{hoy.month():02d}-31"
        self.lbl_mes.setText(f"Resumen de {self.MESES_LARGO[hoy.month() - 1]} {hoy.year()}")
        datos = db.resumen_periodos(self.con, desde, hasta, "mes")
        suma = lambda k: sum(d[k] for d in datos)
        n_v = db.contar_ventas(self.con, "", None, desde, hasta)
        n_c = db.contar_compras(self.con, "", None, desde, hasta)
        n_g = db.contar_gastos(self.con, "", None, desde, hasta)
        for clave, valor, sub in (("ventas", suma("ingresos"), f"{n_v} venta(s)"),
                                  ("insumos", suma("gasto_insumos"), f"{n_c} compra(s)"),
                                  ("operativo", suma("gasto_operativo"), f"{n_g} gasto(s) registrado(s)"),
                                  ("resultado", suma("resultado"), "ventas − compras − gastos")):
            self.kpi[clave][0].setText(fmt_money(valor))
            self.kpi[clave][1].setText(sub)

    def al_mostrar(self):
        self.refrescar()


# ----------------------------- pestañas en blanco -----------------------------
class PestanaVacia(QWidget):
    """Pestaña sin contenido todavía: solo una leyenda centrada."""

    def __init__(self, texto):
        super().__init__()
        lbl = QLabel(texto)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setObjectName("nota")
        lbl.setStyleSheet("font-size: 20px;")
        lay = QVBoxLayout(self)
        lay.addWidget(lbl)

    def al_mostrar(self):
        pass


# ----------------------------- tema (colores) -----------------------------
# Los colores viven en tema.json (junto a app.py). Se editan con el Bloc de notas, sin tocar el código.
TEMA_PATH = db.BASE / "tema.json"

TEMAS_INICIALES = {
    "claro": {
        "fondo": "#FDF8F6", "superficie": "#FFFFFF", "texto": "#3B2F2F", "texto_secundario": "#8C7B76",
        "principal": "#C2185B", "principal_hover": "#A01449", "texto_boton": "#FFFFFF",
        "encabezado": "#2E7D5B", "texto_encabezado": "#FFFFFF", "fila_alterna": "#FBEFEA",
        "seleccion": "#F8BBD0", "borde": "#D9BFB7", "pestana": "#F1E3DE",
        "deshabilitado_fondo": "#D9C9C4", "deshabilitado_texto": "#8C7B76", "margen_editable": "#FFF4CC",
    },
    "oscuro": {
        "fondo": "#1E1B1C", "superficie": "#2A2627", "texto": "#F2E9E6", "texto_secundario": "#A89B97",
        "principal": "#E0508A", "principal_hover": "#C23D74", "texto_boton": "#FFFFFF",
        "encabezado": "#2E7D5B", "texto_encabezado": "#FFFFFF", "fila_alterna": "#322D2E",
        "seleccion": "#6A2B45", "borde": "#4A4244", "pestana": "#2F2A2B",
        "deshabilitado_fondo": "#4A4244", "deshabilitado_texto": "#8C7F7B", "margen_editable": "#4A4020",
    },
    "azul": {
        "fondo": "#F4F8FC", "superficie": "#FFFFFF", "texto": "#1F2D3D", "texto_secundario": "#6B7C8F",
        "principal": "#1E6FD9", "principal_hover": "#1859B0", "texto_boton": "#FFFFFF",
        "encabezado": "#2C5282", "texto_encabezado": "#FFFFFF", "fila_alterna": "#EAF1FA",
        "seleccion": "#BBD6F7", "borde": "#C5D3E3", "pestana": "#E3ECF7",
        "deshabilitado_fondo": "#CBD5E1", "deshabilitado_texto": "#7B8794", "margen_editable": "#FFF4CC",
    },
}


for _t in TEMAS_INICIALES.values():  # color de "cambio sin guardar" (ladrillo) y su texto
    _t.setdefault("cambio", "#B5523B")
    _t.setdefault("cambio_texto", "#FFFFFF")
TEMAS_INICIALES["oscuro"]["cambio"] = "#B84A33"


def _config_inicial():
    return {"tema_activo": "claro", "temas": TEMAS_INICIALES}


def _crear_archivo_tema():
    """Crea tema.json la primera vez, para que sepas cuál archivo editar."""
    if not TEMA_PATH.exists():
        try:
            TEMA_PATH.write_text(json.dumps(_config_inicial(), indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _leer_config():
    """Lee tema.json; si no existe o tiene un error, usa los temas iniciales."""
    try:
        cfg = json.loads(TEMA_PATH.read_text(encoding="utf-8"))
        if not isinstance(cfg["temas"], dict) or not cfg["temas"]:
            raise ValueError
        return cfg
    except (OSError, ValueError, KeyError, TypeError):
        return _config_inicial()


def temas_disponibles():
    return list(_leer_config()["temas"].keys())


def tema_activo():
    cfg = _leer_config()
    nombre = cfg.get("tema_activo")
    return nombre if nombre in cfg["temas"] else next(iter(cfg["temas"]))


def guardar_tema_activo(nombre):
    try:  # si tema.json tiene un error, no se sobrescribe (para no perder lo que escribiste)
        cfg = json.loads(TEMA_PATH.read_text(encoding="utf-8"))
        cfg["tema_activo"] = nombre
        TEMA_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def cargar_colores():
    """Colores del tema activo. Lo que falte o sea inválido se completa con el tema 'claro'."""
    cfg = _leer_config()
    elegido = cfg["temas"].get(tema_activo())
    base = dict(TEMAS_INICIALES["claro"])
    if isinstance(elegido, dict):
        base.update(elegido)
    for k, v in list(base.items()):
        if not QColor(str(v)).isValid():
            base[k] = TEMAS_INICIALES["claro"].get(k, "#808080")
    return base


def crear_paleta(c):
    """Paleta explícita: cubre lo que la hoja de estilos no alcanza (calendario, barras de desplazamiento…)
    y evita que el modo oscuro de Windows se mezcle con nuestros colores."""
    p = QPalette()
    for rol, clave in ((QPalette.Window, "fondo"), (QPalette.WindowText, "texto"),
                       (QPalette.Base, "superficie"), (QPalette.AlternateBase, "fila_alterna"),
                       (QPalette.Text, "texto"), (QPalette.Button, "principal"),
                       (QPalette.ButtonText, "texto_boton"), (QPalette.Highlight, "seleccion"),
                       (QPalette.HighlightedText, "texto"), (QPalette.ToolTipBase, "superficie"),
                       (QPalette.ToolTipText, "texto"), (QPalette.PlaceholderText, "texto_secundario")):
        p.setColor(rol, QColor(c[clave]))
    return p


def generar_qss(c):
    return f"""
QWidget {{ color: {c['texto']}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {c['fondo']}; }}
QMenuBar {{ background: {c['fondo']}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {c['seleccion']}; }}
QMenu {{ background: {c['superficie']}; border: 1px solid {c['borde']}; }}
QToolTip {{ background: {c['superficie']}; color: {c['texto']}; border: 1px solid {c['borde']}; padding: 4px; }}
QLabel#nota {{ color: {c['texto_secundario']}; }}
QLineEdit[cambiado="true"], QPlainTextEdit[cambiado="true"], QComboBox[cambiado="true"],
QDateEdit[cambiado="true"], QCheckBox[cambiado="true"] {{ background: {c['cambio']}; color: {c['cambio_texto']}; }}
QFrame#toast {{ background: {c['superficie']}; border: 2px solid {c['cambio']}; border-radius: 8px; }}
QLabel#toast_titulo {{ color: {c['cambio']}; font-weight: bold; font-size: 14px; }}
QLabel#toast_pie {{ color: {c['texto_secundario']}; font-size: 11px; }}
QFrame#kpi {{ background: {c['superficie']}; border: 1px solid {c['borde']}; border-radius: 8px; }}
QLabel#kpi_titulo {{ color: {c['texto_secundario']}; font-size: 12px; }}
QLabel#kpi_valor {{ font-size: 20px; font-weight: bold; }}
QLabel#kpi_sub {{ color: {c['texto_secundario']}; font-size: 11px; }}
QLabel#estado {{ color: {c['encabezado']}; font-weight: bold; }}
QTabWidget::pane {{ border: 1px solid {c['borde']}; background: {c['fondo']}; }}
QTabBar::tab {{ background: {c['pestana']}; padding: 8px 20px; margin-right: 2px;
               border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QTabBar::tab:selected {{ background: {c['principal']}; color: {c['texto_boton']}; }}
QPushButton {{ background: {c['principal']}; color: {c['texto_boton']}; border: none;
              padding: 6px 14px; border-radius: 5px; }}
QPushButton:hover {{ background: {c['principal_hover']}; }}
QPushButton:disabled {{ background: {c['deshabilitado_fondo']}; color: {c['deshabilitado_texto']}; }}
QHeaderView::section {{ background: {c['encabezado']}; color: {c['texto_encabezado']}; padding: 6px; border: none; }}
QTableWidget {{ background: {c['superficie']}; alternate-background-color: {c['fila_alterna']};
               gridline-color: {c['borde']}; selection-background-color: {c['seleccion']};
               selection-color: {c['texto']}; }}
QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QListWidget {{
    background: {c['superficie']}; border: 1px solid {c['borde']}; border-radius: 4px; padding: 4px; }}
QLineEdit:read-only {{ background: {c['fondo']}; }}
QComboBox QAbstractItemView {{ background: {c['superficie']}; color: {c['texto']};
                               selection-background-color: {c['seleccion']}; selection-color: {c['texto']}; }}
"""


def aplicar_tema():
    app = QApplication.instance()
    COL.clear()
    COL.update(cargar_colores())
    try:  # Qt 6.8+: también ajusta la barra de título de la ventana; en versiones anteriores se ignora
        claro = QColor(COL["fondo"]).lightness() > 128
        app.styleHints().setColorScheme(Qt.ColorScheme.Light if claro else Qt.ColorScheme.Dark)
    except Exception:
        pass
    app.setPalette(crear_paleta(COL))
    app.setStyleSheet(generar_qss(COL))


# ----------------------------- ventana principal -----------------------------
class MainWindow(QMainWindow):
    def __init__(self, con):
        super().__init__()
        self.setWindowTitle("Florería — Gestión")
        self.resize(1200, 700)
        self.mantenimiento = MantenimientoTab(con)
        self.compras = ComprasTab(con)
        self.insumos = InsumosTab(con)
        self.productos = ProductosTab(con)
        self.ventas = VentasTab(con)
        self.gastos = GastosTab(con)
        self.reportes = ReportesTab(con)
        self.tabs = QTabWidget()
        for widget, nombre in ((self.mantenimiento, "Mantenimiento"), (self.compras, "Histórico de Compras"),
                               (self.insumos, "Insumos"), (self.productos, "Productos"), (self.ventas, "Ventas"),
                               (self.gastos, "Gasto Operativo"), (self.reportes, "Reportes")):
            self.tabs.addTab(widget, nombre)
        # la pestaña Inicio es la primera; sus botones llevan a las demás
        accesos = [(self.tabs.tabText(i), self.tabs.widget(i)) for i in range(self.tabs.count())]
        self.inicio = InicioTab(con, accesos, self.tabs.setCurrentWidget)
        self.tabs.insertTab(0, self.inicio, "Inicio")
        self.tabs.setCurrentIndex(0)
        self.setCentralWidget(self.tabs)
        # al cambiar de pestaña, se recargan los datos (precios, categorías, nombres…)
        self.tabs.currentChanged.connect(lambda i: self.tabs.widget(i).al_mostrar())
        self.menu_apariencia = self.menuBar().addMenu("Apariencia")
        self._armar_menu()

    def _armar_menu(self):
        self.menu_apariencia.clear()
        self._grupo = QActionGroup(self)
        self._grupo.setExclusive(True)
        activo = tema_activo()
        for nombre in temas_disponibles():
            acc = QAction(nombre.capitalize(), self)
            acc.setCheckable(True)
            acc.setChecked(nombre == activo)
            acc.triggered.connect(lambda _=False, n=nombre: self.cambiar_tema(n))
            self._grupo.addAction(acc)
            self.menu_apariencia.addAction(acc)
        self.menu_apariencia.addSeparator()
        recargar = QAction("Recargar colores desde tema.json", self)
        recargar.triggered.connect(lambda _=False: self.recargar_colores())
        self.menu_apariencia.addAction(recargar)

    def _repintar(self):
        aplicar_tema()
        self.productos.refrescar()  # recolorea la celda editable del margen
        self.reportes.al_mostrar()  # redibuja los gráficos con los colores del tema

    def cambiar_tema(self, nombre):
        guardar_tema_activo(nombre)
        self._repintar()

    def recargar_colores(self):
        self._repintar()
        self._armar_menu()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _crear_archivo_tema()
    aplicar_tema()
    con = db.conectar()
    win = MainWindow(con)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
