#!/home/user/venv/bin/python
import datetime
import logging
import os
import sqlite3
import sys
import threading

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

log_file = os.path.join(BASE_DIR, "log.txt")
logging.basicConfig(
    # level=logging.DEBUG,        # enable debug mode
    level=logging.ERROR,  # normal mode
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("Starting...")

########################################################################
# Database Manager
########################################################################


class DataManager:
    DB_PATH = os.path.join(BASE_DIR, "usageData.db")
    _conn: sqlite3.Connection = None  # persistent connection for all DB access

    # In-memory write buffer: (date, app_name) -> accumulated duration_seconds.
    # add_daily_usage() only touches this dict; nothing gets on disk until
    # flush() runs. This is what keeps SSD writes very low without affecting the
    # UI, since the UI is always driven by MainWindow.usage_today, never by
    # the database directly.
    _pending: dict = {}
    _lock = threading.Lock()

    @staticmethod
    def _get_conn() -> sqlite3.Connection:
        """Return (and lazily create) the single persistent connection."""
        if DataManager._conn is None:
            conn = sqlite3.connect(DataManager.DB_PATH, check_same_thread=False)
            # WAL = writers don't block readers and each commit is a small
            # append instead of a full journal file create/delete cycle.
            # synchronous=NORMAL is safe in WAL mode (no corruption risk,
            # only the very last commit could be lost on a power outage),
            # and it skips most of the fsync() calls that wear an SSD.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            DataManager._conn = conn
        return DataManager._conn

    @staticmethod
    def initialize_database():
        try:
            conn = DataManager._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS DailyUsage (
                    date TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    PRIMARY KEY (date, app_name)
                )
            """)
            conn.commit()
            logger.info("Database initialized: %s", DataManager.DB_PATH)
        except Exception:
            logger.exception("Error during database initialization:")

    @staticmethod
    def add_daily_usage(app_name, seconds, date=None):
        """Buffer a usage delta in memory. No disk I/O happens here."""
        if not date:
            date = datetime.date.today().isoformat()
        key = (date, app_name)
        with DataManager._lock:
            DataManager._pending[key] = DataManager._pending.get(key, 0.0) + seconds

    @staticmethod
    def flush():
        """Write all buffered deltas to disk in a single transaction.

        Cheap to call often: it's a no-op whenever there is nothing pending,
        so callers (periodic timer, app exit, tray minimize, stats reload)
        can call it defensively without worrying about extra SSD wear.
        """
        with DataManager._lock:
            if not DataManager._pending:
                return
            items = list(DataManager._pending.items())
            DataManager._pending.clear()

        conn = DataManager._get_conn()
        try:
            conn.executemany(
                """
                INSERT INTO DailyUsage (date, app_name, duration_seconds)
                VALUES (?, ?, ?)
                ON CONFLICT(date, app_name)
                DO UPDATE SET duration_seconds = duration_seconds + excluded.duration_seconds
                """,
                [(date, app_name, seconds) for (date, app_name), seconds in items],
            )
            conn.commit()
        except Exception:
            logger.exception("Error flushing pending usage to database:")
            # Roll back first: executemany may have already applied some
            # rows to the open (uncommitted) transaction before failing.
            # Without this, re-queuing "items" below and retrying later
            # would double-count those already-applied rows once they
            # eventually get committed.
            try:
                conn.rollback()
            except Exception:
                logger.exception("Error rolling back failed flush transaction:")
            # Don't lose the data: put it back so the next flush retries it.
            with DataManager._lock:
                for (date, app_name), seconds in items:
                    key = (date, app_name)
                    DataManager._pending[key] = (
                        DataManager._pending.get(key, 0.0) + seconds
                    )

    @staticmethod
    def get_daily_usage(from_date, to_date):
        # Flush first so a read (e.g. opening the statistics page) always
        # sees the latest data, even though writes are otherwise batched.
        DataManager.flush()
        conn = DataManager._get_conn()
        c = conn.cursor()
        c.execute(
            """
            SELECT date, app_name, duration_seconds
            FROM DailyUsage
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """,
            (from_date, to_date),
        )
        return c.fetchall()

    @staticmethod
    def get_data_version():
        # Reflects the timestamp of the last actual disk write (flush),
        # which is exactly what the statistics cache needs to invalidate on.
        return os.path.getmtime(DataManager.DB_PATH)
