#!/usr/bin/env python3
"""Dump all unique handle IDs from chat.db, sorted, one per line."""

import sqlite3
from pathlib import Path

DB = Path.home() / "Library/Messages/chat.db"
con = sqlite3.connect(str(DB))
con.execute("PRAGMA query_only = ON")
cur = con.cursor()
cur.execute("SELECT DISTINCT id FROM handle ORDER BY id")
for row in cur.fetchall():
    print(row[0])
con.close()
