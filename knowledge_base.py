"""
Multi-wiki knowledge base. Auto-discovers wiki data files in data/ directory.
Supports multiple wikis simultaneously, each with independent TF-IDF index.
Hot reload: detects new/modified data files and reloads automatically.
"""
import json
import re
import pickle
import time
import threading
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

DATA_DIR = Path(__file__).parent / "data"


class WikiKB:
    """Knowledge base for a single wiki."""

    def __init__(self, slug: str, name: str = ""):
        self.slug = slug
        self.name = name or slug
        self.pages = {}
        self.chunks = []
        self.vectorizer = None
        self.chunk_matrix = None
        self.data_file = DATA_DIR / f"{slug}.json"
        self.index_dir = DATA_DIR / f"{slug}_index"
        self._mtime = 0  # file modification time when last loaded

    def load(self):
        if not self.data_file.exists():
            raise FileNotFoundError(f"Data file not found: {self.data_file}")
        with open(self.data_file, "r") as f:
            self.pages = json.load(f)
        self._mtime = self.data_file.stat().st_mtime
        self._chunk_pages()
        self._build_index()
        return len(self.pages), len(self.chunks)

    def is_stale(self) -> bool:
        """Check if data file has been modified since last load."""
        if not self.data_file.exists():
            return False
        return self.data_file.stat().st_mtime > self._mtime

    def _chunk_pages(self):
        self.chunks = []
        for page_name, text in self.pages.items():
            sections = re.split(r'\n(?=## )', text)
            for sec in sections:
                sec = sec.strip()
                if not sec or len(sec) < 30:
                    continue
                header_match = re.match(r'## (.+)', sec)
                section_title = header_match.group(1).strip() if header_match else page_name
                self.chunks.append({"page": page_name, "section": section_title, "text": sec})

    def _build_index(self):
        if not self.chunks:
            return
        texts = [c["text"] for c in self.chunks]
        self.vectorizer = TfidfVectorizer(max_features=5000, stop_words=None, ngram_range=(1, 2), sublinear_tf=True)
        self.chunk_matrix = self.vectorizer.fit_transform(texts)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if self.vectorizer is None or self.chunk_matrix is None:
            return []
        query_vec = self.vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self.chunk_matrix)[0]
        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []
        for idx in top_indices:
            if scores[idx] < 0.05:
                continue
            results.append({**self.chunks[idx], "score": float(scores[idx])})
        return results

    def list_pages(self) -> list[str]:
        return sorted(self.pages.keys())

    def save_index(self):
        self.index_dir.mkdir(parents=True, exist_ok=True)
        with open(self.index_dir / "vectorizer.pkl", "wb") as f:
            pickle.dump(self.vectorizer, f)
        with open(self.index_dir / "matrix.pkl", "wb") as f:
            pickle.dump(self.chunk_matrix, f)

    def load_index(self) -> bool:
        vf = self.index_dir / "vectorizer.pkl"
        mf = self.index_dir / "matrix.pkl"
        if vf.exists() and mf.exists():
            with open(vf, "rb") as f:
                self.vectorizer = pickle.load(f)
            with open(mf, "rb") as f:
                self.chunk_matrix = pickle.load(f)
            return True
        return False


class MultiWikiKB:
    """Manages multiple wikis, auto-discovers from data/ directory."""

    def __init__(self):
        self.wikis: dict[str, WikiKB] = {}
        self.default_slug: str | None = None
        self._watcher_running = False
        self._discover()

    def _discover(self):
        """Find all {slug}.json files in data/ directory."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for f in sorted(DATA_DIR.glob("*.json")):
            slug = f.stem
            if slug in ("config",):
                continue
            if slug in self.wikis:
                # Already loaded — check if stale
                if self.wikis[slug].is_stale():
                    self._reload_wiki(slug)
            else:
                # New wiki
                self._load_wiki(slug)
            if self.default_slug is None:
                self.default_slug = slug

    def _load_wiki(self, slug: str):
        """Load a single wiki by slug."""
        name = slug.replace("_", " ").title()
        kb = WikiKB(slug, name=name)
        try:
            if not kb.load_index():
                kb.load()
                kb.save_index()
            else:
                kb.load()
            self.wikis[slug] = kb
            return True
        except Exception as e:
            print(f"Failed to load wiki '{slug}': {e}")
            return False

    def _reload_wiki(self, slug: str):
        """Reload a modified wiki."""
        print(f"[hot-reload] {slug} changed, reloading...")
        old = self.wikis.pop(slug, None)
        # Clear old index
        if old:
            import shutil
            if old.index_dir.exists():
                shutil.rmtree(old.index_dir)
        if self._load_wiki(slug):
            kb = self.wikis[slug]
            print(f"[hot-reload] ✓ {slug}: {len(kb.pages)} pages, {len(kb.chunks)} chunks")
        else:
            # Restore old if reload failed
            if old:
                self.wikis[slug] = old

    def reload(self) -> dict:
        """Check for new/modified files and reload. Returns changes."""
        before = set(self.wikis.keys())
        self._discover()
        after = set(self.wikis.keys())
        added = after - before
        modified = {s for s in before & after if self.wikis[s].is_stale()}
        if modified:
            for s in modified:
                self._reload_wiki(s)
        return {
            "added": list(added),
            "modified": list(modified),
            "total": len(self.wikis),
            "wikis": self.list_wikis(),
        }

    def start_watcher(self, interval: float = 5.0):
        """Start background thread that polls for file changes."""
        if self._watcher_running:
            return
        self._watcher_running = True

        def _watch():
            while self._watcher_running:
                time.sleep(interval)
                try:
                    self._discover()
                except Exception:
                    pass

        t = threading.Thread(target=_watch, daemon=True)
        t.start()

    def stop_watcher(self):
        self._watcher_running = False

    def list_wikis(self) -> list[dict]:
        return [
            {"slug": slug, "name": kb.name, "pages": len(kb.pages), "chunks": len(kb.chunks)}
            for slug, kb in self.wikis.items()
        ]

    def get(self, slug: str | None = None) -> WikiKB | None:
        if slug and slug in self.wikis:
            return self.wikis[slug]
        if self.default_slug and self.default_slug in self.wikis:
            return self.wikis[self.default_slug]
        for kb in self.wikis.values():
            return kb
        return None

    def search(self, query: str, slug: str | None = None, top_k: int = 5) -> tuple[list[dict], str]:
        kb = self.get(slug)
        if kb is None:
            return [], ""
        return kb.search(query, top_k=top_k), kb.name


# Singleton
_mkb: MultiWikiKB | None = None


def get_kb() -> MultiWikiKB:
    global _mkb
    if _mkb is None:
        _mkb = MultiWikiKB()
        _mkb.start_watcher(interval=5.0)
    return _mkb
