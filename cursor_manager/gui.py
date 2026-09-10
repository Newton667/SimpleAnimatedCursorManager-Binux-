"""Qt (PySide6) desktop app for Cursor Manager."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QRadioButton, QScrollArea,
                               QSizePolicy, QSpinBox, QSplitter, QStatusBar, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from . import __version__, config, daemon, sets as sets_mod, startup, theme
from .formats import Cursor, Frame, load_cursor, scale_frame
from .sets import ROLE_LABELS, ROLES, CursorSet

LAUNCHER = config.PROJECT_DIR / "cursor-manager"
APP_ICON = config.PROJECT_DIR / "assets" / "cursor-manager.svg"
SINGLE_INSTANCE_KEY = f"{config.APP_ID}-gui-{os.getuid()}"
TEST_ROLES = [r for r in ROLES if r not in ("person", "pin")]
QT_SHAPES = {
    "default": Qt.CursorShape.ArrowCursor, "help": Qt.CursorShape.WhatsThisCursor,
    "progress": Qt.CursorShape.BusyCursor, "wait": Qt.CursorShape.WaitCursor,
    "crosshair": Qt.CursorShape.CrossCursor, "text": Qt.CursorShape.IBeamCursor,
    "pencil": None, "not-allowed": Qt.CursorShape.ForbiddenCursor,
    "ns-resize": Qt.CursorShape.SizeVerCursor, "ew-resize": Qt.CursorShape.SizeHorCursor,
    "nwse-resize": Qt.CursorShape.SizeFDiagCursor, "nesw-resize": Qt.CursorShape.SizeBDiagCursor,
    "move": Qt.CursorShape.SizeAllCursor, "alternate": Qt.CursorShape.UpArrowCursor,
    "pointer": Qt.CursorShape.PointingHandCursor,
}
UNITS = [("seconds", 1), ("minutes", 60), ("hours", 3600)]


# --------------------------------------------------------------------------- rendering helpers

def frame_to_pixmap(frame: Frame, mark_hotspot=False) -> QPixmap:
    img = frame.image
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888).copy()
    pm = QPixmap.fromImage(qimg)
    if mark_hotspot:
        p = QPainter(pm)
        p.setPen(QPen(QColor(255, 40, 40), 1))
        x, y = frame.xhot, frame.yhot
        p.drawLine(x - 4, y, x + 4, y)
        p.drawLine(x, y - 4, x, y + 4)
        p.end()
    return pm


class CursorAnim:
    """A cursor pre-rendered at a pixel size; frames selected by a shared clock."""

    def __init__(self, cursor: Cursor, px: int, dpr: float = 1.0, mark_hotspot=False):
        self.frames: List[Tuple[QPixmap, int, int]] = []
        self.delays: List[int] = []
        for fr in cursor.frames:
            s = scale_frame(fr, max(1, round(px * dpr)))
            pm = frame_to_pixmap(s, mark_hotspot)
            pm.setDevicePixelRatio(dpr)
            self.frames.append((pm, round(s.xhot / dpr), round(s.yhot / dpr)))
            self.delays.append(max(20, fr.delay_ms) if cursor.animated else 0)
        self.total = sum(self.delays)
        self._qcursors: Dict[int, QCursor] = {}

    def index_at(self, t_ms: float) -> int:
        if len(self.frames) < 2 or self.total <= 0:
            return 0
        t = t_ms % self.total
        for i, d in enumerate(self.delays):
            if t < d:
                return i
            t -= d
        return len(self.frames) - 1

    def qcursor(self, i: int) -> QCursor:
        if i not in self._qcursors:
            pm, xh, yh = self.frames[i]
            self._qcursors[i] = QCursor(pm, xh, yh)
        return self._qcursors[i]


class Tile(QFrame):
    """Icon preview tile with a role label."""

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def __init__(self, label: str, size: int = 88):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.anim: Optional[CursorAnim] = None
        self._idx = -1
        self.img = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.img.setFixedSize(size, size)
        self.img.setStyleSheet("background-color: #8c8c8c; border-radius: 4px;")
        self.text = QLabel(label, alignment=Qt.AlignmentFlag.AlignCenter)
        self.text.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.img, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.text)

    def set_anim(self, anim: Optional[CursorAnim]):
        self.anim = anim
        self._idx = -1
        if anim is None:
            self.img.clear()
        self.tick(0)

    def tick(self, t_ms: float):
        if self.anim is None:
            return
        i = self.anim.index_at(t_ms)
        if i != self._idx:
            self._idx = i
            self.img.setPixmap(self.anim.frames[i][0])


class TestZone(QFrame):
    """An area that shows a given cursor when hovered."""

    def __init__(self, label: str, widget: Optional[QWidget] = None):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.anim: Optional[CursorAnim] = None
        self._idx = -1
        self.inner = widget
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        if widget is not None:
            lay.addWidget(widget)
        self.caption = QLabel(label, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.caption)

    def _apply(self, cur: Optional[QCursor], shape: Optional[Qt.CursorShape] = None):
        targets = [self] + ([self.inner] if self.inner is not None else [])
        for w in targets:
            if cur is not None:
                w.setCursor(cur)
            elif shape is not None:
                w.setCursor(QCursor(shape))
            else:
                w.unsetCursor()

    def set_anim(self, anim: Optional[CursorAnim]):
        self.anim = anim
        self._idx = -1
        if anim is None:
            self._apply(None)
        else:
            self.tick(0)

    def set_shape(self, shape: Optional[Qt.CursorShape]):
        self.anim = None
        self._idx = -1
        self._apply(None, shape)

    def tick(self, t_ms: float):
        if self.anim is None:
            return
        i = self.anim.index_at(t_ms)
        if i != self._idx:
            self._idx = i
            self._apply(self.anim.qcursor(i))


def app_icon() -> QIcon:
    icon = QIcon(str(APP_ICON)) if APP_ICON.exists() else QIcon()
    return icon if not icon.isNull() else QIcon.fromTheme("input-mouse")


# --------------------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cursor Manager")
        self.resize(1500, 1000)
        self.setMinimumSize(1100, 760)
        self.cfg = config.load_config()
        self.sets: List[CursorSet] = []
        self.current: Optional[CursorSet] = None
        self._cursor_cache: Dict[Tuple[str, str, int, int], Cursor] = {}
        self._loading_ui = False
        self.t0 = time.monotonic()

        self._build_ui()
        self._size_timer = QTimer(self)           # debounce spinbox changes before rebuilding
        self._size_timer.setSingleShot(True)
        self._size_timer.setInterval(700)
        self._size_timer.timeout.connect(self._push_active_set)
        self._build_tray()
        self._tray_theme = None
        self._quitting = False
        self.clock = QTimer(self)
        self.clock.setInterval(33)
        self.clock.timeout.connect(self._tick)
        self.clock.start()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start()
        self.rescan(notify_daemon=False)
        self._refresh_status()

    # ---- UI construction -------------------------------------------------
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        # far left: sidebar with the app identity and places to get cursors
        splitter.addWidget(self._build_sidebar())

        # left: set list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        hdr = QLabel("<b>Cursor sets</b>")
        ll.addWidget(hdr)
        frow = QHBoxLayout()
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: palette(mid);")
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        folder_btn = QPushButton("Change folder…")
        folder_btn.setToolTip("Pick the folder that holds your cursor files and zips")
        folder_btn.clicked.connect(self.choose_folder)
        frow.addWidget(self.folder_label, 1)
        frow.addWidget(folder_btn, 0, Qt.AlignmentFlag.AlignTop)
        ll.addLayout(frow)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cursor sets…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(QIcon.fromTheme("edit-find"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter_list)
        ll.addWidget(self.search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(32, 32))
        self.list.currentItemChanged.connect(self._on_select)
        self.list.itemChanged.connect(self._on_item_changed)
        ll.addWidget(self.list, 1)
        hint = QLabel("Checked sets take part in the rotation.\n"
                      "Add .ani / .cur / .png / .gif files, folders or cursor-pack .zip files to the folder.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        ll.addWidget(hint)
        row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply now")
        self.apply_btn.setToolTip("Use this set as the system cursor right away")
        self.apply_btn.clicked.connect(self.apply_selected)
        rescan_btn = QPushButton("Rescan")
        rescan_btn.clicked.connect(self.rescan)
        row.addWidget(self.apply_btn)
        row.addWidget(rescan_btn)
        ll.addLayout(row)
        splitter.addWidget(left)

        # right: details
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("<h2>No cursor set selected</h2>")
        self.info = QLabel("")
        self.info.setStyleSheet("color: palette(mid);")
        rl.addWidget(self.title)
        rl.addWidget(self.info)

        icons_box = QGroupBox("Icons in this set")
        ib = QVBoxLayout(icons_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        grid_host = QWidget()
        self.tile_grid = QGridLayout(grid_host)
        self.tile_grid.setSpacing(6)
        self.tiles: Dict[str, Tile] = {}
        for i, role in enumerate(ROLES):
            t = Tile(ROLE_LABELS[role])
            self.tiles[role] = t
            self.tile_grid.addWidget(t, i // 6, i % 6)
        scroll.setWidget(grid_host)
        scroll.setMinimumHeight(420)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.icon_scroll = scroll
        ib.addWidget(scroll)
        hs_row = QHBoxLayout()
        self.hotspot_widget = QWidget()
        self.hotspot_widget.setLayout(hs_row)
        hs_row.setContentsMargins(0, 0, 0, 0)
        hs_row.addWidget(QLabel("Hotspot (click point) X:"))
        self.hot_x = QSpinBox()
        self.hot_y = QSpinBox()
        for sb in (self.hot_x, self.hot_y):
            sb.setRange(0, 255)
            sb.valueChanged.connect(self._on_hotspot_changed)
        hs_row.addWidget(self.hot_x)
        hs_row.addWidget(QLabel("Y:"))
        hs_row.addWidget(self.hot_y)
        hs_row.addWidget(QLabel("(plain images have no built-in hotspot; the red cross shows it)"))
        hs_row.addStretch(1)
        ib.addWidget(self.hotspot_widget)
        rl.addWidget(icons_box, 3)

        test_box = QGroupBox("Test area - hover to try each cursor")
        tb = QVBoxLayout(test_box)
        mode_row = QHBoxLayout()
        self.mode_set = QRadioButton("Preview the selected set")
        self.mode_sys = QRadioButton("Show what is applied system-wide right now")
        self.mode_set.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.mode_set)
        grp.addButton(self.mode_sys)
        self.mode_set.toggled.connect(lambda _: self._refresh_test_area())
        mode_row.addWidget(self.mode_set)
        mode_row.addWidget(self.mode_sys)
        mode_row.addStretch(1)
        tb.addLayout(mode_row)
        zone_grid = QGridLayout()
        zone_grid.setSpacing(6)
        self.zones: Dict[str, TestZone] = {}
        for i, role in enumerate(TEST_ROLES):
            inner = None
            if role == "text":
                inner = QLineEdit("Click here and type something")
            elif role == "pointer":
                inner = QPushButton("A link / button")
                inner.setFlat(True)
                inner.setStyleSheet("color: palette(link); text-decoration: underline;")
            z = TestZone(ROLE_LABELS[role], inner)
            self.zones[role] = z
            zone_grid.addWidget(z, i // 5, i % 5)
        tb.addLayout(zone_grid, 1)
        rl.addWidget(test_box, 2)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([260, 360, 880])

        # bottom: rotation settings
        rot = QGroupBox("Rotation")
        rot_row = QHBoxLayout(rot)
        controls = QWidget()
        rb = QVBoxLayout(controls)
        rb.setContentsMargins(0, 0, 0, 0)
        rot_row.addWidget(controls, 1)
        self.now_tile = Tile("Current cursor", 140)
        self.now_tile.setToolTip("The Normal cursor of the set applied to the desktop right now.\n"
                                 "Click to jump to it in the Cursor sets list.")
        self.now_tile.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.now_tile.clicked.connect(self.jump_to_current)
        self.now_tile.text.setStyleSheet("font-weight: 600;")
        rot_row.addWidget(self.now_tile, 0, Qt.AlignmentFlag.AlignTop)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Change cursor every"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 999)
        self.interval_unit = QComboBox()
        for name, _ in UNITS:
            self.interval_unit.addItem(name)
        self._load_interval()
        self.interval_spin.valueChanged.connect(self._on_interval_changed)
        self.interval_unit.currentIndexChanged.connect(self._on_interval_changed)
        r1.addWidget(self.interval_spin)
        r1.addWidget(self.interval_unit)
        r1.addSpacing(16)
        r1.addWidget(QLabel("Order:"))
        self.order_combo = QComboBox()
        self.order_combo.addItems(["In order", "Shuffle"])
        self.order_combo.setCurrentIndex(1 if self.cfg.get("shuffle") else 0)
        self.order_combo.currentIndexChanged.connect(self._on_order_changed)
        r1.addWidget(self.order_combo)
        r1.addSpacing(16)
        r1.addWidget(QLabel("Cursor size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(16, 128)
        self.size_spin.setSuffix(" px")
        self.size_spin.setValue(int(self.cfg["cursor_size"]))
        self.size_spin.setToolTip("Pixel size on screen. 32 px shows Windows cursors at their native size.")
        self.size_spin.valueChanged.connect(self._on_size_changed)
        r1.addWidget(self.size_spin)
        r1.addStretch(1)
        rb.addLayout(r1)
        r2 = QHBoxLayout()
        self.start_btn = QPushButton("Start rotation")
        self.start_btn.clicked.connect(self.toggle_rotation)
        self.next_btn = QPushButton("Next cursor")
        self.next_btn.clicked.connect(self.next_cursor)
        self.restore_btn = QPushButton("Restore original cursor")
        self.restore_btn.clicked.connect(self.restore_original)
        self.autostart_cb = QCheckBox("Start rotation when I log in")
        self.autostart_cb.setChecked(startup.autostart_enabled())
        self.autostart_cb.toggled.connect(self._on_autostart)
        r2.addWidget(self.start_btn)
        r2.addWidget(self.next_btn)
        r2.addWidget(self.restore_btn)
        r2.addSpacing(16)
        r2.addWidget(self.autostart_cb)
        r2.addStretch(1)
        self.defaults_btn = QPushButton("Reset to defaults")
        self.defaults_btn.setToolTip("Every 10 minutes, in order, 32 px cursor size")
        self.defaults_btn.clicked.connect(self.reset_defaults)
        r2.addWidget(self.defaults_btn)
        rb.addLayout(r2)
        self.status_label = QLabel()
        rb.addWidget(self.status_label)
        outer.addWidget(rot, 0)
        self.setStatusBar(QStatusBar())

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        side.setMinimumWidth(240)
        side.setMaximumWidth(340)
        side.setStyleSheet("""
            #sidebar { background: palette(alternate-base); }
            #sidebar QFrame#card { background: palette(base); border: 1px solid palette(mid); border-radius: 8px; }
            #sidebar QLabel#cardTitle { font-weight: 600; font-size: 11pt; }
            #sidebar QLabel#muted { color: palette(mid); }
            #sidebar QPushButton#kofi {
                background: #ff5e5b; color: white; border: none; border-radius: 6px;
                padding: 7px 10px; font-weight: 600; text-align: left; }
            #sidebar QPushButton#kofi:hover { background: #ff7573; }
            #sidebar QPushButton#kofi:pressed { background: #e04f4c; }
        """)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(40, 40))
        head.addWidget(logo)
        title = QLabel(f"<b>Cursor Manager</b><br><span style='color: palette(mid)'>v{__version__}</span>")
        head.addWidget(title, 1)
        lay.addLayout(head)

        sec = QLabel("GET MORE CURSORS")
        sec.setObjectName("muted")
        sec.setStyleSheet("letter-spacing: 1px; font-size: 8pt; font-weight: 600;")
        lay.addWidget(sec)
        for name, url, blurb in config.CURSOR_SOURCES:
            card = QFrame()
            card.setObjectName("card")
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 10, 10, 10)
            cl.setSpacing(6)
            cl.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)   # grow with wrapped text
            t = QLabel(name)
            t.setObjectName("cardTitle")
            t.setWordWrap(True)
            d = QLabel(blurb)
            d.setObjectName("muted")
            d.setWordWrap(True)
            d.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
            b = QPushButton("☕  Open on Ko-fi")
            b.setObjectName("kofi")
            b.setToolTip(url)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            cl.addWidget(t)
            cl.addWidget(d)
            cl.addWidget(b)
            lay.addWidget(card)

        how = QLabel("Download a pack's zip, drop it into your cursors folder and press Rescan. "
                     "No unzipping needed.")
        how.setObjectName("muted")
        how.setWordWrap(True)
        lay.addWidget(how)
        lay.addStretch(1)
        drop = QPushButton(QIcon.fromTheme("folder-open"), "Open cursors folder")
        drop.clicked.connect(self.open_folder)
        lay.addWidget(drop)
        foot = QLabel(f'<a href="{config.PROJECT_URL}">Source on GitHub</a><br>'
                      f'by {config.AUTHOR} · MIT license')
        foot.setObjectName("muted")
        foot.setOpenExternalLinks(True)
        foot.setToolTip(config.PROJECT_URL)
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(foot)
        return side

    def open_folder(self):
        folder = Path(self.cfg["images_dir"]).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Cursors folder", f"Cannot create {folder}:\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ---- system tray ------------------------------------------------------------
    def _build_tray(self):
        self.tray: Optional[QSystemTrayIcon] = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("Cursor Manager")
        menu = QMenu()
        self.tray_show = QAction("Show Cursor Manager", menu)
        self.tray_show.triggered.connect(self.show_window)
        self.tray_toggle = QAction("Start rotation", menu)
        self.tray_toggle.triggered.connect(self.toggle_rotation)
        nxt = QAction("Next cursor", menu)
        nxt.triggered.connect(self.next_cursor)
        rest = QAction("Restore original cursor", menu)
        rest.triggered.connect(self.restore_original)
        quit_ = QAction("Quit", menu)
        quit_.triggered.connect(self.quit_app)
        menu.addAction(self.tray_show)
        menu.addSeparator()
        menu.addAction(self.tray_toggle)
        menu.addAction(nxt)
        menu.addAction(rest)
        menu.addSeparator()
        links = menu.addMenu("Get more cursors")
        for label, url, _blurb in config.CURSOR_SOURCES:
            act = QAction(f"{label} on Ko-fi", links)
            act.triggered.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            links.addAction(act)
        menu.addSeparator()
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_window()

    def _update_tray(self, cur_theme: str, running: bool, label: str):
        """Keep the tray, window and taskbar icons in step with the applied cursor."""
        if self.tray is not None:
            self.tray_toggle.setText("Stop rotation" if running else "Start rotation")
            self.tray.setToolTip(f"Cursor Manager\n{label}")
        if cur_theme == self._tray_theme:
            return
        self._tray_theme = cur_theme
        self._update_now_tile(cur_theme)
        icon = self._icon_for_theme(cur_theme)
        if self.tray is not None:
            self.tray.setIcon(icon)
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)

    def jump_to_current(self):
        """Select the set that is applied to the desktop right now."""
        cur_theme = theme.current_theme()
        cs = next((s for s in self.sets if s.theme_name == cur_theme), None)
        if cs is None:
            self.statusBar().showMessage("The current cursor is not one of the sets in the list", 4000)
            return
        if self.search.text():
            self.search.clear()          # a filter could be hiding it
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == cs.id:
                item.setHidden(False)
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
                self.list.setFocus()
                break

    def _update_now_tile(self, cur_theme: str):
        cs = next((s for s in self.sets if s.theme_name == cur_theme), None)
        if cs is None or "default" not in cs.roles:
            self.now_tile.set_anim(None)
            self.now_tile.img.setPixmap(app_icon().pixmap(96, 96))
            self.now_tile.text.setText("Current cursor\n" + (cur_theme or "none"))
            return
        try:
            self.now_tile.set_anim(CursorAnim(self._cursor(cs, "default"), 128, self.devicePixelRatioF()))
        except Exception:  # noqa: BLE001
            self.now_tile.set_anim(None)
        self.now_tile.text.setText(f"Current cursor\n{cs.name}")

    def _icon_for_theme(self, cur_theme: str) -> QIcon:
        """Icon built from the active set's arrow, or the app icon when none is applied."""
        cs = next((s for s in self.sets if s.theme_name == cur_theme), None)
        if cs is None or "default" not in cs.roles:
            return app_icon()
        try:
            fr = self._cursor(cs, "default").frames[0]
        except Exception:  # noqa: BLE001
            return app_icon()
        icon = QIcon()
        for size in (32, 64, 128, 256):
            f = scale_frame(fr, size)
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            src = frame_to_pixmap(f)
            p = QPainter(pm)
            p.drawPixmap((size - src.width()) // 2, (size - src.height()) // 2, src)
            p.end()
            icon.addPixmap(pm)
        return icon

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._quitting = True
        if self.tray is not None:
            self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.tray is not None and not self._quitting:
            event.ignore()
            self.hide()
            st = config.load_state()
            if not st.get("tray_hint_shown"):
                self.tray.showMessage("Cursor Manager is still running",
                                      "Find it in the system tray. Use Quit from the tray menu to close it.",
                                      app_icon(), 5000)
                config.save_state(tray_hint_shown=True)
            return
        super().closeEvent(event)

    def hideEvent(self, event):
        # No need to animate previews while hidden in the tray.
        if hasattr(self, "clock"):
            self.clock.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if hasattr(self, "clock"):
            self.clock.start()
        super().showEvent(event)

    # ---- data -----------------------------------------------------------------
    def rescan(self, notify_daemon: bool = True):
        self.cfg = config.load_config()
        self.folder_label.setText(self.cfg["images_dir"])
        self.sets = sets_mod.scan(self.cfg["images_dir"])
        self._cursor_cache.clear()
        disabled = set(self.cfg.get("disabled_sets", []))
        selected = self.current.id if self.current else config.load_state().get("current_set")
        self._loading_ui = True
        self.list.clear()
        for cs in self.sets:
            item = QListWidgetItem(cs.name + ("  (built-in)" if cs.bundled else ""))
            item.setData(Qt.ItemDataRole.UserRole, cs.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if cs.id in disabled else Qt.CheckState.Checked)
            tip = f"{cs.source}\n{cs.kind}, {len(cs.roles)} icon(s)"
            if cs.bundled:
                tip += "\nBuilt in: lives in assets/bundled of this install"
            item.setToolTip(tip)
            try:
                cur = self._cursor(cs, "default")
                item.setIcon(QIcon(frame_to_pixmap(scale_frame(cur.frames[0], 32))))
            except Exception:  # noqa: BLE001
                pass
            self.list.addItem(item)
            if cs.id == selected:
                self.list.setCurrentItem(item)
        self._loading_ui = False
        self._filter_list(self.search.text())
        self._tray_theme = None      # force the tray / window icon / current preview to refresh
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        if not self.sets:
            self.current = None
            self.title.setText("<h2>No cursor sets found</h2>")
            self.info.setText(f"Put cursor files or cursor-pack zips into {self.cfg['images_dir']} and click Rescan.")
            for t in self.tiles.values():
                t.set_anim(None)
            self._refresh_test_area()
        self.statusBar().showMessage(f"Found {len(self.sets)} cursor set(s)", 3000)
        if notify_daemon:
            daemon.reload()   # tell the rotation daemon to pick up the changes right away

    def _filter_list(self, text: str = ""):
        """Hide sets whose name or source file doesn't contain every word typed."""
        words = text.lower().split()
        shown = 0
        first_visible = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            hay = (item.text() + " " + item.toolTip()).lower()
            visible = all(w in hay for w in words)
            item.setHidden(not visible)
            if visible:
                shown += 1
                if first_visible is None:
                    first_visible = item
        cur = self.list.currentItem()
        if words and (cur is None or cur.isHidden()) and first_visible is not None:
            self.list.setCurrentItem(first_visible)
        if words:
            self.statusBar().showMessage(f"{shown} of {self.list.count()} sets match", 2000)

    def choose_folder(self) -> bool:
        start = self.cfg["images_dir"] if Path(self.cfg["images_dir"]).is_dir() else str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose your cursors folder", start)
        if not path:
            return False
        self.cfg["images_dir"] = path
        self.cfg["setup_done"] = True
        self._save()
        self.current = None
        self.rescan()
        self.statusBar().showMessage(f"Cursor folder set to {path}", 6000)
        return True

    def first_run_prompt(self):
        """Remind a new user to pick the folder their cursors live in."""
        if self.cfg.get("setup_done"):
            return
        default = Path(config.DEFAULT_CONFIG["images_dir"])
        box = QMessageBox(self)
        box.setWindowTitle("Choose your cursors folder")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Where are your cursors?")
        box.setInformativeText(
            "Cursor Manager reads cursor packs (.zip, .ani, .cur, .png, .gif) from a folder you choose. "
            "No packs are included; the sidebar links to artists who make them.\n\n"
            f"Pick a folder now, or use the default one:\n{default}")
        choose = box.addButton("Choose a folder…", QMessageBox.ButtonRole.AcceptRole)
        use_default = box.addButton("Use the default folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Ask me later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(choose)
        box.exec()
        clicked = box.clickedButton()
        if clicked is choose:
            if not self.choose_folder():
                self.statusBar().showMessage("No folder chosen yet - use 'Change folder…' when you are ready", 8000)
        elif clicked is use_default:
            try:
                default.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            self.cfg["images_dir"] = str(default)
            self.cfg["setup_done"] = True
            self._save()
            self.rescan()
            self.statusBar().showMessage(f"Using {default} - drop cursor zips there and press Rescan", 8000)

    def _hotspot(self, cs: CursorSet) -> Tuple[int, int]:
        hs = self.cfg.get("hotspots", {}).get(cs.id, [0, 0])
        return int(hs[0]), int(hs[1])

    def _cursor(self, cs: CursorSet, role: str) -> Cursor:
        hs = self._hotspot(cs)
        key = (cs.id, role, hs[0], hs[1])
        if key not in self._cursor_cache:
            self._cursor_cache[key] = load_cursor(str(cs.roles[role]), hs)
        return self._cursor_cache[key]

    def _selected_set(self) -> Optional[CursorSet]:
        item = self.list.currentItem()
        if item is None:
            return None
        sid = item.data(Qt.ItemDataRole.UserRole)
        return next((s for s in self.sets if s.id == sid), None)

    # ---- selection / previews -----------------------------------------------
    def _on_select(self, *_):
        cs = self._selected_set()
        self.current = cs
        if cs is None:
            return
        self._loading_ui = True
        try:
            hs = self._hotspot(cs)
            self.hot_x.setValue(hs[0])
            self.hot_y.setValue(hs[1])
        finally:
            self._loading_ui = False
        self.hotspot_widget.setVisible(cs.is_image)
        self._show_set(cs)

    def _show_set(self, cs: CursorSet):
        self.title.setText(f"<h2>{cs.name}</h2>")
        dpr = self.devicePixelRatioF()
        sizes = set()
        animated = False
        for role, tile in self.tiles.items():
            if role in cs.roles:
                try:
                    cur = self._cursor(cs, role)
                except Exception as exc:  # noqa: BLE001
                    tile.set_anim(None)
                    tile.text.setText(f"{ROLE_LABELS[role]}\n(unreadable)")
                    print(f"cannot read {cs.roles[role]}: {exc}")
                    continue
                sizes.add("x".join(map(str, cur.size)))
                animated = animated or cur.animated
                tile.set_anim(CursorAnim(cur, 80, dpr, mark_hotspot=cs.is_image))
                tile.text.setText(ROLE_LABELS[role] + (" (unused)" if role in ("person", "pin") else ""))
                tile.setVisible(True)
            else:
                tile.set_anim(None)
                tile.setVisible(False)
        kind = "cursor pack" if cs.kind == "pack" else "single cursor"
        missing = [ROLE_LABELS[r] for r in TEST_ROLES if r not in cs.roles]
        note = f" - missing icons ({', '.join(missing)}) fall back to {theme.original_theme()}" if missing else ""
        self.info.setText(f"{kind}, {len(cs.roles)} icon(s), {'/'.join(sorted(sizes)) or '?'} px, "
                          f"{'animated' if animated else 'static'}{note}\n{cs.source}")
        self._refresh_test_area()

    def _refresh_test_area(self):
        cs = self.current
        if self.mode_sys.isChecked() or cs is None:
            for role, zone in self.zones.items():
                zone.set_shape(QT_SHAPES.get(role))
                zone.caption.setText(ROLE_LABELS[role] + ("" if QT_SHAPES.get(role) else " (no Qt shape)"))
            return
        dpr = self.devicePixelRatioF()
        px = int(self.cfg["cursor_size"])
        for role, zone in self.zones.items():
            zone.caption.setText(ROLE_LABELS[role])
            if role in cs.roles:
                try:
                    zone.set_anim(CursorAnim(self._cursor(cs, role), px, dpr))
                    continue
                except Exception:  # noqa: BLE001
                    pass
            zone.set_shape(QT_SHAPES.get(role))
            zone.caption.setText(ROLE_LABELS[role] + " (not in set)")

    def _tick(self):
        t = (time.monotonic() - self.t0) * 1000.0
        for tile in self.tiles.values():
            if tile.isVisible():
                tile.tick(t)
        self.now_tile.tick(t)
        for zone in self.zones.values():
            zone.tick(t)

    # ---- settings ---------------------------------------------------------------
    def _save(self):
        config.save_config(self.cfg)

    def _on_item_changed(self, item: QListWidgetItem):
        if self._loading_ui:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        disabled = set(self.cfg.get("disabled_sets", []))
        if item.checkState() == Qt.CheckState.Checked:
            disabled.discard(sid)
        else:
            disabled.add(sid)
        self.cfg["disabled_sets"] = sorted(disabled)
        self._save()

    def _load_interval(self):
        secs = int(self.cfg["interval_seconds"])
        unit_idx = 0
        for i, (_, mult) in enumerate(UNITS):
            if secs % mult == 0 and secs // mult <= 999:
                unit_idx = i
        was_loading = self._loading_ui
        self._loading_ui = True
        self.interval_unit.setCurrentIndex(unit_idx)
        self.interval_spin.setValue(max(1, secs // UNITS[unit_idx][1]))
        self._loading_ui = was_loading

    def _on_interval_changed(self, *_):
        if self._loading_ui:
            return
        secs = self.interval_spin.value() * UNITS[self.interval_unit.currentIndex()][1]
        self.cfg["interval_seconds"] = max(5, secs)
        self._save()

    def _on_order_changed(self, idx):
        if self._loading_ui:
            return
        self.cfg["shuffle"] = idx == 1
        self._save()

    def _on_size_changed(self, val):
        if self._loading_ui:
            return
        self.cfg["cursor_size"] = int(val)
        self._save()
        self._refresh_test_area()
        self._size_timer.start()

    def _push_active_set(self):
        """Re-apply the active set so a size/hotspot change shows up on the desktop."""
        cur_theme = theme.current_theme()
        cs = next((s for s in self.sets if s.theme_name == cur_theme), None)
        if cs is None:
            return
        try:
            theme.apply_set(cs, self.cfg)
            self.statusBar().showMessage(f"Cursor size {self.cfg['cursor_size']} px applied to '{cs.name}'", 5000)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"Could not re-apply: {exc}", 8000)
        self._refresh_status()

    def _on_hotspot_changed(self, *_):
        if self._loading_ui or self.current is None:
            return
        self.cfg.setdefault("hotspots", {})[self.current.id] = [self.hot_x.value(), self.hot_y.value()]
        self._save()
        self._cursor_cache = {k: v for k, v in self._cursor_cache.items() if k[0] != self.current.id}
        self._show_set(self.current)
        self._size_timer.start()

    def reset_defaults(self):
        """Restore the rotation settings shipped with the app."""
        d = config.DEFAULT_CONFIG
        self.cfg["interval_seconds"] = d["interval_seconds"]
        self.cfg["shuffle"] = d["shuffle"]
        self.cfg["cursor_size"] = d["cursor_size"]
        self._save()
        self._loading_ui = True
        try:
            self._load_interval()
            self.order_combo.setCurrentIndex(0)
            self.size_spin.setValue(int(d["cursor_size"]))
        finally:
            self._loading_ui = False
        self._refresh_test_area()
        self._size_timer.start()
        mins = d["interval_seconds"] // 60
        self.statusBar().showMessage(
            f"Rotation reset: every {mins} minutes, in order, {d['cursor_size']} px", 6000)

    def _on_autostart(self, on: bool):
        try:
            startup.set_autostart(on)
            self.statusBar().showMessage(
                f"Rotation will {'start' if on else 'not start'} at login ({config.AUTOSTART_FILE})", 6000)
        except OSError as exc:
            QMessageBox.warning(self, "Autostart", f"Could not write the autostart entry:\n{exc}")

    # ---- actions ----------------------------------------------------------------
    def apply_selected(self):
        cs = self._selected_set()
        if cs is None:
            return
        try:
            msg = theme.apply_set(cs, self.cfg)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Apply failed", str(exc))
            return
        self.statusBar().showMessage(msg, 8000)
        self._refresh_status()

    def toggle_rotation(self):
        if daemon.is_running():
            daemon.stop()
        else:
            if not sets_mod.enabled_sets(self.sets, self.cfg):
                QMessageBox.information(self, "Nothing to rotate", "No cursor sets are checked.")
                return
            subprocess.Popen([sys.executable, str(LAUNCHER), "daemon"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        QTimer.singleShot(600, self._refresh_status)

    def next_cursor(self):
        if daemon.skip():
            self.statusBar().showMessage("Switching to the next cursor", 3000)
            QTimer.singleShot(800, self._refresh_status)
            return
        order = sets_mod.enabled_sets(self.sets, self.cfg)
        nxt = daemon._pick_next(order, config.load_state().get("current_set"), self.cfg.get("shuffle", False))
        if nxt is None:
            QMessageBox.information(self, "Nothing to rotate", "No cursor sets are checked.")
            return
        self.statusBar().showMessage(theme.apply_set(nxt, self.cfg), 8000)
        self._refresh_status()

    def restore_original(self):
        self.statusBar().showMessage(theme.restore_original(), 8000)
        self._refresh_status()

    def _refresh_status(self):
        running = daemon.is_running()
        self.start_btn.setText("Stop rotation" if running else "Start rotation")
        st = config.load_state()
        cur_theme = theme.current_theme()
        cur_name = next((s.name for s in self.sets if s.theme_name == cur_theme), None)
        parts = []
        if running:
            parts.append("Rotation running")
            nc = st.get("next_change")
            if nc:
                rem = max(0, int(nc - time.time()))
                parts.append(f"next change in {rem // 60:02d}:{rem % 60:02d}")
        else:
            parts.append("Rotation stopped")
        parts.append(f"current cursor: {cur_name or cur_theme}")
        self.status_label.setText("  ·  ".join(parts))
        self._update_tray(cur_theme, running, "  ·  ".join(parts))


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("Cursor Manager")
    app.setDesktopFileName(config.APP_ID)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    # Single instance: if the app is already running (maybe hidden in the tray), just show it.
    server = None
    if "--screenshot" not in argv:
        probe = QLocalSocket()
        probe.connectToServer(SINGLE_INSTANCE_KEY)
        if probe.waitForConnected(300):
            probe.write(b"show")
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            return 0
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        server = QLocalServer()
        server.listen(SINGLE_INSTANCE_KEY)

    win = MainWindow()
    win.setWindowIcon(app_icon())

    def on_connection():
        sock = server.nextPendingConnection()
        if sock is not None:
            sock.readyRead.connect(win.show_window)
            sock.disconnected.connect(sock.deleteLater)
    if server is not None:
        server.newConnection.connect(on_connection)

    if "--hidden" in argv and win.tray is not None:
        pass  # start in the tray only
    else:
        win.show()
        QTimer.singleShot(0, win.first_run_prompt)
    if "--screenshot" in argv:  # used for automated checks
        out = argv[argv.index("--screenshot") + 1]

        def shot():
            target = QApplication.activeModalWidget() or win
            target.grab().save(out)
            win.quit_app()
        QTimer.singleShot(1500, shot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
