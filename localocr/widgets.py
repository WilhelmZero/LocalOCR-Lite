from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPixmap, QPen, QColor, QPolygonF, QPainter, QBrush
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPolygonItem,
                             QGraphicsRectItem, QTableWidget, QTableWidgetItem,
                             QAbstractItemView, QHeaderView)

from .core import table_shape, fill_table


class ImageView(QGraphicsView):
    selected = Signal(str)
    region = Signal(list)
    corners_selected = Signal(list)
    color_selected = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#e8eeec"))
        self.overlays = {}
        self.select_region = False
        self.pick_corners, self.corner_points = False, []
        self.pick_color=False
        self.pixel_image=None
        self.origin, self.selection, self.image_rect = None, None, QRectF()

    def load(self, path, overlays):
        self.load_pixmap(QPixmap(str(path)),overlays)

    def load_pixmap(self, pixmap, overlays):
        self.scene().clear()
        self.pick_corners, self.corner_points, self.select_region, self.origin = False, [], False, None
        self.pick_color=False
        self.overlays = {}
        self.selection = None
        self.pixel_image=pixmap.toImage()
        if pixmap.isNull():
            self.scene().addText("图片无法加载")
            return
        self.scene().addPixmap(pixmap)
        self.image_rect = QRectF(0, 0, pixmap.width(), pixmap.height())
        self.scene().setSceneRect(self.image_rect)
        self.resetTransform()
        for key, poly, doubtful in overlays:
            if not poly:
                continue
            item = QGraphicsPolygonItem(QPolygonF([QPointF(*p) for p in poly]))
            item.setData(0, key)
            item.setPen(QPen(QColor("#d48a26" if doubtful else "#409c87"), 1.5))
            item.setBrush(QBrush(QColor(220, 160, 45, 20) if doubtful else QColor(30, 145, 116, 10)))
            self.scene().addItem(item)
            self.overlays[key] = item
        self.fit()

    def fit(self):
        if not self.image_rect.isEmpty():
            self.fitInView(self.image_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def highlight(self, key):
        for k, item in self.overlays.items():
            item.setZValue(2 if k == key else 1)
            item.setBrush(QBrush(QColor(26, 128, 100, 70 if k == key else 10)))
            item.setPen(QPen(QColor("#08735a" if k == key else "#6da693"), 3 if k == key else 1))
        if key in self.overlays:
            self.ensureVisible(self.overlays[key], 30, 30)

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1/1.2
        current = self.transform().m11()
        # Rotation can make m11 zero; use vector magnitude instead.
        scale = (current**2 + self.transform().m12()**2)**0.5
        if 0.025 < scale*factor < 15:
            self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = self.mapToScene(event.position().toPoint())
            if self.pick_color:
                if self.image_rect.contains(pos) and self.pixel_image is not None:
                    color=self.pixel_image.pixelColor(min(self.pixel_image.width()-1,int(pos.x())),min(self.pixel_image.height()-1,int(pos.y())))
                    self.pick_color=False
                    self.color_selected.emit([color.red(),color.green(),color.blue()])
                return
            if self.pick_corners:
                if self.image_rect.contains(pos):
                    self.corner_points.append([pos.x(),pos.y()])
                    self.scene().addEllipse(pos.x()-4,pos.y()-4,8,8,QPen(QColor('#d04b35')),QBrush(QColor('#d04b35')))
                    if len(self.corner_points)==4:
                        self.pick_corners = False
                        self.corners_selected.emit(self.corner_points)
                return
            if self.select_region:
                self.origin = pos
                if self.selection:
                    self.scene().removeItem(self.selection)
                self.selection = QGraphicsRectItem()
                self.selection.setPen(QPen(QColor("#1b7863"), 2, Qt.PenStyle.DashLine))
                self.scene().addItem(self.selection)
                return
            item = self.itemAt(event.position().toPoint())
            if item and item.data(0):
                self.selected.emit(item.data(0))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            rect = QRectF(self.origin, self.mapToScene(event.position().toPoint())).normalized().intersected(self.image_rect)
            self.selection.setRect(rect)
            return
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.pick_color=False
            self.pick_corners, self.select_region, self.origin = False, False, None
            self.corner_points = []
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.origin is not None:
            rect = self.selection.rect()
            self.origin = None
            self.select_region = False
            if rect.width() > 3 and rect.height() > 3:
                self.region.emit([rect.left(), rect.top(), rect.right(), rect.bottom()])
            return
        super().mouseReleaseEvent(event)


class TableGrid(QTableWidget):
    edited = Signal()
    cell_selected = Signal(str)

    def __init__(self, table, threshold=.85, parent=None):
        super().__init__(parent)
        self.table = table
        self.threshold = threshold
        self.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setDefaultSectionSize(150)
        self.itemChanged.connect(self.edit_cell)
        self.currentCellChanged.connect(self.select_cell)
        self.refresh()

    def refresh(self):
        self.blockSignals(True)
        self.clearSpans()
        self.clearContents()
        fill_table(self.table)
        rows, cols = table_shape(self.table)
        self.setRowCount(rows)
        self.setColumnCount(cols)
        for cell in self.table["cells"]:
            r, c = cell["row"], cell["col"]
            item = QTableWidgetItem(cell["text"])
            confidence = cell.get("confidence")
            item.setToolTip("置信度未知" if confidence is None else f"识别置信度 {confidence:.1%}")
            if confidence is None or confidence < self.threshold:
                item.setBackground(QColor("#fff4dc"))
            self.setItem(r, c, item)
            if cell["rowspan"] > 1 or cell["colspan"] > 1:
                self.setSpan(r, c, cell["rowspan"], cell["colspan"])
        self.resizeRowsToContents()
        self.blockSignals(False)

    def edit_cell(self, item):
        for cell in self.table["cells"]:
            if (cell["row"], cell["col"]) == (item.row(), item.column()):
                cell["text"] = item.text()
                self.edited.emit()
                return

    def select_cell(self, row, col, *_):
        for cell in self.table["cells"]:
            if cell["row"] <= row < cell["row"]+cell["rowspan"] and cell["col"] <= col < cell["col"]+cell["colspan"]:
                self.cell_selected.emit(f"{self.table['id']}:{cell['row']}:{cell['col']}")
                return
