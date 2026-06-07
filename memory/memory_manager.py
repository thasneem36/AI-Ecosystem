"""Persistent conversation memory stored as JSON on disk.

Each conversation is a record with an id, timestamp, the original user
message and the list of agent messages produced while solving it.
"""
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings


class MemoryManager:
    def __init__(self, memory_file: Optional[Path] = None) -> None:
        self.memory_file: Path = Path(memory_file) if memory_file else settings.MEMORY_FILE
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.memory_file.exists():
            self._write([])

    # ---------- low level ----------
    def _read(self) -> List[Dict[str, Any]]:
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, data: List[Dict[str, Any]]) -> None:
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ---------- public API ----------
    def save_conversation(
        self,
        user_message: str,
        messages: List[Dict[str, Any]],
        model: str = "ollama",
    ) -> Dict[str, Any]:
        """Append a finished conversation to memory and return the record."""
        record = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "user_message": user_message,
            "preview": user_message[:80],
            "messages": messages,
        }
        with self._lock:
            data = self._read()
            data.append(record)
            self._write(data)
        return record

    def get_history(self) -> List[Dict[str, Any]]:
        """Return all conversations, newest first."""
        data = self._read()
        return sorted(data, key=lambda r: r.get("timestamp", ""), reverse=True)

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        for record in self._read():
            if record.get("id") == conversation_id:
                return record
        return None

    def count(self) -> int:
        return len(self._read())

    def clear(self) -> None:
        with self._lock:
            self._write([])


# Shared singleton
memory_manager = MemoryManager()
