import json
import sqlite3
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("")
    raise SystemExit(0)
path = Path(sys.argv[1])
if not path.is_file():
    print("")
    raise SystemExit(0)
con = sqlite3.connect(path)
try:
    row = con.execute(
        "select value from settings where key='auto_login'"
    ).fetchone()
finally:
    con.close()
if not row:
    print("")
    raise SystemExit(0)
try:
    value = json.loads(row[0])
except Exception:
    value = row[0]
print(value if isinstance(value, str) else "")
