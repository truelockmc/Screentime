#!/home/user/venv/bin/python
import datetime
import os
import platform
import sys
from collections import defaultdict

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5 import QtCore, QtGui, QtWidgets

import map_resolve
from data_manager import DataManager

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

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
        def get_icon_for_app(self, app_name: str, icon_hint: str | None = None):
            return QtWidgets.QApplication.style().standardIcon(
                QtWidgets.QStyle.SP_FileIcon
            )

    icon_manager = _FallbackIconManager()

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAPPING_PATH = os.path.join(BASE_DIR, "map.json")
app_mapping = map_resolve.AppMapping(MAPPING_PATH)


class StatisticsCache:
    _cache = {}

    @classmethod
    def get(cls, key):
        return cls._cache.get(key)

    @classmethod
    def set(cls, key, value):
        cls._cache[key] = value


class StatisticsWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(
        dict, dict, float
    )  # (time_series, per_app, total_seconds)

    def __init__(self, from_date, to_date, aggregation):
        super().__init__()
        self.from_date = from_date
        self.to_date = to_date
        self.aggregation = aggregation

    def run(self):
        cache_key = (
            self.from_date.isoformat(),
            self.to_date.isoformat(),
            self.aggregation,
            DataManager.get_data_version(),
        )

        cached = StatisticsCache.get(cache_key)
        if cached:
            self.finished.emit(
                cached["time_series"], cached["per_app"], cached["total_seconds"]
            )
            return

        rows = DataManager.get_daily_usage(
            self.from_date.isoformat(), self.to_date.isoformat()
        )

        time_series = defaultdict(float)
        per_app = defaultdict(float)

        for date, app_name, seconds in rows:
            if self.aggregation == "day":
                bucket_key = date
            elif self.aggregation == "week":
                y, w, _ = datetime.date.fromisoformat(date).isocalendar()
                bucket_key = f"{y}-W{w:02d}"
            else:  # month
                bucket_key = date[:7]
            time_series[bucket_key] += seconds
            per_app[app_name] += seconds

        # Fill missing days/weeks/months with 0
        if self.aggregation == "day":
            current = self.from_date
            while current <= self.to_date:
                date_str = current.isoformat()
                if date_str not in time_series:
                    time_series[date_str] = 0
                current += datetime.timedelta(days=1)
        elif self.aggregation == "week":
            current = self.from_date
            while current <= self.to_date:
                y, w, _ = current.isocalendar()
                week_key = f"{y}-W{w:02d}"
                if week_key not in time_series:
                    time_series[week_key] = 0
                current += datetime.timedelta(weeks=1)
        else:  # month
            current = self.from_date
            while current <= self.to_date:
                month_key = current.strftime("%Y-%m")
                if month_key not in time_series:
                    time_series[month_key] = 0
                # Move to next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)

        # Calculate total seconds
        total_seconds = sum(time_series.values())

        StatisticsCache.set(
            cache_key,
            {
                "time_series": time_series,
                "per_app": per_app,
                "total_seconds": total_seconds,
            },
        )
        self.finished.emit(time_series, per_app, total_seconds)


class LoadingOverlay(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("background-color: rgba(0,0,0,150);")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 0)
        bar.setFixedWidth(250)

        label = QtWidgets.QLabel("Loading statistics…")
        label.setStyleSheet("color:white; font-size:14pt")

        layout.addWidget(bar)
        layout.addWidget(label)


class StatisticsPage(QtWidgets.QWidget):
    def __init__(self, stack, parent=None):
        super().__init__(parent)
        self.stack = stack
        layout = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()

        back_btn = QtWidgets.QPushButton("← Back")
        back_btn.clicked.connect(self.go_back)

        top.addWidget(back_btn)
        top.addStretch()

        layout.addLayout(top)

        top = QtWidgets.QHBoxLayout()

        self.total_label = QtWidgets.QLabel("Total Usage: 0h")
        self.total_label.setFont(QtGui.QFont("Segoe UI", 16, QtGui.QFont.Bold))
        self.total_label.setStyleSheet("color: white;")
        top.addWidget(self.total_label)
        top.addStretch()
        top.addWidget(QtWidgets.QLabel("Range:"))

        self.range_combo = QtWidgets.QComboBox()
        self.range_combo.addItems(["Week", "Month", "Year", "Custom"])
        self.range_combo.setMinimumWidth(120)
        self.range_combo.currentTextChanged.connect(self.reload)
        top.addWidget(self.range_combo)

        self.from_date = QtWidgets.QDateEdit(calendarPopup=True)
        self.to_date = QtWidgets.QDateEdit(calendarPopup=True)
        self.from_date.setVisible(False)
        self.to_date.setVisible(False)

        top.addWidget(self.from_date)
        top.addWidget(self.to_date)

        layout.addLayout(top)

        # Graph
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, stretch=2)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Icon", "App", "Total Time"])
        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )

        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)

        self.overlay = LoadingOverlay(self)
        self.overlay.hide()

        self.reload()

    def go_back(self):
        self.stack.setCurrentIndex(0)

    def reload(self):
        thr = getattr(self, "_statistics_thread", None)
        if isinstance(thr, QtCore.QThread) and thr.isRunning():
            return
        now = datetime.date.today()

        match self.range_combo.currentText():
            case "Week":
                from_date = now - datetime.timedelta(days=6)
                agg = "day"
            case "Month":
                from_date = now - datetime.timedelta(days=30)
                agg = "week"
            case "Year":
                from_date = now - datetime.timedelta(days=365)
                agg = "month"
            case "Custom":
                self.from_date.setVisible(True)
                self.to_date.setVisible(True)
                from_date = self.from_date.date().toPyDate()
                now = self.to_date.date().toPyDate()
                agg = "day"
            case _:
                return

        self.overlay.resize(self.size())
        self.overlay.show()

        self.thread = QtCore.QThread()
        self.worker = StatisticsWorker(from_date, now, agg)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_ready)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def on_ready(self, time_series, per_app, total_seconds):

        formatted_total = str(datetime.timedelta(seconds=int(total_seconds)))
        self.total_label.setText(f"Total Usage: {formatted_total}")

        self.overlay.hide()
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        keys = sorted(time_series.keys())
        values = [time_series[k] / 3600 for k in keys]
        ax.plot(keys, values, marker="o")
        ax.set_ylabel("Hours")
        ax.grid(True)
        ax.tick_params(axis="x", rotation=45)
        self.canvas.draw()

        self.table.setRowCount(0)
        for row_idx, (app, seconds) in enumerate(
            sorted(per_app.items(), key=lambda x: x[1], reverse=True)
        ):
            self.table.insertRow(row_idx)

            display_name, icon_hint = app_mapping.resolve(app)
            icon = icon_manager.get_icon_for_app(app, icon_hint)

            icon_item = QtWidgets.QTableWidgetItem()
            icon_item.setIcon(icon)
            name_item = QtWidgets.QTableWidgetItem(display_name.title())
            time_item = QtWidgets.QTableWidgetItem(
                str(datetime.timedelta(seconds=int(seconds)))
            )

            self.table.setItem(row_idx, 0, icon_item)
            self.table.setItem(row_idx, 1, name_item)
            self.table.setItem(row_idx, 2, time_item)
