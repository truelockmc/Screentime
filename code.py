#!/home/user/venv/bin/python
import sys
import os
import platform
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    import winreg
    import extraction
else:
    import subprocess
    extraction = None

MAPPING_PATH = "~/.config/screentime/wm_class_map.json"

try:
    from window_resolver import get_active_app
except Exception:
    print("falling back on xdotool")
    get_active_app = None

import sqlite3
import datetime
import psutil
from collections import defaultdict
import logging

from PyQt5.QtGui import QIcon

from PyQt5.QtCore import QByteArray, QBuffer, QIODevice
from PyQt5 import QtWidgets, QtGui, QtCore
from PIL import Image
from io import BytesIO
import qdarkstyle

# Matplotlib in PyQt5 einbetten
import matplotlib

matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


########################################################################
# Logging-Setup
########################################################################

log_file = os.path.join(BASE_DIR, "log.txt")
logging.basicConfig(
    #level=logging.DEBUG,        # enable debug mode
    level=logging.CRITICAL + 1, # disable debug mode
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("Starting...")

########################################################################
# Datenpersistenz: DataManager mit SQLite
########################################################################

class DataManager:
    
    DB_PATH = os.path.join(BASE_DIR, "usageData.db")

    @staticmethod
    def initialize_database():
        try:
            conn = sqlite3.connect(DataManager.DB_PATH)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS DailyUsage (
                    date TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    PRIMARY KEY (date, app_name)
                )
            """)

            conn.commit()
            conn.close()
            logger.info("Datenbank initialisiert: %s", DataManager.DB_PATH)
        except Exception as e:
            logger.exception("Fehler bei der Initialisierung der Datenbank:")

    @staticmethod
    def add_daily_usage(app_name, seconds, date=None):
        if not date:
            date = datetime.date.today().isoformat()

        conn = sqlite3.connect(DataManager.DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO DailyUsage (date, app_name, duration_seconds)
            VALUES (?, ?, ?)
            ON CONFLICT(date, app_name)
            DO UPDATE SET duration_seconds = duration_seconds + ?
        """, (date, app_name, seconds, seconds))
        conn.commit()
        conn.close()

    @staticmethod
    def get_daily_usage(from_date, to_date):
        conn = sqlite3.connect(DataManager.DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT date, app_name, duration_seconds
            FROM DailyUsage
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """, (from_date, to_date))
        rows = c.fetchall()
        conn.close()
        return rows
                    
# Startup Features

def get_executable_path():
    return sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)

def add_to_autostart():
    try:
        if IS_WINDOWS:
            exe_path = get_executable_path()
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "ScreenTimeApp", 0, winreg.REG_SZ, exe_path)
            winreg.CloseKey(key)
            logger.info("Autostart (Windows) hinzugefügt.")
        elif IS_LINUX:
            exe_path = get_executable_path()
            autostart_dir = Path.home() / ".config" / "autostart"
            autostart_dir.mkdir(parents=True, exist_ok=True)
            desktop_file = autostart_dir / "screentime.desktop"
            content = f"""[Desktop Entry]
Type=Application
Name=ScreenTimeApp
Exec="{exe_path}"
X-GNOME-Autostart-enabled=true
"""
            desktop_file.write_text(content, encoding="utf-8")
            logger.info("Autostart (Linux .desktop) hinzugefügt: %s", desktop_file)
        else:
            logger.warning("Autostart wird auf dieser Plattform nicht unterstützt.")
    except Exception:
        logger.exception("Fehler beim Hinzufügen zum Autostart:")

def remove_from_autostart():
    try:
        if IS_WINDOWS:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, "ScreenTimeApp")
            winreg.CloseKey(key)
            logger.info("Autostart (Windows) entfernt.")
        elif IS_LINUX:
            desktop_file = Path.home() / ".config" / "autostart" / "screentime.desktop"
            if desktop_file.exists():
                desktop_file.unlink()
                logger.info("Autostart (Linux) entfernt: %s", desktop_file)
        else:
            logger.warning("Autostart wird auf dieser Plattform nicht unterstützt.")
    except Exception:
        logger.exception("Fehler beim Entfernen des Autostarts:")

########################################################################
# Ermitteln des aktuell aktiven Prozesses
########################################################################

def get_active_window_process_name():
    if IS_WINDOWS:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd == 0:
                return ""
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = psutil.Process(pid.value)
            return process.name()
        except Exception:
            logger.exception("Fehler beim Ermitteln des aktiven Fensters (Windows):")
            return ""
    elif IS_LINUX:
        try:
            if get_active_app is not None:
                try:
                    info = get_active_app(mapping_path=MAPPING_PATH)
                except Exception:
                    info = {}
                name = info.get('app_name') or info.get('app_id') or None
                if name:
                    return name
                proc_path = info.get('proc_path')
                if proc_path:
                    try:
                        return Path(proc_path).name
                    except Exception:
                        pass
                wm_pid = info.get('wm_pid')
                if wm_pid:
                    try:
                        return psutil.Process(int(wm_pid)).name()
                    except Exception:
                        pass
            try:
                out = subprocess.check_output(["xdotool", "getwindowfocus", "getwindowpid"], stderr=subprocess.DEVNULL)
                pid = int(out.strip())
                return psutil.Process(pid).name()
            except FileNotFoundError:
                logger.warning("xdotool nicht gefunden; aktives Fenster unter Linux nicht bestimmt.")
                return ""
            except Exception:
                logger.exception("Fehler beim Ermitteln des aktiven Fensters (Linux):")
                return ""
        except Exception:
            logger.exception("Fehler in get_active_window_process_name (Linux):")
            return ""
    else:
        return ""

# Dont count time on Lockscreen
def is_screen_locked_linux():
    try:
        out = subprocess.check_output([
            "gdbus", "call",
            "--session",
            "--dest", "org.gnome.ScreenSaver",
            "--object-path", "/org/gnome/ScreenSaver",
            "--method", "org.gnome.ScreenSaver.GetActive"
        ], stderr=subprocess.DEVNULL)
        return "true" in out.decode().lower()
    except Exception:
        return False


########################################################################
# Fenster für die Diagramm-Ansicht (Statistiken)
########################################################################

class ChartWindow(QtWidgets.QDialog):
    def __init__(self, from_time, to_time, aggregation="day", show_app_list=False, parent=None):
        """
        aggregation: "day" für Wochendiagramm (x = Tage),
                     "week" für Monatsdiagramm (x = Kalenderwochen),
                     "month" für Jahresdiagramm (x = Monate)
        """
        super().__init__(parent)
        self.setWindowTitle("Statistiken")
        self.resize(900, 600)
        self.from_time = from_time
        self.to_time = to_time
        self.aggregation = aggregation
        self.show_app_list = show_app_list

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Matplotlib-Figur
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        main_layout.addWidget(self.canvas)
        self.plot_usage()

        if self.show_app_list:
            label = QtWidgets.QLabel("App-usage in this time period:")
            label.setFont(QtGui.QFont("Segoe UI", 14))
            main_layout.addWidget(label)
            self.table = QtWidgets.QTableWidget()
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(["", "App", "Time used", "Ratio"])
            self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setStyleSheet("font-size: 14pt;")
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self.table)
            main_layout.addWidget(scroll)
            self.populate_app_table(from_time, to_time)

    def plot_usage(self):
        from_date = self.from_time.date().isoformat()
        to_date = self.to_time.date().isoformat()

        rows = DataManager.get_daily_usage(from_date, to_date)

        agg = defaultdict(float)

        if self.aggregation == "day":
            for date, _, seconds in rows:
                agg[date] += seconds
            x_labels = [d[8:10] + "." + d[5:7] for d in sorted(agg)]
            y_values = [agg[d] / 3600 for d in sorted(agg)]
            title = "Screentime per Day (Hours)"

        elif self.aggregation == "week":
            for date, _, seconds in rows:
                year, week, _ = datetime.date.fromisoformat(date).isocalendar()
                key = f"{year}-KW{week:02d}"
                agg[key] += seconds
            x_labels = sorted(agg)
            y_values = [agg[k] / 3600 for k in x_labels]
            title = "Screentime per Week (Hours)"

        elif self.aggregation == "month":
            for date, _, seconds in rows:
                key = date[:7]  # YYYY-MM
                agg[key] += seconds
            x_labels = sorted(agg)
            y_values = [agg[k] / 3600 for k in x_labels]
            title = "Screentime per Month (Hours)"

        ax = self.figure.add_subplot(111)
        ax.clear()
        ax.plot(x_labels, y_values, marker='o')
        ax.set_title(title)
        ax.set_ylabel("Hours")
        ax.grid(True)
        ax.tick_params(axis='x', rotation=45)
        self.canvas.draw()

    def populate_app_table(self, from_time, to_time):
        from_date = from_time.date().isoformat()
        to_date = to_time.date().isoformat()

        rows = DataManager.get_daily_usage(from_date, to_date)
        app_usage = defaultdict(float)

        for _, app, seconds in rows:
            app_usage[app] += seconds

        total = sum(app_usage.values())
        apps = sorted(app_usage.items(), key=lambda x: x[1], reverse=True)

        self.table.setRowCount(len(apps))

        for row, (app, seconds) in enumerate(apps):
            percentage = (seconds / total * 100) if total > 0 else 0
            formatted_time = str(datetime.timedelta(seconds=int(seconds)))
            icon = icon_manager.get_icon_for_app(app)

            icon_item = QtWidgets.QTableWidgetItem()
            icon_item.setIcon(icon)

            self.table.setItem(row, 0, icon_item)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(app))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(formatted_time))

            bar = QtWidgets.QProgressBar()
            bar.setValue(int(percentage))
            bar.setFormat(f"{percentage:.1f}%")
            self.table.setCellWidget(row, 3, bar)

# Settings

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(400, 200)
        layout = QtWidgets.QVBoxLayout(self)
        self.chk_autostart = QtWidgets.QCheckBox("Open on Startup")
        self.chk_autostart.setChecked(True)  # active by default
        layout.addWidget(self.chk_autostart)
        self.chk_option = QtWidgets.QCheckBox("Option A")
        layout.addWidget(self.chk_option)
        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_settings(self):
        return {"autostart": self.chk_autostart.isChecked(),
                "option_a": self.chk_option.isChecked()}

########################################################################
# Hauptfenster der Anwendung
########################################################################

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Screen Time")
        self.resize(900, 600)
        self.setFont(QtGui.QFont("Segoe UI", 12))

        self.usage_today = defaultdict(float)
        self.current_process = ""
        self.last_switch_time = datetime.datetime.now()

        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.header = QtWidgets.QLabel("Screentime today")
        self.header.setFont(QtGui.QFont("Segoe UI", 16))
        main_layout.addWidget(self.header)
        
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "App", "Time used", "Ratio"])
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setStyleSheet("font-size: 14pt;")
        main_layout.addWidget(self.table)
        
        # Settings Button oben rechts hinzufügen:
        self.settings_button = QtWidgets.QToolButton(self)
        self.settings_button.setText("⚙")  # alternativ: self.settings_button.setIcon(QtGui.QIcon("path/to/settings_icon.png"))
        self.settings_button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.clicked.connect(self.open_settings)
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addStretch()
        top_layout.addWidget(self.settings_button)
        main_layout.insertLayout(0, top_layout)
 
        button_layout = QtWidgets.QHBoxLayout()
        self.btn_weekly = QtWidgets.QPushButton("Weekly Statistics")
        self.btn_monthly = QtWidgets.QPushButton("Monthly Statistics")
        self.btn_yearly = QtWidgets.QPushButton("Yearly Statistics")
        self.btn_exit = QtWidgets.QPushButton("Quit")
        for btn in (self.btn_weekly, self.btn_monthly, self.btn_yearly, self.btn_exit):
            btn.setFont(QtGui.QFont("Segoe UI", 14))
            button_layout.addWidget(btn)
        main_layout.addLayout(button_layout)

        self.btn_weekly.clicked.connect(self.show_weekly_stats)
        self.btn_monthly.clicked.connect(self.show_monthly_stats)
        self.btn_yearly.clicked.connect(self.show_yearly_stats)
        self.btn_exit.clicked.connect(self.exit_app)
        
        self.qsettings = QtCore.QSettings("true_lock", "Screen Time")
        autostart_enabled = self.qsettings.value("autostart", True, type=bool)
        if autostart_enabled:
            add_to_autostart()
        else:
            remove_from_autostart()

        self.setup_tray_icon()

        self.timer = QtCore.QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_tracking)
        self.timer.start()

        DataManager.initialize_database()
        self.load_usage_from_db()
        
        total_seconds = sum(self.usage_today.values())
        formatted_total = str(datetime.timedelta(seconds=int(total_seconds)))
        self.header.setText(f"Todays App Usage (Total: {formatted_total})")
        
    def open_settings(self):
        dlg = SettingsDialog(self)
        # Vorbelegen der Dialogfelder mit gespeicherten Einstellungen:
        autostart_enabled = self.qsettings.value("autostart", True, type=bool)
        dlg.chk_autostart.setChecked(autostart_enabled)
        option_a = self.qsettings.value("option_a", False, type=bool)
        dlg.chk_option.setChecked(option_a)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            settings = dlg.get_settings()
            self.qsettings.setValue("autostart", settings["autostart"])
            self.qsettings.setValue("option_a", settings["option_a"])
            if settings["autostart"]:
                add_to_autostart()
            else:
                remove_from_autostart()

    def load_usage_from_db(self):
        today = datetime.date.today().isoformat()

        conn = sqlite3.connect(DataManager.DB_PATH)
        c = conn.cursor()

        c.execute("""
            SELECT app_name, duration_seconds
            FROM DailyUsage
            WHERE date = ?
        """, (today,))

        for app, seconds in c.fetchall():
            self.usage_today[app] += seconds

        conn.close()

    def setup_tray_icon(self):
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon))
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
        
    def update_total_usage(self):
        total_seconds = sum(self.usage_today.values())
        formatted_total = str(datetime.timedelta(seconds=int(total_seconds)))
        self.header.setText(f"Todays App Usage (Total: {formatted_total})")

    def update_tracking(self):

        now = datetime.datetime.now()

        if now.date() != self.last_switch_time.date():
            self.last_switch_time = now


        if IS_LINUX and is_screen_locked_linux():
            # Dont count time on Lockscreen
            self.current_process = ""
            self.last_switch_time = now
            return

        active_app = get_active_window_process_name()
        if active_app == self.current_process and self.current_process:
            self.update_total_usage()
            self.update_table(live_update=True)
        else:
            duration = (now - self.last_switch_time).total_seconds()

            if self.current_process and duration >= 5:
                self.usage_today[self.current_process] += duration
                DataManager.add_daily_usage(self.current_process, duration)

            self.current_process = active_app
            self.last_switch_time = now

            self.update_total_usage()
            self.update_table(live_update=False)

    def update_table(self, live_update=False):
        display_usage = self.usage_today.copy()
        if live_update and self.current_process:
            delta = (datetime.datetime.now() - self.last_switch_time).total_seconds()
            display_usage[self.current_process] = display_usage.get(self.current_process, 0) + delta

        total = sum(display_usage.values())
        v_scroll = self.table.verticalScrollBar().value()
        self.table.setRowCount(0)
        for app, seconds in sorted(display_usage.items(), key=lambda x: x[1], reverse=True):
            percentage = (seconds / total * 100) if total > 0 else 0
            formatted_time = str(datetime.timedelta(seconds=int(seconds)))
            row = self.table.rowCount()
            self.table.insertRow(row)
            icon = icon_manager.get_icon_for_app(app)
            icon_item = QtWidgets.QTableWidgetItem()
            icon_item.setIcon(icon)
            name_item = QtWidgets.QTableWidgetItem(app)
            time_item = QtWidgets.QTableWidgetItem(formatted_time)
            progress = QtWidgets.QProgressBar()
            progress.setMinimum(0)
            progress.setMaximum(100)
            progress.setValue(int(percentage))
            progress.setFormat(f"{percentage:.1f}%")
            progress.setAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(row, 0, icon_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, time_item)
            self.table.setCellWidget(row, 3, progress)
        self.table.resizeRowsToContents()
        self.table.verticalScrollBar().setValue(v_scroll)

    def show_stats_window(self, from_time, to_time, show_app_list=False):
        dlg = ChartWindow(from_time, to_time, show_app_list, self)
        dlg.exec_()

    def show_weekly_stats(self):
        to_time = datetime.datetime.now()
        from_time = to_time - datetime.timedelta(days=7)
        # Für eine Woche: Aggregation pro Tag ("day")
        dlg = ChartWindow(from_time, to_time, aggregation="day", show_app_list=True, parent=self)
        dlg.exec_()

    def show_monthly_stats(self):
        to_time = datetime.datetime.now()
        from_time = to_time - datetime.timedelta(days=30)
        # Für einen Monat: Aggregation pro Kalenderwoche ("week")
        dlg = ChartWindow(from_time, to_time, aggregation="week", show_app_list=False, parent=self)
        dlg.exec_()

    def show_yearly_stats(self):
        to_time = datetime.datetime.now()
        from_time = to_time - datetime.timedelta(days=365)
        # Für ein Jahr: Aggregation pro Monat ("month")
        dlg = ChartWindow(from_time, to_time, aggregation="month", show_app_list=False, parent=self)
        dlg.exec_()

    def exit_app(self):
        now = datetime.datetime.now()
        duration = (now - self.last_switch_time).total_seconds()

        if self.current_process and duration > 0:
            self.usage_today[self.current_process] += duration
            DataManager.add_daily_usage(self.current_process, duration)

        logger.info("Quitting...")
        QtWidgets.QApplication.quit()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray_icon.showMessage("Screen Time",
                                   "The App will continue to run in the Background.",
                                   QtWidgets.QSystemTrayIcon.Information, 2000)

########################################################################
# Main
########################################################################
# platform-specific Icon Manager Initialization
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
        def get_icon_for_app(self, name):
            return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)

    icon_manager = _FallbackIconManager()

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Segoe UI", 12))
    app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
