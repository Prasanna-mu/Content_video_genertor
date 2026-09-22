import json
from pathlib import Path
from typing import Any, Union
from utils.filesystem import ensure_dir


def load_json(path: Union[str, Path]) -> Any:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Union[str, Path], data: Any, indent: int = 2) -> Path:
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")
    return path


def load_json_safe(path: Union[str, Path], default: Any = None) -> Any:
    try:
        return load_json(path)
    except (FileNotFoundError, json.JSONDecodeError):
        return default