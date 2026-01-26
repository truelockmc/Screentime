#!/home/user/venv/bin/python
import datetime
from collections import defaultdict
from PyQt5 import QtWidgets, QtCore
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from data_manager import DataManager

class StatisticsCache:
    _cache = {}

    @classmethod
    def get(cls, key):
        return cls._cache.get(key)

    @classmethod
    def set(cls, key, value):
        cls._cache[key] = value

class StatisticsWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(dict, dict)  # (time_series, per_app)

    def __init__(self, from_date, to_date, aggregation):
        super().__init__()
        self.from_date = from_date
        self.to_date = to_date
        self.aggregation = aggregation

    def run(self):
        cache_key = (
            self.from_date.isoformat(),
            self.to_date.isoformat(),
            self.aggregation
        )

        cached = StatisticsCache.get(cache_key)
        if cached:
            self.finished.emit(cached["time_series"], cached["per_app"])
            return

        rows = DataManager.get_daily_usage(
            self.from_date.isoformat(),
            self.to_date.isoformat()
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

        StatisticsCache.set(cache_key, {"time_series": time_series, "per_app": per_app})
        self.finished.emit(time_series, per_app)

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

class StatisticsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Statistics")
        self.resize(900, 600)

        layout = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        self.range_combo = QtWidgets.QComboBox()
        self.range_combo.addItems(["Week", "Month", "Year", "Custom"])
        self.range_combo.currentTextChanged.connect(self.reload)

        self.from_date = QtWidgets.QDateEdit(calendarPopup=True)
        self.to_date = QtWidgets.QDateEdit(calendarPopup=True)
        self.from_date.setVisible(False)
        self.to_date.setVisible(False)

        top.addWidget(QtWidgets.QLabel("Range:"))
        top.addWidget(self.range_combo)
        top.addWidget(self.from_date)
        top.addWidget(self.to_date)

        layout.addLayout(top)

        # Graph
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, stretch=2)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["App", "Total Time"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)

        self.overlay = LoadingOverlay(self)
        self.overlay.hide()

        self.reload()

    def reload(self):
        now = datetime.date.today()

        match self.range_combo.currentText():
            case "Week":
                from_date = now - datetime.timedelta(days=7)
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

    def on_ready(self, time_series, per_app):
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
        for row_idx, (app, seconds) in enumerate(sorted(per_app.items(), key=lambda x: x[1], reverse=True)):
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(app))
            self.table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(str(datetime.timedelta(seconds=int(seconds)))))
