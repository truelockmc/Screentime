#!/usr/bin/env python3
"""
Merge old/incorrect app names with correct/new app names in the Usage database (usage.db).
Before anything is written, usage.db.bak is created as a backup file incase anything should break.

This is useful if the app has been updated to correctly detect some appnames,
but there are still entries with the incorrect names.
You do probably not want to have two different entries for the same app on your statistics screen.

"""

import shutil
import sqlite3
import sys
from pathlib import Path

# old/incorrect name -> new/correct name
RENAMES = {
    "vesktop.bin": "Vesktop",
    "kate-bin": "Kate",
    "thunderbird-bin": "Thunderbird",
    "Mail": "Thunderbird",
    "steam_app_4320050": "Haunted Heist",
    "zen": "Zen Browser",
    "zed-editor": "Zed",
}


def merge(db_path: Path) -> None:
    backup_path = db_path.with_suffix(db_path.suffix + ".bak")
    shutil.copy2(db_path, backup_path)
    print(f"Backup created: {backup_path}")

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    for old_name, new_name in RENAMES.items():
        cur.execute(
            "SELECT date, duration_seconds FROM DailyUsage WHERE app_name = ?",
            (old_name,),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"  '{old_name}' -> '{new_name}': no entries found, skipped.")
            continue

        for date, duration in rows:
            cur.execute(
                """
                INSERT INTO DailyUsage (date, app_name, duration_seconds)
                VALUES (?, ?, ?)
                ON CONFLICT(date, app_name) DO UPDATE SET
                    duration_seconds = duration_seconds + excluded.duration_seconds
                """,
                (date, new_name, duration),
            )

        cur.execute("DELETE FROM DailyUsage WHERE app_name = ?", (old_name,))
        total = sum(r[1] for r in rows)
        print(f"  '{old_name}' -> '{new_name}': {len(rows)} Days combined "
              f"({total:.1f}s added).")

    con.commit()
    con.close()
    print("Done!")


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("usageData.db")
    if not db_path.exists():
        print(f"File not found: {db_path}")
        sys.exit(1)
    merge(db_path)
