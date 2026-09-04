#!/usr/bin/env python3
import os
import platform
import sys
from pathlib import Path

# CRITICAL: Enable Wayland support for Qt5 BEFORE importing PyQt5
if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
    os.environ["QT_QPA_PLATFORM"] = "wayland"


def is_wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_WAYLAND = IS_LINUX and is_wayland_session()

if IS_WINDOWS:
    import ctypes
    import winreg

    import extraction
else:
    import subprocess

    extraction = None

try:
    from window_resolver import get_active_app, get_active_app_wayland
except Exception:
    print("falling back on xdotool")
    get_active_app = None
    get_active_app_wayland = None

try:
    import wayland_bridge
except Exception:
    wayland_bridge = None

import datetime
import logging
from collections import defaultdict

import psutil
import qdarkstyle
from PyQt5 import QtCore, QtGui, QtWidgets

# matplotlib is not imported on purpose here. It is only needed for the statistics page.
import argparse

import map_resolve
from data_manager import DataManager
from single_instance import InstanceAlreadyRunningError, SingleInstance, get_data_dir

print(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE')}")
print(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY')}")
print(f"DISPLAY: {os.environ.get('DISPLAY')}")
print()

parser = argparse.ArgumentParser(
    prog=os.path.basename(sys.argv[0]),
    description="Screen Time application",
)
parser.add_argument("--hidden", action="store_true", help="Start hidden (no UI)")
args, remaining_argv = parser.parse_known_args()

if "-h" in sys.argv or "--help" in sys.argv:
    parser.print_help()
    sys.exit(0)

# ── Single-Instance-Guard ──────────────────────────────────────────────────────
try:
    _instance_lock = SingleInstance()
except InstanceAlreadyRunningError:
    print("Screentime is already running. Exiting this instance...")
    sys.exit(0)
# ──────────────────────────────────────────────────────────────────────────────

DATA_DIR = get_data_dir()

# Point to DATA_DIR
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAPPING_PATH = os.path.join(DATA_DIR, "map.json")

# copy map.json (for Appimages)
if not os.path.exists(MAPPING_PATH):
    src = os.path.join(BASE_DIR, "map.json")
    if os.path.exists(src):
        import shutil

        shutil.copy2(src, MAPPING_PATH)

app_mapping = map_resolve.AppMapping(MAPPING_PATH)

########################################################################
# Logging setup
########################################################################

log_file = os.path.join(DATA_DIR, "log.txt")
logging.basicConfig(
    # level=logging.DEBUG,        # enable debug mode
    level=logging.ERROR,  # normal mode
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("Starting...")


def get_executable_path():
    return (
        sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    )


def add_to_autostart():
    try:
        if IS_WINDOWS:
            exe_path = get_executable_path()
            start_with_ui = QtCore.QSettings("true_lock", "Screen Time").value(
                "start_with_ui", True, type=bool
            )
            cmd = f'"{exe_path}"'
            if not start_with_ui:
                cmd += " --hidden"
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, "ScreenTimeApp", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            logger.info("Autostart (Windows) added.")
        elif IS_LINUX:
            exe_path = get_executable_path()
            start_with_ui = QtCore.QSettings("true_lock", "Screen Time").value(
                "start_with_ui", True, type=bool
            )
            autostart_dir = Path.home() / ".config" / "autostart"
            autostart_dir.mkdir(parents=True, exist_ok=True)
            desktop_file = autostart_dir / "screentime.desktop"
            content = f"""[Desktop Entry]
Type=Application
Name=ScreenTimeApp
Exec="{exe_path}"{" --hidden" if not start_with_ui else ""}
X-GNOME-Autostart-enabled=true
"""
            # Only write if the content actually changed
            try:
                existing = desktop_file.read_text(encoding="utf-8")
            except FileNotFoundError:
                existing = None
            if existing != content:
                desktop_file.write_text(content, encoding="utf-8")
                logger.info("Autostart (Linux .desktop) added: %s", desktop_file)
        else:
            logger.warning("Autostart is not yet supported on this platform.")
    except Exception:
        logger.exception("Error while trying to create an autostart entry:")


def remove_from_autostart():
    try:
        if IS_WINDOWS:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.DeleteValue(key, "ScreenTimeApp")
            winreg.CloseKey(key)
            logger.info("Autostart (Windows) removed.")
        elif IS_LINUX:
            desktop_file = Path.home() / ".config" / "autostart" / "screentime.desktop"
            if desktop_file.exists():
                desktop_file.unlink()
                logger.info("Autostart (Linux) removed: %s", desktop_file)
        else:
            logger.warning("Autostart is not supported on this platform.")
    except Exception:
        logger.exception("Error while removing the autostart entry:")


########################################################################
# Determine the currently active process
########################################################################


def get_active_window_process_name():
    """Returns a (name, pid) tuple. pid is a string or None if unknown."""
    if IS_WINDOWS:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd == 0:
                return "", None
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = psutil.Process(pid.value)
            return process.name(), str(pid.value)
        except Exception:
            logger.exception("Error while determining the active window (Windows):")
            return "", None
    elif IS_LINUX:
        try:
            if get_active_app is not None:
                try:
                    info = get_active_app(mapping_path=MAPPING_PATH)
                except Exception:
                    info = {}
                wm_pid = info.get("wm_pid")
                name = info.get("app_name") or info.get("app_id") or None
                if name:
                    return name, wm_pid
                proc_path = info.get("proc_path")
                if proc_path:
                    try:
                        return Path(proc_path).name, wm_pid
                    except Exception:
                        pass
                if wm_pid:
                    try:
                        return psutil.Process(int(wm_pid)).name(), wm_pid
                    except Exception:
                        pass
            try:
                out = subprocess.check_output(
                    ["xdotool", "getwindowfocus", "getwindowpid"],
                    stderr=subprocess.DEVNULL,
                )
                pid = int(out.strip())
                return psutil.Process(pid).name(), str(pid)
            except FileNotFoundError:
                logger.warning(
                    "xdotool not found; could not determine the active window on Linux."
                )
                return "", None
            except Exception:
                logger.exception("Error while determining the active window (Linux):")
                return "", None
        except Exception:
            logger.exception("Error in get_active_window_process_name (Linux):")
            return "", None
    else:
        return "", None


def get_active_window_process_name_wayland():
    """
    Like get_active_window_process_name(), but sourced from the kwin-script.
    If there is no data from the script, it falls back to the generic "Wayland PC".

    Returns a (name, pid) tuple. Values for name:
      - None:  Never received an event from the script (probably not installed/active) -> fall back to generic "Wayland PC" entry
      - "":    Script is active, but explicitly reports NO
               active window (e.g. last window closed) ->
               treat like screen-lock: time continues, but does not get added to any entry.
      - Name:  Normal, active app.
    pid is a string or None if unknown.
    """

    if wayland_bridge is None or get_active_app_wayland is None:
        return None, None
    receiver = wayland_bridge.get_receiver()
    if receiver is None or not receiver.has_data:
        return None, None
    if not receiver.has_active_window:
        return "", None
    try:
        info = get_active_app_wayland(MAPPING_PATH, receiver.snapshot())
    except Exception:
        logger.exception("Error in get_active_window_process_name_wayland:")
        return "", None
    wm_pid = info.get("wm_pid")
    name = info.get("app_name") or info.get("app_id")
    if name:
        return name, wm_pid
    if wm_pid:
        try:
            return psutil.Process(int(wm_pid)).name(), wm_pid
        except Exception:
            pass
    return "", None


# Don't count time on the lock screen.
# Cache the gdbus result for 5 seconds to avoid a subprocess call every tick.
_lock_cache: dict = {"result": False, "ts": 0.0}
_LOCK_CACHE_TTL = 5.0


def is_screen_locked_linux():
    import time

    now = time.monotonic()
    if now - _lock_cache["ts"] < _LOCK_CACHE_TTL:
        return _lock_cache["result"]

    # Different DEs implement different ScreenSaver D-Bus interfaces:
    # ScreenSaver D-Bus interfaces:
    #   - org.freedesktop.ScreenSaver -> KDE (kscreenlocker) + standard
    #   - org.gnome.ScreenSaver       -> GNOME, XFCE (xfce4-screensaver)

    candidates = [
        ("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.GetActive"),
        ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.GetActive"),
    ]

    result = False
    for dest, obj_path, method in candidates:
        try:
            out = subprocess.check_output(
                ["gdbus", "call", "--session", "--dest", dest,
                 "--object-path", obj_path, "--method", method],
                stderr=subprocess.DEVNULL,
            )
            result = "true" in out.decode().lower()
            break  # Answer received -> do not continue trying
        except Exception:
            continue  # this interface is not reachable, try the next one

    _lock_cache["result"] = result
    _lock_cache["ts"] = now
    return result


########################################################################
# Settings
########################################################################


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(400, 200)
        layout = QtWidgets.QVBoxLayout(self)
        self.chk_autostart = QtWidgets.QCheckBox("Open on Startup")
        self.chk_autostart.setChecked(True)  # active by default
        layout.addWidget(self.chk_autostart)
        self.chk_start_with_ui = QtWidgets.QCheckBox("Start with UI when autostarting")
        layout.addWidget(self.chk_start_with_ui)
        start_with_ui = self.parent().qsettings.value("start_with_ui", True, type=bool)
        self.chk_start_with_ui.setChecked(start_with_ui)
        self.chk_start_with_ui.setEnabled(self.chk_autostart.isChecked())
        self.chk_autostart.toggled.connect(self.chk_start_with_ui.setEnabled)
        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_settings(self):
        return {
            "autostart": self.chk_autostart.isChecked(),
            "start_with_ui": self.chk_start_with_ui.isChecked(),
        }


########################################################################
# Customize App Dialog
########################################################################


class CustomizeAppDialog(QtWidgets.QDialog):
    def __init__(self, raw_key: str, current_display: str, mapping, parent=None):
        super().__init__(parent)
        self.raw_key = raw_key
        self.mapping = mapping
        self._original_display = current_display

        self.setWindowTitle(f"Customize, {current_display}")
        self.resize(480, 160)
        layout = QtWidgets.QFormLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        self.name_edit = QtWidgets.QLineEdit()
        entry = mapping.mapping.get(raw_key, {})
        self.name_edit.setText(entry.get("display_name", current_display))
        layout.addRow("Display name:", self.name_edit)
        # Icon path
        icon_row = QtWidgets.QHBoxLayout()
        self.icon_edit = QtWidgets.QLineEdit()
        self.icon_edit.setPlaceholderText("Path to .png / .svg  (leave empty for auto)")
        self.icon_edit.setText(entry.get("icon", ""))
        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_icon)
        icon_row.addWidget(self.icon_edit)
        icon_row.addWidget(browse_btn)
        layout.addRow("Icon:", icon_row)

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._save)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _browse_icon(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select icon",
            str(Path.home()),
            "Images (*.png *.svg *.xpm *.ico *.jpg);;All files (*)",
        )
        if path:
            self.icon_edit.setText(path)

    def _save(self):
        display = self.name_edit.text().strip()
        icon = self.icon_edit.text().strip()
        if not display:
            QtWidgets.QMessageBox.warning(
                self, "Error", "Display name cannot be empty."
            )
            return
        existing = self.mapping.mapping.get(self.raw_key, {})
        # Only write if something actually changed
        if display == existing.get(
            "display_name", self._original_display
        ) and icon == existing.get("icon", ""):
            self.accept()
            return
        entry = {}
        if display:
            entry["display_name"] = display
        if icon:
            entry["icon"] = icon
        self.mapping.save_entry(self.raw_key, entry)
        self.accept()


########################################################################
# MainWindow
########################################################################


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        DataManager.initialize_database()

        self.setWindowTitle("Screen Time")
        self.resize(900, 600)
        self.setFont(QtGui.QFont("Segoe UI", 12))

        self.usage_today = defaultdict(float)
        self.current_process = ""
        self.current_pid = None
        # Remembers the last known pid a given app name ran with, so
        # update_table() can still resolve an icon (e.g. via icoextract)
        # after the window is no longer the active one.
        self.last_pid_for_app = {}
        self.last_switch_time = datetime.datetime.now()

        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QtWidgets.QVBoxLayout(central_widget)

        self.stack = QtWidgets.QStackedWidget()
        root_layout.addWidget(self.stack)
        self.today_page = QtWidgets.QWidget()
        self.stack.addWidget(self.today_page)

        main_layout = QtWidgets.QVBoxLayout(self.today_page)
        # Statistics page is created lazily
        self._statistics_page = None
        self.stack.currentChanged.connect(self.on_stack_changed)

        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.header = QtWidgets.QLabel("Screentime today")
        self.header.setFont(QtGui.QFont("Segoe UI", 16))
        main_layout.addWidget(self.header)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "App", "Time used", "Ratio"])
        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setStyleSheet("font-size: 14pt;")
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.on_table_context_menu)
        main_layout.addWidget(self.table)

        self.settings_button = QtWidgets.QToolButton(self)
        self.settings_button.setText("⚙")
        self.settings_button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.clicked.connect(self.open_settings)
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addStretch()
        top_layout.addWidget(self.settings_button)
        main_layout.insertLayout(0, top_layout)

        button_layout = QtWidgets.QHBoxLayout()
        self.btn_statistics = QtWidgets.QPushButton("Statistics")
        self.btn_statistics.setFont(QtGui.QFont("Segoe UI", 14))
        self.btn_statistics.clicked.connect(self.show_statistics)
        button_layout.addWidget(self.btn_statistics)
        self.btn_exit = QtWidgets.QPushButton("Quit")
        for btn in (self.btn_statistics, self.btn_exit):
            btn.setFont(QtGui.QFont("Segoe UI", 14))
            button_layout.addWidget(btn)
        main_layout.addLayout(button_layout)

        self.from_date = QtWidgets.QDateEdit(calendarPopup=True)
        self.to_date = QtWidgets.QDateEdit(calendarPopup=True)
        self.from_date.setVisible(False)
        self.to_date.setVisible(False)

        self.btn_exit.clicked.connect(self.exit_app)

        self.qsettings = QtCore.QSettings("true_lock", "Screen Time")
        autostart_enabled = self.qsettings.value("autostart", True, type=bool)
        if autostart_enabled:
            add_to_autostart()
        else:
            remove_from_autostart()

        self.setup_tray_icon()

        if IS_WAYLAND and wayland_bridge is not None:
            wayland_bridge.start()
            # small delay and check again
            QtCore.QTimer.singleShot(3000, self._maybe_show_wayland_warning)
        elif IS_WAYLAND:
            self.show_wayland_warning_once()

        self.timer = QtCore.QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_tracking)
        self.timer.start()
        self._last_display_usage = {}

        # Separate, low-frequency timer that flushes buffered usage data to
        # disk.
        self.db_flush_timer = QtCore.QTimer()
        self.db_flush_timer.setInterval(40_000)  # flush at most once every 40 seconds
        self.db_flush_timer.start()

        self.load_usage_from_db()

        total_seconds = sum(self.usage_today.values())
        formatted_total = str(datetime.timedelta(seconds=int(total_seconds)))
        self.header.setText(f"Todays App Usage (Total: {formatted_total})")

    def _maybe_show_wayland_warning(self):
        receiver = wayland_bridge.get_receiver() if wayland_bridge else None
        if receiver is not None and receiver.has_data:
            return  # KWin script seems to work, all good
        self.show_wayland_warning_once()

    def show_wayland_warning_once(self):
        shown = self.qsettings.value("wayland_warning_shown", False, type=bool)
        if shown:
            return
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.setWindowTitle("Wayland Limitation")
        msg.setText(
            "Per-application screen time tracking on Wayland requires the "
            "'screentime-kwin' KWin script (KDE Plasma only). It doesn't seem "
            "to be installed/active.\n\n"
            "Only total PC usage time will be tracked and stored as "
            '"Wayland PC" until it is installed. See README.md for setup.'
        )
        msg.setInformativeText(
            "This is a technical limitation of Wayland.\n\n"
            "See the GitHub issue for details."
        )
        msg.setStandardButtons(QtWidgets.QMessageBox.Ok)
        github_button = msg.addButton(
            "View GitHub Issue", QtWidgets.QMessageBox.ActionRole
        )
        msg.exec_()
        if msg.clickedButton() == github_button:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl("https://github.com/truelockmc/Screentime/issues/7")
            )
        self.qsettings.setValue("wayland_warning_shown", True)

    def update_wayland_tracking(self):
        now = datetime.datetime.now()
        if IS_LINUX and is_screen_locked_linux():
            self.current_process = ""
            self.last_switch_time = now
            return

        raw_active_app, active_pid = get_active_window_process_name_wayland()

        if raw_active_app is None:
            # No screentime kwin script installed (yet)
            duration = (now - self.last_switch_time).total_seconds()
            if duration > 0:
                self.usage_today["Wayland PC"] += duration
                DataManager.add_daily_usage("Wayland PC", duration)
            self.current_process = ""
            self.current_pid = None
            self.last_switch_time = now
            self.update_total_usage()
            self.update_table(live_update=False)
            return

        # From here on -> real per-application time tracking just like on x11.
        if raw_active_app == self.current_process and self.current_process:
            self.update_total_usage()
            self.update_table(live_update=True)
        else:
            duration = (now - self.last_switch_time).total_seconds()
            if self.current_process and duration > 0:
                self.usage_today[self.current_process] += duration
                DataManager.add_daily_usage(self.current_process, duration)
            self.current_process = raw_active_app
            self.current_pid = active_pid
            if raw_active_app and active_pid:
                self.last_pid_for_app[raw_active_app] = active_pid
            self.last_switch_time = now
            self.update_total_usage()
            self.update_table(live_update=False)

    def open_settings(self):
        dlg = SettingsDialog(self)
        autostart_enabled = self.qsettings.value("autostart", True, type=bool)
        dlg.chk_autostart.setChecked(autostart_enabled)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            settings = dlg.get_settings()
            self.qsettings.setValue("autostart", settings["autostart"])
            self.qsettings.setValue("start_with_ui", settings["start_with_ui"])
            if settings["autostart"]:
                add_to_autostart()
            else:
                remove_from_autostart()

    def on_stack_changed(self, index: int):
        try:
            widget = self.stack.widget(index)
            if self._statistics_page is not None and widget is self._statistics_page:
                self._statistics_page.reload()
        except Exception:
            pass

    def _get_statistics_page(self):
        if self._statistics_page is None:
            from statistics import StatisticsPage

            self._statistics_page = StatisticsPage(
                self.stack, icon_manager, app_mapping
            )
            self.stack.addWidget(self._statistics_page)
        return self._statistics_page

    def show_statistics(self):
        self.stack.setCurrentWidget(self._get_statistics_page())

    def load_usage_from_db(self):
        today = datetime.date.today().isoformat()
        conn = DataManager._get_conn()
        c = conn.cursor()
        c.execute(
            "SELECT app_name, duration_seconds FROM DailyUsage WHERE date = ?",
            (today,),
        )
        for app, seconds in c.fetchall():
            self.usage_today[app] += seconds

    def setup_tray_icon(self):
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        self.tray_icon.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
        )
        self.tray_icon.setToolTip("Screen Time")
        tray_menu = QtWidgets.QMenu()
        show_action = tray_menu.addAction("Open")
        quit_action = tray_menu.addAction("Exit")
        show_action.triggered.connect(self.show_normal)
        quit_action.triggered.connect(self.exit_app)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.DoubleClick:
            self.show_normal()

    def show_normal(self):
        self.show()
        self.setWindowState(QtCore.Qt.WindowNoState)
        try:
            if (
                self._statistics_page is not None
                and self.stack.currentWidget() is self._statistics_page
            ):
                self._statistics_page.reload()
        except Exception:
            pass

    def update_total_usage(self):
        total_seconds = sum(self.usage_today.values())
        formatted_total = str(datetime.timedelta(seconds=int(total_seconds)))
        self.header.setText(f"Todays App Usage (Total: {formatted_total})")

    def update_tracking(self):
        if IS_WAYLAND:
            self.update_wayland_tracking()
            return

        now = datetime.datetime.now()
        if now.date() != self.last_switch_time.date():
            self.last_switch_time = now

        if IS_LINUX and is_screen_locked_linux():
            # Don't count time on the lock screen
            self.current_process = ""
            self.last_switch_time = now
            return

        raw_active_app, active_pid = get_active_window_process_name()

        if raw_active_app == self.current_process and self.current_process:
            self.update_total_usage()
            self.update_table(live_update=True)
        else:
            duration = (now - self.last_switch_time).total_seconds()
            if self.current_process and duration > 0:
                self.usage_today[self.current_process] += duration
                DataManager.add_daily_usage(self.current_process, duration)
            self.current_process = raw_active_app
            self.current_pid = active_pid
            if raw_active_app and active_pid:
                self.last_pid_for_app[raw_active_app] = active_pid
            self.last_switch_time = now
            self.update_total_usage()
            self.update_table(live_update=False)

    def update_table(self, live_update=False):
        if not self.isVisible():
            return

        display_usage = self.usage_today.copy()
        if live_update and self.current_process:
            delta = (datetime.datetime.now() - self.last_switch_time).total_seconds()
            display_usage[self.current_process] = (
                display_usage.get(self.current_process, 0) + delta
            )

        total = sum(display_usage.values())
        if total == 0:
            self.update_total_usage()
            self.table.setRowCount(0)
            self._last_display_usage = {}
            return

        self.update_total_usage()

        aggregated = {}
        for raw_app, seconds in display_usage.items():
            display_name, icon_hint = app_mapping.resolve(raw_app)
            label = display_name.title() if display_name else raw_app
            info = aggregated.get(label)
            if info is None:
                aggregated[label] = {
                    "seconds": seconds,
                    "raw": raw_app,
                    "icon_hint": icon_hint,
                }
            else:
                info["seconds"] += seconds

        sorted_apps = sorted(
            aggregated.items(), key=lambda x: x[1]["seconds"], reverse=True
        )
        self.table.setRowCount(len(sorted_apps))

        new_display_cache = {}
        scroll_bar = self.table.verticalScrollBar()
        scroll_value_before = scroll_bar.value()

        for row, (label, info) in enumerate(sorted_apps):
            seconds = info["seconds"]
            raw_for_icon = info["raw"]
            icon_hint = info["icon_hint"]

            percentage = seconds / total * 100
            formatted_time = str(datetime.timedelta(seconds=int(seconds)))
            icon_pid = self.last_pid_for_app.get(raw_for_icon)
            icon = icon_manager.get_icon_for_app(raw_for_icon, icon_hint, pid=icon_pid)

            item_icon = self.table.item(row, 0)
            if item_icon is None:
                item_icon = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, 0, item_icon)
            item_icon.setIcon(icon)

            item_name = self.table.item(row, 1)
            if item_name is None:
                item_name = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, 1, item_name)
            item_name.setText(label)
            # Store raw key so context menu can look it up
            item_name.setData(QtCore.Qt.UserRole, raw_for_icon)

            item_time = self.table.item(row, 2)
            if item_time is None:
                item_time = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, 2, item_time)
            item_time.setText(formatted_time)

            progress: QtWidgets.QProgressBar = self.table.cellWidget(row, 3)
            if progress is None:
                progress = QtWidgets.QProgressBar()
                progress.setMinimum(0)
                progress.setMaximum(100)
                progress.setAlignment(QtCore.Qt.AlignCenter)
                self.table.setCellWidget(row, 3, progress)
            progress.setValue(int(percentage))
            progress.setFormat(f"{percentage:.1f}%")

            new_display_cache[label] = seconds

        QtCore.QTimer.singleShot(0, lambda: scroll_bar.setValue(scroll_value_before))
        self._last_display_usage = new_display_cache

    def on_table_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        name_item = self.table.item(row, 1)
        if name_item is None:
            return
        raw_key = name_item.data(QtCore.Qt.UserRole) or name_item.text()
        display_name = name_item.text()
        menu = QtWidgets.QMenu(self)
        action = menu.addAction("✏  Customize - " + display_name)
        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen == action:
            self.open_customize_dialog(raw_key, display_name)

    def open_customize_dialog(self, raw_key: str, current_display: str):
        dlg = CustomizeAppDialog(raw_key, current_display, app_mapping, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            # Reload mapping and flush icon cache (memory + disk) so changes
            # appear immediately instead of showing a stale cached icon
            app_mapping.load()
            # Only drop the cached icon for THIS app. clear_cache() would
            # remove every other app's icon too
            if hasattr(icon_manager, "invalidate_app"):
                icon_manager.invalidate_app(raw_key)
            elif hasattr(icon_manager, "clear_cache"):
                icon_manager.clear_cache()
            elif hasattr(icon_manager, "app_icons"):
                icon_manager.app_icons.clear()
            self.update_table(live_update=False)

    def exit_app(self):
        now = datetime.datetime.now()
        duration = (now - self.last_switch_time).total_seconds()
        if self.current_process and duration > 0:
            self.usage_today[self.current_process] += duration
            DataManager.add_daily_usage(self.current_process, duration)
        # Make sure nothing buffered in memory is lost on exit.
        DataManager.flush()
        _instance_lock.release()
        logger.info("Quitting...")
        QtWidgets.QApplication.quit()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        # Cheap safety checkpoint: persist buffered usage whenever the
        # window is sent to the tray.
        DataManager.flush()
        self.tray_icon.showMessage(
            "Screen Time",
            "The App will continue to run in the Background.",
            QtWidgets.QSystemTrayIcon.Information,
            2000,
        )


########################################################################
# Platform-specific Icon Manager
########################################################################

try:
    if IS_WINDOWS:
        try:
            from icon_manager_win import IconManager as PlatformIconManager
        except ImportError:
            from icon_manager import IconManager as PlatformIconManager
    else:
        from icon_manager import ImprovedIconManager as PlatformIconManager

    icon_manager = PlatformIconManager()

except Exception:

    class _FallbackIconManager:
        def get_icon_for_app(
            self,
            app_name: str,
            icon_hint: str | None = None,
            pid: str | None = None,
        ):
            return QtWidgets.QApplication.style().standardIcon(
                QtWidgets.QStyle.SP_FileIcon
            )

    icon_manager = _FallbackIconManager()


########################################################################
# Entry Point
########################################################################


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QtGui.QFont("Segoe UI", 12))
    app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())

    window = MainWindow()

    if not args.hidden:
        window.show()
    else:
        window.hide()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
