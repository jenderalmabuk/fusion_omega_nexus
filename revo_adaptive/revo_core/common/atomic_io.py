from __future__ import annotations
import json, os, tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | Path, data: Any, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=indent, sort_keys=False)
            f.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: str | Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default
