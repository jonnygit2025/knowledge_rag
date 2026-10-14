"""Restore only to a newly created, explicitly named database."""

import argparse
import re
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("backup", type=Path)
parser.add_argument("--database", required=True)
args = parser.parse_args()
if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", args.database) or args.database in (
    "knowledge",
    "postgres",
    "template0",
    "template1",
):
    parser.error("Choose a new database name, for example knowledge_restore")
if not args.backup.is_file():
    parser.error("Backup file does not exist")
prefix = ["docker", "compose", "exec", "-T", "postgres"]
# createdb fails if the destination exists; there is no drop/overwrite path.
subprocess.run(prefix + ["createdb", "-U", "knowledge", args.database], check=True)
with args.backup.open("rb") as handle:
    subprocess.run(
        prefix
        + ["pg_restore", "-U", "knowledge", "--no-owner", "--exit-on-error", "-d", args.database],
        stdin=handle,
        check=True,
    )
print(f"Restored to {args.database}; the application's current database was not changed.")
