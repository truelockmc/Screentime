import sqlite3
import datetime

DB_PATH = "usageData.db"

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.execute("""
    CREATE TABLE IF NOT EXISTS DailyUsage (
        date TEXT NOT NULL,
        app_name TEXT NOT NULL,
        duration_seconds REAL NOT NULL,
        PRIMARY KEY (date, app_name)
    )
""")

c.execute("""
    SELECT app_name,
           DATE(start_time) AS day,
           SUM(duration_seconds)
    FROM UsageRecords
    GROUP BY app_name, day
""")

rows = c.fetchall()

for app, day, seconds in rows:
    c.execute("""
        INSERT INTO DailyUsage (date, app_name, duration_seconds)
        VALUES (?, ?, ?)
        ON CONFLICT(date, app_name)
        DO UPDATE SET duration_seconds = duration_seconds + ?
    """, (day, app, seconds, seconds))

conn.commit()

print(f"Migriert: {len(rows)} App/Tag-Kombinationen")

conn.close()
