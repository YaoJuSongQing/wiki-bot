"""
Multi-wiki knowledge base. Dense embedding retrieval (sentence-transformers).
LLM reranking happens in server.py for semantic precision.
"""
import json
import re
import time
import threading
from pathlib import Path

# ── Fix Windows SSL cert errors ─────────────────────────────
try:
    import certifi
    import os as _os
    _os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

import numpy as np

DATA_DIR = Path(__file__).parent / "data"

# Shared embedding model (lazy-loaded, one per process)
_embedding_model = None
_embedding_model_name = None


def _get_embedding_model(model_name: str = "BAAI/bge-small-zh-v1.5", hf_endpoint: str = ""):
    """Lazy-load and cache the sentence-transformers model."""
    global _embedding_model, _embedding_model_name
    if _embedding_model is None or _embedding_model_name != model_name:
        import os
        # Always ensure HF_ENDPOINT is set, fallback to mirror
        os.environ["HF_ENDPOINT"] = (hf_endpoint or "https://hf-mirror.com").strip().rstrip("/")
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(model_name)
        _embedding_model_name = model_name
    return _embedding_model


class WikiKB:
    def __init__(self, slug: str, name: str = "", embedding_model: str = "BAAI/bge-small-zh-v1.5", hf_endpoint: str = ""):
        self.slug = slug
        self.name = name or slug
        self.embedding_model = embedding_model
        self.hf_endpoint = hf_endpoint
        self.pages = {}
        self.memory_entries = []  # user corrections/notes
        self.chunks = []
        self.chunk_embeddings = None  # np.ndarray (n_chunks, dim)
        self.data_file = DATA_DIR / f"{slug}.json"
        self.memory_file = DATA_DIR / f"{slug}.memory.json"
        self.index_dir = DATA_DIR / f"{slug}_index"
        self._mtime = 0

    def load(self):
        if not self.data_file.exists():
            raise FileNotFoundError(f"Data file not found: {self.data_file}")
        with open(self.data_file, "r", encoding="utf-8") as f:
            self.pages = json.load(f)
        self._mtime = self.data_file.stat().st_mtime
        self._load_memory()
        self._chunk_pages()

        # Try loading cached embeddings first
        if not self._load_embeddings():
            self._build_embeddings()
            self._save_embeddings()

        return len(self.pages), len(self.chunks)

    def is_stale(self) -> bool:
        if not self.data_file.exists():
            return False
        return self.data_file.stat().st_mtime > self._mtime

    def _load_memory(self):
        """Load user memory entries from disk."""
        self.memory_entries = []
        if self.memory_file.exists():
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    self.memory_entries = json.load(f)
            except Exception:
                self.memory_entries = []

    def _chunk_pages(self):
        self.chunks = []
        # Wiki chunks
        for page_name, text in self.pages.items():
            sections = re.split(r'\n(?=## )', text)
            for sec in sections:
                sec = sec.strip()
                if not sec or len(sec) < 30:
                    continue
                header_match = re.match(r'## (.+)', sec)
                section_title = header_match.group(1).strip() if header_match else page_name
                self.chunks.append({"page": page_name, "section": section_title, "text": sec, "source": "wiki"})

        # Memory chunks (user corrections & notes)
        for mem in self.memory_entries:
            self.chunks.append({
                "page": mem.get("page", ""),
                "section": mem.get("section", ""),
                "text": mem.get("text", ""),
                "source": "memory",
                "mem_type": mem.get("type", "note"),
                "created_at": mem.get("created_at", ""),
            })

    def add_memory(self, page: str, section: str, text: str, mem_type: str = "note") -> dict:
        """Add a user memory entry, save to disk, and rebuild embeddings."""
        import datetime
        import shutil

        entry = {
            "page": page,
            "section": section,
            "text": text,
            "type": mem_type,
            "created_at": datetime.datetime.now().isoformat(),
        }
        self.memory_entries.append(entry)

        # Save to disk
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(self.memory_entries, f, ensure_ascii=False, indent=2)

        # Re-chunk and rebuild embeddings
        self._chunk_pages()
        if self.index_dir.exists():
            shutil.rmtree(self.index_dir)
        self._build_embeddings()
        self._save_embeddings()

        return entry

    def get_memory_count(self) -> int:
        return len(self.memory_entries)

    def _build_embeddings(self):
        """Generate dense embeddings for all chunks."""
        if not self.chunks:
            return
        texts = [c["text"] for c in self.chunks]
        model = _get_embedding_model(self.embedding_model, self.hf_endpoint)
        self.chunk_embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=32,
        )

    def search(self, query: str, top_k: int = 15) -> list[dict]:
        """Dense vector similarity search."""
        if self.chunk_embeddings is None or len(self.chunk_embeddings) == 0:
            return []

        model = _get_embedding_model(self.embedding_model, self.hf_endpoint)
        query_vec = model.encode([query], normalize_embeddings=True)[0]

        # Cosine similarity (vectors are normalized, so dot product = cosine)
        scores = np.dot(self.chunk_embeddings, query_vec)

        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []
        for idx in top_indices:
            if scores[idx] < 0.25:  # minimum relevance threshold
                continue
            results.append({**self.chunks[idx], "score": float(scores[idx])})
        return results

    def list_pages(self) -> list[str]:
        return sorted(self.pages.keys())

    def _save_embeddings(self):
        """Cache embeddings to disk for fast reload."""
        if self.chunk_embeddings is None:
            return
        self.index_dir.mkdir(parents=True, exist_ok=True)
        np.save(str(self.index_dir / "embeddings.npy"), self.chunk_embeddings)
        # Save metadata so we can detect model changes
        meta = {"model": self.embedding_model, "n_chunks": len(self.chunks)}
        import json as _json
        (self.index_dir / "meta.json").write_text(_json.dumps(meta), encoding="utf-8")

    def _load_embeddings(self) -> bool:
        """Load cached embeddings from disk. Returns False if cache missing/mismatched."""
        ef = self.index_dir / "embeddings.npy"
        mf = self.index_dir / "meta.json"
        if not ef.exists() or not mf.exists():
            return False

        # Check model match
        try:
            import json as _json
            meta = _json.loads(mf.read_text(encoding="utf-8"))
            if meta.get("model") != self.embedding_model:
                return False  # Model changed, rebuild needed
            if meta.get("n_chunks") != len(self.chunks):
                return False  # Data changed
        except Exception:
            pass

        self.chunk_embeddings = np.load(str(ef))
        return True


class MultiWikiKB:
    def __init__(self, embedding_model: str = "BAAI/bge-small-zh-v1.5", hf_endpoint: str = ""):
        self.embedding_model = embedding_model
        self.hf_endpoint = hf_endpoint
        self.wikis: dict[str, WikiKB] = {}
        self.default_slug: str | None = None
        self._watcher_running = False
        self._discover()

    def _discover(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for f in sorted(DATA_DIR.glob("*.json")):
            slug = f.stem
            if slug in ("config",) or slug.endswith(".meta") or slug.endswith(".memory"):
                continue
            if slug in self.wikis:
                if self.wikis[slug].is_stale():
                    self._reload_wiki(slug)
            else:
                self._load_wiki(slug)
            if self.default_slug is None:
                self.default_slug = slug

    def _load_wiki(self, slug: str):
        name = slug.replace("_", " ").title()
        meta_file = DATA_DIR / f"{slug}.meta.json"
        if meta_file.exists():
            try:
                with open(meta_file, encoding="utf-8") as f:
                    meta = json.load(f)
                if meta.get("name"):
                    name = meta["name"]
            except:
                pass
        kb = WikiKB(slug, name=name, embedding_model=self.embedding_model, hf_endpoint=self.hf_endpoint)
        try:
            kb.load()
            self.wikis[slug] = kb
            return True
        except Exception as e:
            print(f"Failed to load wiki '{slug}': {e}")
            return False

    def _reload_wiki(self, slug: str):
        print(f"[hot-reload] {slug} changed, reloading...")
        old = self.wikis.pop(slug, None)
        if old:
            import shutil
            if old.index_dir.exists():
                shutil.rmtree(old.index_dir)
        if self._load_wiki(slug):
            print(f"[hot-reload] OK {slug}: {len(self.wikis[slug].pages)}p")

    def reload(self) -> dict:
        before = set(self.wikis.keys())
        self._discover()
        after = set(self.wikis.keys())
        added = after - before
        modified = {s for s in before & after if self.wikis[s].is_stale()}
        if modified:
            for s in modified:
                self._reload_wiki(s)
        return {"added": list(added), "modified": list(modified), "total": len(self.wikis), "wikis": self.list_wikis()}

    def start_watcher(self, interval=5.0):
        if self._watcher_running:
            return
        self._watcher_running = True

        def _w():
            while self._watcher_running:
                time.sleep(interval)
                try:
                    self._discover()
                except:
                    pass

        t = threading.Thread(target=_w, daemon=True)
        t.start()

    def stop_watcher(self):
        self._watcher_running = False

    def list_wikis(self) -> list[dict]:
        return [{"slug": s, "name": k.name, "pages": len(k.pages), "chunks": len(k.chunks)} for s, k in self.wikis.items()]

    def get(self, slug=None) -> WikiKB | None:
        if slug and slug in self.wikis:
            return self.wikis[slug]
        if self.default_slug and self.default_slug in self.wikis:
            return self.wikis[self.default_slug]
        for kb in self.wikis.values():
            return kb
        return None

    def search(self, query, slug=None, top_k=15):
        kb = self.get(slug)
        if kb is None:
            return [], ""
        return kb.search(query, top_k=top_k), kb.name

    def add_memory(self, slug: str, page: str, section: str, text: str, mem_type: str = "note") -> dict:
        """Add a memory entry to a specific wiki and re-index."""
        if slug not in self.wikis:
            raise ValueError(f"Wiki '{slug}' not found")
        return self.wikis[slug].add_memory(page, section, text, mem_type)


_mkb = None


def get_kb(embedding_model: str = "BAAI/bge-small-zh-v1.5", hf_endpoint: str = "") -> MultiWikiKB:
    global _mkb
    if _mkb is None:
        _mkb = MultiWikiKB(embedding_model=embedding_model, hf_endpoint=hf_endpoint)
        _mkb.start_watcher(5.0)
    return _mkb
