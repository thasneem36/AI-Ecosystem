"""Manage files created by the agents inside the output/ directory."""
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings

OUTPUT_DIR: Path = settings.OUTPUT_DIR


def _safe_path(filename: str) -> Path:
    """Resolve a filename strictly inside OUTPUT_DIR (prevents traversal)."""
    name = os.path.basename(filename.strip())
    if not name:
        raise ValueError("Invalid filename")
    return OUTPUT_DIR / name


def save_file(filename: str, content: str) -> Dict[str, Any]:
    """Write text content to output/<filename> and return its metadata."""
    path = _safe_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return file_info(path.name)


def read_file(filename: str) -> Optional[str]:
    path = _safe_path(filename)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def list_files() -> List[Dict[str, Any]]:
    """Return metadata for every file in the output directory."""
    if not OUTPUT_DIR.exists():
        return []
    files = []
    for entry in sorted(OUTPUT_DIR.iterdir()):
        if entry.is_file():
            files.append(file_info(entry.name))
    return files


def file_info(filename: str) -> Dict[str, Any]:
    path = _safe_path(filename)
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "extension": path.suffix.lstrip("."),
    }


def delete_file(filename: str) -> bool:
    path = _safe_path(filename)
    if path.exists():
        path.unlink()
        return True
    return False


def _human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
