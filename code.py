#!/home/user/venv/bin/python
import sys
import os
import ctypes
from ctypes import wintypes
import sqlite3
import datetime
import psutil
from collections import defaultdict
import logging

from PyQt5.QtGui import QIcon

import extraction

from PyQt5.QtCore import QByteArray, QBuffer, QIODevice
from PyQt5 import QtWidgets, QtGui, QtCore
from PIL import Image
from io import BytesIO
import qdarkstyle

# Matplotlib in PyQt5 einbetten
import matplotlib

from extraction import IconSize

matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import winreg

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
    handlers=[logging.FileHandler(log_file, encoding='utf-8'),
              logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
logger.info("Programmstart")

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
            c.execute('''
                CREATE TABLE IF NOT EXISTS UsageRecords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_name TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    duration_seconds REAL NOT NULL
                )
            ''')
            conn.commit()
            conn.close()
            logger.info("Datenbank initialisiert: %s", DataManager.DB_PATH)
        except Exception as e:
            logger.exception("Fehler bei der Initialisierung der Datenbank:")

    @staticmethod
    def save_usage_record(app_name, start_time, end_time, duration_seconds):
        try:
            conn = sqlite3.connect(DataManager.DB_PATH)
            c = conn.cursor()
            c.execute('''
                INSERT INTO UsageRecords (app_name, start_time, end_time, duration_seconds)
                VALUES (?, ?, ?, ?)
            ''', (app_name,
                  start_time.isoformat(),
                  end_time.isoformat(),
                  duration_seconds))
            conn.commit()
            conn.close()
            logger.info("Gespeicherter Datensatz: %s, Dauer: %s Sekunden", app_name, duration_seconds)
        except Exception as e:
            logger.exception("Fehler beim Speichern des Datensatzes:")

    @staticmethod
    def get_usage_records(from_time, to_time):
        try:
            conn = sqlite3.connect(DataManager.DB_PATH)
            c = conn.cursor()
            c.execute('''
                SELECT id, app_name, start_time, end_time, duration_seconds
                FROM UsageRecords
                WHERE start_time >= ? AND end_time <= ?
            ''', (from_time.isoformat(), to_time.isoformat()))
            rows = c.fetchall()
            conn.close()

            records = []
            for row in rows:
                records.append({
                    'id': row[0],
                    'app_name': row[1],
                    'start_time': datetime.datetime.fromisoformat(row[2]),
                    'end_time': datetime.datetime.fromisoformat(row[3]),
                    'duration_seconds': row[4]
                })
            logger.info("Geladene Datensätze: %d", len(records))
            return records
        except Exception as e:
            logger.exception("Fehler beim Laden der Datensätze:")
            return []
                    
# Startup Features

def add_to_autostart():
    try:
        exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "ScreenTimeApp", 0, winreg.REG_SZ, exe_path)
        winreg.CloseKey(key)
        logger.info("Autostart hinzugefügt.")
    except Exception:
        logger.exception("Fehler beim Hinzufügen zum Autostart:")

def remove_from_autostart():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, "ScreenTimeApp")
        winreg.CloseKey(key)
        logger.info("Autostart entfernt.")
    except Exception:
        logger.exception("Fehler beim Entfernen des Autostarts:")

########################################################################
# Windows-spezifische Funktionen für App-Icons mit Caching
########################################################################

class AppIcon():
    __identifier: str
    __raw_bytes: bytes
    __qicon: QIcon

    def __init__(self, identifier, raw_bytes):
        self.__identifier = identifier
        self.__raw_bytes = raw_bytes

        qt_bytes = QByteArray(raw_bytes)
        qt_buffer = QBuffer(qt_bytes)
        qt_buffer.open(QIODevice.ReadOnly)

        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(qt_bytes)
        self.__qicon = QIcon(pixmap)

    def get_bytes(self) -> bytes:
        return self.__raw_bytes

    def get_identifier(self) -> str:
        return self.__identifier

    def get_qicon(self) -> QIcon:
        return self.__qicon

class IconManager:

    app_icons:list[AppIcon] = []

    def get_icon_from_exe(self, exe_path):
        """
        Versucht, das Icon aus der EXE mittels ExtractIconExW bzw. SHGetFileInfoW zu extrahieren.
        """
        try:
            icon = extraction.extract_icon(exe_path, IconSize.LARGE)
            img = Image.frombytes("RGBA", (32, 32), icon, "raw", "BGRA")
            buffer = BytesIO()
            img.save(buffer, format="ICO")
            return buffer.getvalue()
        except Exception as e:
            logger.exception("Fehler beim Extrahieren des Icons aus %s:", exe_path)
        return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)

    def get_icon_for_app(self, app_name) -> QIcon:
        """
        Sucht anhand des Prozessnamens nach einem laufenden Prozess und versucht, sein Icon zu extrahieren.
        Verwendet dabei einen Cache-Ordner 'icons'.
        """

        try:

            for icon in self.app_icons:
                if app_name != icon.get_identifier():
                    continue
                return icon.get_qicon()

            for proc in psutil.process_iter(['name', 'exe']):
                if proc.info['name'] and proc.info['name'].lower() == app_name.lower() and proc.info['exe']:
                    icon = self.get_icon_from_exe(proc.info['exe'])
                    if icon == None:
                        logger.error("Fehler beim icon extrahieren von %s", app_name)
                        return None
                    appIcon = AppIcon(app_name, icon)
                    self.app_icons.append(appIcon)
                    logger.info("Icon für '%s' extrahiert und im Cache gespeichert.", app_name)
                    return appIcon.get_qicon()



        except Exception as e:
            logger.exception("Fehler beim Abrufen des Icons für %s:", app_name)
        logger.warning("Standard-Icon für '%s' verwendet.", app_name)
        return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)

########################################################################
# Ermitteln des aktuell aktiven Prozesses (Windows)
########################################################################

def get_active_window_process_name():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if hwnd == 0:
        return ""
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        process = psutil.Process(pid.value)
        return process.name()
    except Exception:
        return ""

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
            label = QtWidgets.QLabel("App-Nutzung in diesem Zeitraum:")
            label.setFont(QtGui.QFont("Segoe UI", 14))
            main_layout.addWidget(label)
            self.table = QtWidgets.QTableWidget()
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(["", "App", "Nutzungszeit", "Verhältnis"])
            self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setStyleSheet("font-size: 14pt;")
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self.table)
            main_layout.addWidget(scroll)
            self.populate_app_table(from_time, to_time)

    def plot_usage(self):
        records = DataManager.get_usage_records(self.from_time, self.to_time)
        aggregated = {}
        title = ""
        x_labels = []
        y_values = []

        if self.aggregation == "day":
            # Für Wochenansicht: pro Tag aggregieren (x = Tage)
            from collections import defaultdict
            agg = defaultdict(float)
            for rec in records:
                day = rec['start_time'].date()  # Schlüssel: Datum
                agg[day] += rec['duration_seconds']
            days = sorted(agg.keys())
            x_labels = [day.strftime("%d.%m") for day in days]
            # In Stunden umrechnen
            y_values = [agg[day] / 3600.0 for day in days]
            title = "Nutzungszeit pro Tag (Stunden)"
        elif self.aggregation == "week":
            # Für Monatsansicht: pro Kalenderwoche aggregieren
            from collections import defaultdict
            agg = defaultdict(float)
            for rec in records:
                year, week, _ = rec['start_time'].isocalendar()
                key = f"{year}-W{week:02d}"
                agg[key] += rec['duration_seconds']
            weeks = sorted(agg.keys())
            x_labels = weeks
            y_values = [agg[w] / 3600.0 for w in weeks]
            title = "Nutzungszeit pro Kalenderwoche (Stunden)"
        elif self.aggregation == "month":
            # Für Jahresansicht: pro Monat aggregieren
            from collections import defaultdict
            agg = defaultdict(float)
            for rec in records:
                key = rec['start_time'].strftime("%Y-%m")
                agg[key] += rec['duration_seconds']
            months = sorted(agg.keys())
            x_labels = months
            y_values = [agg[m] / 3600.0 for m in months]
            title = "Nutzungszeit pro Monat (Stunden)"

        ax = self.figure.add_subplot(111)
        ax.clear()
        ax.plot(x_labels, y_values, marker='o', color='cyan')
        ax.set_title(title, fontsize=14)
        ax.set_xlabel("Zeitraum", fontsize=12)
        ax.set_ylabel("Nutzungszeit (Stunden)", fontsize=12)
        ax.grid(True)
        for label in ax.get_xticklabels():
            label.set_rotation(45)
            label.set_fontsize(10)
        self.canvas.draw()

    def populate_app_table(self, from_time, to_time):
        # Bestehende Logik für die Tabelle bleibt unverändert
        records = DataManager.get_usage_records(from_time, to_time)
        from collections import defaultdict
        app_usage = defaultdict(float)
        for rec in records:
            app_usage[rec['app_name']] += rec['duration_seconds']
        total = sum(app_usage.values())
        apps = sorted(app_usage.items(), key=lambda x: x[1], reverse=True)
        self.table.setRowCount(len(apps))
        for row, (app, seconds) in enumerate(apps):
            percentage = (seconds / total * 100) if total > 0 else 0
            formatted_time = str(datetime.timedelta(seconds=int(seconds)))
            # Verwende icon_manager.get_icon_for_app() anstelle von get_icon_for_app()
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

# Settings

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.resize(400, 200)
        layout = QtWidgets.QVBoxLayout(self)
        self.chk_autostart = QtWidgets.QCheckBox("Open on Startup")
        self.chk_autostart.setChecked(True)  # Standardmäßig an
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

        self.setWindowTitle("ScreenTime App")
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

        self.header = QtWidgets.QLabel("Heutige App-Nutzung")
        self.header.setFont(QtGui.QFont("Segoe UI", 16))
        main_layout.addWidget(self.header)
        
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "App", "Nutzungszeit", "Verhältnis"])
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
        self.btn_weekly = QtWidgets.QPushButton("Wöchentliche Statistiken")
        self.btn_monthly = QtWidgets.QPushButton("Monatliche Statistiken")
        self.btn_yearly = QtWidgets.QPushButton("Jahresstatistiken")
        self.btn_exit = QtWidgets.QPushButton("Beenden")
        for btn in (self.btn_weekly, self.btn_monthly, self.btn_yearly, self.btn_exit):
            btn.setFont(QtGui.QFont("Segoe UI", 14))
            button_layout.addWidget(btn)
        main_layout.addLayout(button_layout)

        self.btn_weekly.clicked.connect(self.show_weekly_stats)
        self.btn_monthly.clicked.connect(self.show_monthly_stats)
        self.btn_yearly.clicked.connect(self.show_yearly_stats)
        self.btn_exit.clicked.connect(self.exit_app)
        
        self.qsettings = QtCore.QSettings("MyCompany", "ScreenTimeApp")
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
        today_start = datetime.datetime.combine(datetime.date.today(), datetime.time.min)
        now = datetime.datetime.now()
        records = DataManager.get_usage_records(today_start, now)
        for rec in records:
            self.usage_today[rec['app_name']] += rec['duration_seconds']
        logger.info("Tagesdaten geladen. Aktuelle Nutzung: %s", dict(self.usage_today))

    def setup_tray_icon(self):
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon))
        self.tray_icon.setToolTip("ScreenTime")
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
        active_app = get_active_window_process_name()
        if active_app == self.current_process and self.current_process:
            self.update_total_usage()
            self.update_table(live_update=True)
        else:
            duration = (now - self.last_switch_time).total_seconds()
            if self.current_process:
                self.usage_today[self.current_process] += duration
                DataManager.save_usage_record(self.current_process,
                                              self.last_switch_time,
                                              now,
                                              duration)
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
        logger.info("Beende Anwendung.")
        QtWidgets.QApplication.quit()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray_icon.showMessage("ScreenTime App",
                                   "Die App läuft weiter im Hintergrund.",
                                   QtWidgets.QSystemTrayIcon.Information, 2000)

########################################################################
# Main
########################################################################
icon_manager = IconManager()
def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Segoe UI", 12))
    app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()