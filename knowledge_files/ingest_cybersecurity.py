"""
Ingest knowledge_files/cybersecurity/*.md into ChromaDB.

Uses the EXISTING ingestion pipeline from api/knowledge.py — the same
_ingest() function the /knowledge/upload endpoint calls.  No new pipeline.

Run from the project root:
    .\\venv\\Scripts\\python.exe knowledge_files\\ingest_cybersecurity.py
"""
import os
import sys
import uuid
from pathlib import Path

# Project root on sys.path so `api`, `config`, `auth` are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.knowledge import _ingest, _JOBS, _collection  # existing pipeline

FOLDER = Path(__file__).parent / "cybersecurity"

if __name__ == "__main__":
    md_files = sorted(FOLDER.glob("*.md"))
    if not md_files:
        print(f"No .md files found in {FOLDER}")
        sys.exit(1)

    print(f"Ingesting {len(md_files)} file(s) from {FOLDER}\n")

    for md_file in md_files:
        doc_name = f"cybersecurity/{md_file.stem}"
        filename  = md_file.name
        data      = md_file.read_bytes()

        jid = str(uuid.uuid4())
        _JOBS[jid] = {
            "status": "running", "steps": [], "step": "",
            "error": None, "doc_id": None, "chunk_count": 0,
            "filename": filename, "name": doc_name,
        }

        _ingest(jid, doc_name, filename, data)   # synchronous — blocks until done

        j = _JOBS[jid]
        if j["status"] == "done":
            print(f"  OK  {filename:30s}  chunks={j['chunk_count']}  doc_id={j['doc_id']}")
        else:
            print(f"  ERR {filename:30s}  {j['error']}")

    # Verify total count in ChromaDB
    try:
        total = _collection().count()
        print(f"\nTotal chunks now in ChromaDB: {total}")
    except Exception as exc:
        print(f"\n[WARN] Could not count chunks: {exc}")

    # Quick search sanity-check
    print("\nSearch sanity-check — query: 'nmap scan open ports'")
    try:
        col = _collection()
        res = col.query(
            query_texts=["nmap scan open ports"],
            n_results=min(3, col.count()),
            include=["documents", "metadatas", "distances"],
        )
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            score = round(max(0.0, 1 - float(dist)), 3)
            print(f"  [{score:.3f}] {meta.get('name','')} — {doc[:80].strip()}")
    except Exception as exc:
        print(f"  [WARN] Search check failed: {exc}")
