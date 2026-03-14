#!/home/user/venv/bin/python
import datetime
import os
import platform
import sys
from collections import defaultdict

from PyQt5 import QtCore, QtGui, QtWidgets

import map_resolve
from data_manager import DataManager

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"


class StatisticsCache:
    _cache = {}
    _MAX = 20  # cap to avoid unbounded growth

    @classmethod
    def get(cls, key):
        return cls._cache.get(key)

    @classmethod
    def set(cls, key, value):
        if len(cls._cache) >= cls._MAX:
            # Drop the oldest entry
            cls._cache.pop(next(iter(cls._cache)))
        cls._cache[key] = value


def _compute_statistics(from_date, to_date, aggregation):
    """Compute statistics synchronously. Returns (time_series, per_app, total_seconds)."""
    cache_key = (
        from_date.isoformat(),
        to_date.isoformat(),
        aggregation,
        DataManager.get_data_version(),
    )
    cached = StatisticsCache.get(cache_key)
    if cached:
        return cached["time_series"], cached["per_app"], cached["total_seconds"]

    rows = DataManager.get_daily_usage(from_date.isoformat(), to_date.isoformat())

    time_series = defaultdict(float)
    per_app = defaultdict(float)

    for date, app_name, seconds in rows:
        if aggregation == "day":
            bucket_key = date
        elif aggregation == "week":
            y, w, _ = datetime.date.fromisoformat(date).isocalendar()
            bucket_key = f"{y}-W{w:02d}"
        else:  # month
            bucket_key = date[:7]
        time_series[bucket_key] += seconds
        per_app[app_name] += seconds

    # Fill missing buckets with 0
    if aggregation == "day":
        current = from_date
        while current <= to_date:
            time_series.setdefault(current.isoformat(), 0)
            current += datetime.timedelta(days=1)
    elif aggregation == "week":
        current = from_date
        while current <= to_date:
            y, w, _ = current.isocalendar()
            time_series.setdefault(f"{y}-W{w:02d}", 0)
            current += datetime.timedelta(weeks=1)
    else:  # month
        current = from_date
        while current <= to_date:
            time_series.setdefault(current.strftime("%Y-%m"), 0)
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

    total_seconds = sum(time_series.values())
    StatisticsCache.set(
        cache_key,
        {
            "time_series": dict(time_series),
            "per_app": dict(per_app),
            "total_seconds": total_seconds,
        },
    )
    return time_series, per_app, total_seconds


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
    def __init__(self, stack, icon_manager, app_mapping, parent=None):
        # Lazy-import matplotlib here so it is only loaded when the Statistics
        # page is first created (i.e. when the user opens it), not at startup.
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure

        self._FigureCanvas = FigureCanvas
        self._Figure = Figure

        super().__init__(parent)
        self.stack = stack
        self.icon_manager = icon_manager
        self.app_mapping = app_mapping
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
        self.figure = self._Figure()
        self.canvas = self._FigureCanvas(self.figure)
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
        QtWidgets.QApplication.processEvents()  # force repaint before blocking call

        time_series, per_app, total_seconds = _compute_statistics(from_date, now, agg)
        self.on_ready(time_series, per_app, total_seconds)

    def on_ready(self, time_series, per_app, total_seconds):

        self.overlay.hide()
        formatted_total = str(datetime.timedelta(seconds=int(total_seconds)))
        self.total_label.setText(f"Total Usage: {formatted_total}")

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        keys = sorted(time_series.keys())
        values = [time_series[k] / 3600 for k in keys]
        ax.plot(keys, values, marker="o")
        ax.set_ylabel("Hours")
        ax.grid(True)
        ax.tick_params(axis="x", rotation=45)
        self.canvas.draw()

        sorted_apps = sorted(per_app.items(), key=lambda x: x[1], reverse=True)
        self.table.setRowCount(len(sorted_apps))
        for row_idx, (app, seconds) in enumerate(sorted_apps):
            display_name, icon_hint = self.app_mapping.resolve(app)
            icon = self.icon_manager.get_icon_for_app(app, icon_hint)

            icon_item = QtWidgets.QTableWidgetItem()
            icon_item.setIcon(icon)
            name_item = QtWidgets.QTableWidgetItem(display_name.title())
            time_item = QtWidgets.QTableWidgetItem(
                str(datetime.timedelta(seconds=int(seconds)))
            )

            self.table.setItem(row_idx, 0, icon_item)
            self.table.setItem(row_idx, 1, name_item)
            self.table.setItem(row_idx, 2, time_item)
