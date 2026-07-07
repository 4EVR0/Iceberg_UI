from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENV_FILES = (".env", "local.env")


def load_dotenv(path: str | None = None) -> None:
    paths = (path,) if path else DEFAULT_ENV_FILES
    for candidate in paths:
        env_path = Path(candidate)
        if not env_path.exists():
            continue

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue

            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            os.environ.setdefault(key, value)

        break
