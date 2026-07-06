from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


@dataclass(slots=True, frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    google_credentials_path: Path

    @classmethod
    def load(cls) -> "Settings":
        configured = os.environ.get("BIRTHOUT_DATA_DIR")
        data_dir = Path(configured) if configured else Path(user_data_path("BirthOut"))
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(data_dir, data_dir / "birthout.sqlite3", data_dir / "google-client.json")
