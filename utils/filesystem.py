from pathlib import Path
from typing import Union


def ensure_dir(path: Union[str, Path]) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_file(path: Union[str, Path], content: str) -> Path:
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")
    return path


def read_file(path: Union[str, Path]) -> str:
    path = Path(path)
    return path.read_text(encoding="utf-8")


def file_exists(path: Union[str, Path]) -> bool:
    return Path(path).exists()


def get_file_size(path: Union[str, Path]) -> int:
    return Path(path).stat().st_size