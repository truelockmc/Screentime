#!/home/user/venv/bin/python
import os
import sqlite3
import datetime
import sys
import logging

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

log_file = os.path.join(BASE_DIR, "log.txt")
logging.basicConfig(
    #level=logging.DEBUG,        # enable debug mode
    level=logging.CRITICAL, # disable debug mode
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("Starting...")

########################################################################
# Database Manager
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
