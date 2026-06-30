"""Knowledge ingestion system — admin-only endpoints.

Upload PDF / DOCX / TXT  →  chunk (~500 words)  →  store in ChromaDB
→  browse tree  +  semantic search-test.

No RAG answering here — storage and retrieval proof only.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from auth.auth_manager import auth_manager
from config.settings import settings

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
_sec = HTTPBearer(auto_error=False)


# ── Admin guard (mirrors server.py's require_admin without circular import) ───
def _admin(creds: Optional[HTTPAuthorizationCredentials] = Depends(_sec)):
    if not auth_manager.has_accounts():
        return None          # bootstrap mode — allow everything
    if not creds:
        raise HTTPException(401, "Not authenticated")
    acc = auth_manager.verify_session(creds.credentials)
    if not acc:
        raise HTTPException(401, "Invalid or expired session")
    if acc.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return acc


# ── ChromaDB — lazy init so server starts even if package is missing ──────────
_lock   = threading.Lock()
_client = None
_col    = None


def _collection():
    global _client, _col
    with _lock:
        if _col is not None:
            return _col
        try:
            import chromadb  # noqa: F401
        except ImportError:
            raise RuntimeError("chromadb not installed — run: pip install chromadb")
        db_path = str(settings.BASE_DIR / "memory" / "chroma_db")
        Path(db_path).mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=db_path)
        _col = _client.get_or_create_collection(
            "koottam_knowledge",
            metadata={"hnsw:space": "cosine"},
        )
        return _col


def _warm_chromadb() -> None:
    """Pre-load ChromaDB on server start so first upload is fast."""
    try:
        _collection()
    except Exception:
        pass


threading.Thread(target=_warm_chromadb, daemon=True).start()


# ── SQLite metadata (knowledge_items) ─────────────────────────────────────────
_KDB = settings.BASE_DIR / "memory" / "knowledge.db"


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(str(_KDB))
    c.row_factory = sqlite3.Row
    return c


with _db() as _c:
    _c.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_items (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            filename    TEXT NOT NULL,
            file_type   TEXT DEFAULT 'txt',
            chunk_count INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _c.commit()


# ── Text extraction ───────────────────────────────────────────────────────────
def _read_file(data: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    if ext == "pdf":
        try:
            import io
            import pypdf  # noqa: F401
            return "\n\n".join(
                p.extract_text() or ""
                for p in pypdf.PdfReader(io.BytesIO(data)).pages
            )
        except ImportError:
            raise RuntimeError("pypdf not installed — run: pip install pypdf")
    if ext in ("docx", "doc"):
        try:
            import io
            import docx  # noqa: F401
            return "\n".join(
                p.text
                for p in docx.Document(io.BytesIO(data)).paragraphs
                if p.text.strip()
            )
        except ImportError:
            raise RuntimeError(
                "python-docx not installed — run: pip install python-docx"
            )
    return data.decode("utf-8", errors="replace")


def _chunk(text: str, n: int = 500) -> List[str]:
    words = text.split()
    return [
        " ".join(words[i : i + n]).strip()
        for i in range(0, len(words), n)
        if " ".join(words[i : i + n]).strip()
    ]


# ── In-process job tracker ────────────────────────────────────────────────────
_JOBS: Dict[str, Dict[str, Any]] = {}


def _ingest(jid: str, name: str, fn: str, data: bytes) -> None:
    j = _JOBS[jid]

    def push(label: str) -> None:
        j["step"] = label
        j["steps"].append({"label": label, "done": False})

    def tick() -> None:
        if j["steps"]:
            j["steps"][-1]["done"] = True

    try:
        push("reading")
        text = _read_file(data, fn)
        tick()

        push("chunking")
        chunks = _chunk(text)
        if not chunks:
            raise ValueError("No readable text found in file")
        tick()

        push("storing")
        did   = str(uuid.uuid4())
        col   = _collection()
        ids   = [f"{did}_{i}" for i in range(len(chunks))]
        metas = [
            {"doc_id": did, "name": name, "filename": fn, "chunk_index": i}
            for i in range(len(chunks))
        ]
        BATCH = 500
        for s in range(0, len(chunks), BATCH):
            col.add(
                documents=chunks[s : s + BATCH],
                ids=ids[s : s + BATCH],
                metadatas=metas[s : s + BATCH],
            )
        tick()

        ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else "txt"
        with _db() as c:
            c.execute(
                "INSERT INTO knowledge_items(id,name,filename,file_type,chunk_count)"
                " VALUES(?,?,?,?,?)",
                (did, name, fn, ext, len(chunks)),
            )
            c.commit()

        j.update(status="done", doc_id=did, chunk_count=len(chunks))

    except Exception as exc:
        j.update(status="error", error=str(exc))
        if j["steps"] and not j["steps"][-1]["done"]:
            j["steps"][-1]["error"] = True


# ── Schemas ───────────────────────────────────────────────────────────────────
class SearchReq(BaseModel):
    query: str
    n: int = 3


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    name: str = Form(...),
    _: Optional[Dict] = Depends(_admin),
) -> Dict[str, Any]:
    """Receive a file and kick off background ingestion. Returns job_id."""
    fn  = file.filename or "upload.txt"
    ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else "txt"
    if ext not in {"pdf", "docx", "doc", "txt"}:
        raise HTTPException(400, f"Unsupported type .{ext}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")

    jid = str(uuid.uuid4())
    _JOBS[jid] = {
        "status": "running", "steps": [], "step": "",
        "error": None, "doc_id": None, "chunk_count": 0,
        "filename": fn, "name": name,
    }
    threading.Thread(target=_ingest, args=(jid, name, fn, data), daemon=True).start()
    return {"job_id": jid, "filename": fn, "name": name}


@router.get("/job/{jid}")
def poll_job(jid: str, _: Optional[Dict] = Depends(_admin)) -> Dict[str, Any]:
    j = _JOBS.get(jid)
    if not j:
        raise HTTPException(404, "Job not found")
    return {**j, "job_id": jid}


@router.get("/list")
def list_items(_: Optional[Dict] = Depends(_admin)) -> Dict[str, Any]:
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM knowledge_items ORDER BY created_at DESC"
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/stats")
def stats(_: Optional[Dict] = Depends(_admin)) -> Dict[str, Any]:
    with _db() as c:
        docs = c.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
    try:
        chunks = _collection().count()
    except Exception:
        chunks = 0
    return {"doc_count": docs, "total_chunks": chunks}


@router.get("/tree")
def tree(_: Optional[Dict] = Depends(_admin)) -> Dict[str, Any]:
    """Each item with its first 12 chunk previews for the visual tree."""
    with _db() as c:
        items = [
            dict(r)
            for r in c.execute(
                "SELECT id,name,filename,file_type,chunk_count"
                " FROM knowledge_items ORDER BY created_at ASC"
            ).fetchall()
        ]
    try:
        col = _collection()
        for it in items:
            res = col.get(
                where={"doc_id": it["id"]},
                include=["documents"],
                limit=12,
            )
            it["chunk_previews"] = [
                (d or "")[:80].strip() for d in (res.get("documents") or [])
            ]
    except Exception:
        for it in items:
            it["chunk_previews"] = []
    return {"tree": items}


@router.post("/search")
def search(req: SearchReq, _: Optional[Dict] = Depends(_admin)) -> Dict[str, Any]:
    if not req.query.strip():
        raise HTTPException(400, "Empty query")
    try:
        col   = _collection()
        total = col.count()
        if total == 0:
            return {"results": [], "query": req.query, "total_chunks": 0}
        res = col.query(
            query_texts=[req.query],
            n_results=min(req.n, total),
            include=["documents", "metadatas", "distances"],
        )
        return {
            "results": [
                {
                    "text": d,
                    "source_name": m.get("name", ""),
                    "source_file": m.get("filename", ""),
                    "chunk_index": m.get("chunk_index", 0),
                    "score": round(max(0.0, 1 - float(dist)), 3),
                }
                for d, m, dist in zip(
                    res["documents"][0],
                    res["metadatas"][0],
                    res["distances"][0],
                )
            ],
            "query": req.query,
            "total_chunks": total,
        }
    except Exception as exc:
        raise HTTPException(500, f"Search failed: {exc}")


@router.delete("/{doc_id}")
def delete_item(
    doc_id: str, _: Optional[Dict] = Depends(_admin)
) -> Dict[str, Any]:
    with _db() as c:
        row = c.execute(
            "SELECT chunk_count FROM knowledge_items WHERE id=?", (doc_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Not found")

    # Best-effort vector deletion — don't block SQLite cleanup on failure
    try:
        col = _collection()
        ids = [f"{doc_id}_{i}" for i in range(row["chunk_count"])]
        for s in range(0, len(ids), 500):
            col.delete(ids=ids[s : s + 500])
    except Exception:
        pass

    with _db() as c:
        c.execute("DELETE FROM knowledge_items WHERE id=?", (doc_id,))
        c.commit()

    return {"ok": True, "deleted_chunks": row["chunk_count"]}
