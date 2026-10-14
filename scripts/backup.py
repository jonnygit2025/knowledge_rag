"""Binary-safe PostgreSQL backup on Windows, macOS and Linux."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

directory = Path("backups")
directory.mkdir(exist_ok=True)
target = directory / ("knowledge_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".dump")
temporary = target.with_suffix(".tmp")
try:
    with temporary.open("wb") as handle:
        subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "knowledge",
                "-d",
                "knowledge",
                "-Fc",
            ],
            stdout=handle,
            check=True,
        )
    temporary.replace(target)
finally:
    temporary.unlink(missing_ok=True)
print(target)
