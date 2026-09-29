import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(relpath: str) -> Any:
    path = FIXTURES_DIR / relpath
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return path.read_text(encoding="utf-8")
