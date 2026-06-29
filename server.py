"""
Wiki Q&A Bot — multi-wiki RAG server.
Auto-discovers wikis from data/*.json, switchable via API and web UI.
"""
import os
import sys

# ── Fix Windows SSL cert errors ─────────────────────────────
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass
import re
from pathlib import Path
import yaml

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx

from knowledge_base import get_kb
from scrape_wiki import scrape_mediawiki, scrape_sphinx, scrape_generic
import ssl
import urllib.request

# ── Load config ──────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.yaml"
config = {}
if CONFIG_PATH.exists():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

wiki_cfg = config.get("wiki", {})
llm_cfg = config.get("llm", {})
srv_cfg = config.get("server", {})
emb_cfg = config.get("embedding", {})

API_KEY = llm_cfg.get("api_key") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
API_BASE = llm_cfg.get("base_url") or "https://api.deepseek.com/v1"
MODEL = llm_cfg.get("model") or "deepseek-v4-flash"
EMBEDDING_MODEL = emb_cfg.get("model", "BAAI/bge-small-zh-v1.5")
HF_ENDPOINT = (emb_cfg.get("hf_endpoint", "") or "https://hf-mirror.com").strip().rstrip("/")
HOST = srv_cfg.get("host", "0.0.0.0")
PORT = int(srv_cfg.get("port", 8080))

# ── Wiki URL map (slug → url) for empty-wiki fallback ─────────
_wiki_urls: dict[str, str] = {}
for _w in config.get("wikis", []) or []:
    _s = _w.get("slug", "") or re.sub(r'[^a-z0-9]+', '_', _w.get("name", "").lower().strip()).strip('_')
    _u = _w.get("url", "")
    if _s and _u:
        _wiki_urls[_s] = _u.rstrip("/")

# ── App ──────────────────────────────────────────────────────
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    mkb = get_kb(embedding_model=EMBEDDING_MODEL, hf_endpoint=HF_ENDPOINT)
    # Warm up: trigger model load so first request isn't slow
    try:
        mkb.search("warmup", top_k=1)
    except:
        pass
    print("\n" + "="*50)
    print("  WikiBot ready! Open http://localhost:8080")
    print("="*50 + "\n")
    yield


app = FastAPI(title="Wiki Q&A Bot", version="3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ───────────────────────────────────────────────────
class QuestionRequest(BaseModel):
    question: str
    wiki: str | None = None   # wiki slug, e.g. "isaac"
    top_k: int = 8
    history: list[dict] = []  # [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]

class RelevantChunk(BaseModel):
    page: str
    section: str
    text: str
    score: float

class AnswerResponse(BaseModel):
    question: str
    wiki: str
    answer: str
    sources: list[RelevantChunk]


class ScrapeRequest(BaseModel):
    url: str
    type: str   # "mediawiki", "sphinx", or "generic"
    name: str
    slug: str | None = None


# ── Endpoints ────────────────────────────────────────────────
@app.get("/health")
async def health():
    mkb = get_kb()
    wikis = mkb.list_wikis()
    return {"status": "ok", "model": MODEL, "wikis": wikis}


@app.get("/wikis")
async def list_wikis():
    return {"wikis": get_kb().list_wikis()}


@app.get("/pages")
async def list_pages(wiki: str | None = Query(None)):
    mkb = get_kb()
    kb = mkb.get(wiki)
    if not kb:
        return {"error": "Wiki not found", "available": mkb.list_wikis()}
    return {"wiki": kb.name, "pages": kb.list_pages()}


@app.post("/search")
async def search(request: QuestionRequest):
    mkb = get_kb()
    results, wiki_name = mkb.search(request.question, slug=request.wiki, top_k=request.top_k)
    return {"question": request.question, "wiki": wiki_name, "results": results}


@app.post("/reload")
async def reload_wikis():
    """Hot reload: check data/ for new or modified wiki files."""
    mkb = get_kb()
    changes = mkb.reload()
    return {"ok": True, "changes": changes}


@app.post("/update")
async def update_wiki():
    """Trigger wiki re-scrape from config.yaml, then reload."""
    import subprocess
    result = subprocess.run(
        ["python3", str(Path(__file__).parent / "scrape_wiki.py")],
        capture_output=True, text=True, timeout=300,
        cwd=str(Path(__file__).parent),
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or result.stdout.strip()}

    # Reload after scrape
    mkb = get_kb()
    changes = mkb.reload()
    return {"ok": True, "output": result.stdout.strip()[-500:], "changes": changes}


class MemoryRequest(BaseModel):
    wiki: str
    page: str = ""
    section: str = ""
    text: str
    mem_type: str = "note"  # "note" or "correction"


@app.post("/scrape")
async def scrape_wiki_endpoint(request: ScrapeRequest):
    """Add a new wiki: scrape it, save JSON, and reload KB."""
    import json as json_module

    wiki_type = request.type.strip().lower()
    wiki_url = request.url.strip().rstrip("/")
    wiki_name = request.name.strip()

    if not wiki_url or not wiki_name:
        return {"ok": False, "error": "url and name are required"}

    # Auto-generate slug from name
    slug = (request.slug or "").strip()
    if not slug:
        slug = re.sub(r'[^a-z0-9]+', '_', wiki_name.lower().strip()).strip('_')

    ctx = ssl.create_default_context()

    try:
        if wiki_type == "mediawiki":
            # Auto-detect API URL
            api_url = ""
            for suffix in ["/api.php", "/w/api.php"]:
                test_url = wiki_url + suffix
                try:
                    req = urllib.request.Request(test_url, headers={"User-Agent": "WikiBot/3.0"})
                    urllib.request.urlopen(req, context=ctx, timeout=10)
                    api_url = test_url
                    break
                except Exception:
                    continue
            if not api_url:
                return {"ok": False, "error": f"Could not auto-detect MediaWiki API for {wiki_url}. Make sure it's a valid Fandom/Wikipedia URL."}
            results = scrape_mediawiki(api_url, wiki_url)

        elif wiki_type == "sphinx":
            results = scrape_sphinx(wiki_url)

        elif wiki_type == "generic":
            results = scrape_generic([wiki_url])

        else:
            return {"ok": False, "error": f"Unknown wiki type: {wiki_type}. Use mediawiki, sphinx, or generic."}

    except Exception as e:
        return {"ok": False, "error": f"Scrape failed: {e}"}

    if not results:
        return {"ok": False, "error": "No pages scraped. Check the URL and wiki type."}

    # Save data/{slug}.json
    output = Path(__file__).parent / "data" / f"{slug}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json_module.dump(results, f, ensure_ascii=False)

    # Save data/{slug}.meta.json
    meta_path = Path(__file__).parent / "data" / f"{slug}.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json_module.dump({"name": wiki_name}, f, ensure_ascii=False)

    total_chars = sum(len(v) for v in results.values())

    # Hot reload
    mkb = get_kb()
    changes = mkb.reload()

    return {
        "ok": True,
        "slug": slug,
        "name": wiki_name,
        "pages": len(results),
        "total_chars": total_chars,
        "changes": changes,
    }


@app.post("/memory")
async def save_memory(request: MemoryRequest):
    """Save a user memory entry (note or correction) to a wiki."""
    mkb = get_kb()
    try:
        if not request.text.strip():
            return {"ok": False, "error": "text is required"}
        entry = mkb.add_memory(
            request.wiki, request.page, request.section,
            request.text.strip(), request.mem_type
        )
        return {
            "ok": True,
            "wiki": request.wiki,
            "memory_id": len(mkb.wikis[request.wiki].memory_entries),
            "entry": entry,
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.get("/info")
async def system_info():
    """System info: which APIs loaded, costs, etc."""
    mkb = get_kb()
    wikis = mkb.list_wikis()
    # Add memory counts
    for w in wikis:
        kb = mkb.wikis.get(w["slug"])
        if kb:
            w["memory"] = kb.get_memory_count()
    return {
        "api_provider": "deepseek" if "deepseek" in API_BASE else "openai",
        "model": MODEL,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_type": "dense (sentence-transformers)",
        "cost_estimate": "≈¥0.008/question (DeepSeek: ¥1/M tokens, embedding: free/local)",
        "hot_reload": "enabled (5s polling, or POST /reload)",
        "wikis": wikis,
    }


# ── Endpoints ────────────────────────────────────────────────
@app.post("/ask", response_model=AnswerResponse)
async def ask(request: QuestionRequest):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="No API key configured. Set api_key in config.yaml.")

    mkb = get_kb()
    kb = mkb.get(request.wiki)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"Wiki not found. Available: {[w['slug'] for w in mkb.list_wikis()]}")

    # ── Built-in meta answers (no RAG needed) ────────────────
    q = request.question.strip().lower()
    wiki_list = "\n".join(f"  • {w['name']}（{w['pages']}页）" for w in mkb.list_wikis())

    meta_patterns = [
        (["什么wiki", "谁的wiki", "哪个wiki", "加载了", "现在是什么", "这是什么", "当前wiki", "是什么wiki", "哪个文档"],
         f"当前加载的是 **{kb.name}**（{len(kb.pages)} 页，{len(kb.chunks)} 个知识点块）。\n\n所有可用 wiki：\n{wiki_list}\n\n网页顶部下拉框可以切换。"),
        (["你能做什么", "怎么用", "帮助", "help", "功能"],
         f"我是 Wiki Q&A 机器人。把 wiki 文档吃进去，你用自然语言提问，我检索相关内容并生成答案。\n\n当前加载 {len(mkb.wikis)} 个 wiki，共 {sum(w['pages'] for w in mkb.list_wikis())} 页。\n\n用法：输入问题 → 回车。中英文都行。"),
        (["有多少", "几个wiki", "多少页", "统计"],
         f"当前共 {len(mkb.wikis)} 个 wiki：\n{wiki_list}\n\n合计 {sum(w['pages'] for w in mkb.list_wikis())} 页。"),
    ]

    for triggers, answer in meta_patterns:
        if any(t in q for t in triggers):
            return AnswerResponse(question=request.question, wiki=kb.name, answer=answer, sources=[])

    # ── Memory commands: "记住:/修正:" prefix ──────────────────
    mem_match = re.match(r'^(记住|记下|记录|修正)[：:]\\s*(.+)', request.question.strip(), re.DOTALL)
    if mem_match:
        cmd_type = "correction" if mem_match.group(1) == "修正" else "note"
        content = mem_match.group(2).strip()
        if content:
            try:
                entry = mkb.add_memory(request.wiki or mkb.default_slug or "", "", "", content, cmd_type)
                prefix = "修正" if cmd_type == "correction" else "记忆"
                return AnswerResponse(
                    question=request.question,
                    wiki=kb.name,
                    answer=f"✅ 已{prefix}：{content}\n\n（{kb.name} 的记忆条目总数：{kb.get_memory_count()}）",
                    sources=[]
                )
            except Exception as e:
                return AnswerResponse(
                    question=request.question,
                    wiki=kb.name,
                    answer=f"❌ 保存失败：{e}",
                    sources=[]
                )

    # ── Query expansion: generate keyword variations for better retrieval ──
    queries = [request.question]
    if API_KEY and len(request.question) > 3:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                qe_resp = await client.post(
                    f"{API_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    json={"model": MODEL, "messages": [
                        {"role": "system", "content": (
                            "You are a search query optimizer for a wiki knowledge base. "
                            "Given a user question, output 3-5 keyword search queries (one per line) "
                            "that would retrieve the most relevant wiki pages. "
                            "Use specific item names, game mechanics terms, and wiki-style phrasing. "
                            "Do NOT answer the question — only output search queries."
                        )},
                        {"role": "user", "content": request.question}
                    ], "temperature": 0, "max_tokens": 150},
                )
            if qe_resp.status_code == 200:
                expanded = qe_resp.json()["choices"][0]["message"]["content"].strip()
                for line in expanded.split("\n"):
                    line = line.strip().lstrip("0123456789.-•* ").strip()
                    if line and line not in queries and len(line) > 2:
                        queries.append(line)
        except Exception:
            pass

    # Search with all queries, merge + deduplicate
    all_chunks = []
    seen_keys = set()
    for q in queries[:5]:
        for c in kb.search(q, top_k=10):
            key = c["page"] + c["section"]
            if key not in seen_keys:
                seen_keys.add(key)
                all_chunks.append(c)
    chunks = all_chunks[:30]  # Get more candidates for rerank

    # Chinese/CJK: translate and merge results
    has_cjk = bool(re.search(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', request.question))
    if has_cjk and API_KEY:
        async with httpx.AsyncClient(timeout=30.0) as client:
            tr_resp = await client.post(
                f"{API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": [
                    {"role": "system", "content": "Translate to English. Keep proper nouns in original form. Output ONLY translation."},
                    {"role": "user", "content": request.question}
                ], "temperature": 0, "max_tokens": 100},
            )
        if tr_resp.status_code == 200:
            en_query = tr_resp.json()["choices"][0]["message"]["content"].strip()
            en_chunks = kb.search(en_query, top_k=15)
            seen = set()
            merged = []
            for c in en_chunks + chunks:
                key = c["page"] + c["section"]
                if key not in seen:
                    seen.add(key)
                    merged.append(c)
            chunks = merged[:25]

    if not chunks:
        # Still ask LLM with web search — it can search itself
        pass  # fall through to LLM with enable_search

    # ── LLM Rerank: pick the top 5 most relevant chunks ─────
    if len(chunks) > 5 and API_KEY:
        candidates_text = "\n\n".join(
            f"[{i}] Page: {c['page']}\n{c['text'][:300]}"
            for i, c in enumerate(chunks)
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            rr_resp = await client.post(
                f"{API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": [
                    {"role": "system", "content": "Pick the 5 most relevant chunks for answering the question. Output ONLY the indices separated by commas, e.g.: 3,7,1,12,5"},
                    {"role": "user", "content": f"Question: {request.question}\n\nCandidates:\n{candidates_text}"}
                ], "temperature": 0, "max_tokens": 50},
            )
        if rr_resp.status_code == 200:
            try:
                indices = [int(x.strip()) for x in rr_resp.json()["choices"][0]["message"]["content"].strip().split(",") if x.strip().isdigit()]
                chunks = [chunks[i] for i in indices if 0 <= i < len(chunks)][:5]
            except:
                chunks = chunks[:5]  # fallback
    else:
        chunks = chunks[:5]

    context_parts, seen = [], set()
    for c in chunks:
        key = (c["page"], c["section"])
        if key in seen: continue
        seen.add(key)
        if c.get("source") == "memory":
            tag = "用户修正" if c.get("mem_type") == "correction" else "用户记忆"
            context_parts.append(f"--- [{tag}] {c['page']} | {c['section']} ---\n{c['text']}")
        else:
            context_parts.append(f"--- {c['page']} | {c['section']} ---\n{c['text']}")

    # ── System prompt: if wiki is empty, guide LLM to search its website ──
    wiki_url_hint = ""
    if len(kb.pages) == 0:
        wiki_url = _wiki_urls.get(request.wiki or "", config.get("wiki", {}).get("url", "").rstrip("/"))
        if wiki_url:
            wiki_url_hint = (
                f" IMPORTANT: {kb.name}'s local data is currently empty. "
                f"You MUST use web search to answer. "
                f"Try searching the wiki directly: site:{wiki_url} — this is the official wiki website. "
                f"Another option: search \"{kb.name} wiki\" or specific topic + \"wiki\". "
            )

    sp = (
        f"You are a knowledgeable Q&A bot for {kb.name}. "
        "First, answer using the wiki context provided below. "
        "If the wiki context doesn't fully answer the question, use web search to supplement your knowledge. "
        + wiki_url_hint +
        "CRITICAL: YOU MUST respond in the SAME LANGUAGE as the user's question. "
        "If the user asks in Chinese, answer in Chinese. If in English, answer in English. "
        "When using information from wiki context, cite the source page inline like 【来源：Page Name】. "
        "Context entries marked [用户记忆] or [用户修正] are user-submitted content — "
        "prioritize them over wiki content when they conflict, and cite them as 【用户记忆：Page Name】 or 【用户修正：Page Name】. "
        "Include specific facts, stats, or steps. Be thorough and helpful."
    )
    user_prompt = f"Context:\n\n" + "\n\n".join(context_parts) if context_parts else "Context: (No wiki results found — answer with web search)\n\n" + f"Question: {request.question}\n\nAnswer (must be in the same language as the question):"

    messages = [{"role": "system", "content": sp}] + request.history + [{"role": "user", "content": user_prompt}]

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": messages, "temperature": 0.3, "max_tokens": 2000, "enable_search": True},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"LLM API error: {resp.status_code}")

    answer = resp.json()["choices"][0]["message"]["content"].strip()
    sources = [RelevantChunk(page=c["page"], section=c["section"], text=c["text"][:500], score=c["score"]) for c in chunks]

    return AnswerResponse(question=request.question, wiki=kb.name, answer=answer, sources=sources)


# ── Web UI ───────────────────────────────────────────────────
def _render_ui():
    mkb = get_kb()
    wikis = mkb.list_wikis()
    wiki_opts = "\n".join(
        f'<option value="{w["slug"]}" {"selected" if i == 0 else ""}>{w["name"]}'
        f' ({w["pages"]}页'
        f'{f", 记忆x{mkb.wikis[w["slug"]].get_memory_count()}" if mkb.wikis.get(w["slug"]) and mkb.wikis[w["slug"]].get_memory_count() > 0 else ""}'
        f')</option>'
        for i, w in enumerate(wikis)
    )
    first_name = wikis[0]["name"] if wikis else "Wiki"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wiki Q&A Bot</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,'Segoe UI',sans-serif;background:#1a1a2e;color:#e0e0e0;min-height:100vh;display:flex;flex-direction:column}}
.header{{background:#16213e;padding:14px 20px;border-bottom:1px solid #0f3460;display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.header h1{{font-size:18px;color:#e94560;white-space:nowrap}}
.header select{{padding:6px 10px;border-radius:6px;border:1px solid #0f3460;background:#1a1a2e;color:#e0e0e0;font-size:13px;cursor:pointer}}
.header .hint{{font-size:12px;color:#666;margin-left:auto}}
.header button.add-btn{{padding:6px 12px;border-radius:6px;border:1px dashed #e94560;background:transparent;color:#e94560;font-size:12px;cursor:pointer;white-space:nowrap}}
.header button.add-btn:hover{{background:#e94560;color:#fff}}
.chat{{flex:1;max-width:800px;width:100%;margin:0 auto;padding:20px;display:flex;flex-direction:column}}
.messages{{flex:1;overflow-y:auto;margin-bottom:16px}}
.msg{{margin-bottom:14px;padding:10px 14px;border-radius:8px;max-width:85%;line-height:1.6;font-size:14px}}
.msg.user{{background:#0f3460;align-self:flex-end;margin-left:auto}}
.msg.bot{{background:#16213e;border-left:3px solid #e94560}}
.msg.bot pre{{background:#0a0a1a;padding:8px;border-radius:4px;overflow-x:auto;margin:6px 0;font-size:13px}}
.msg.bot code{{background:#0a0a1a;padding:1px 4px;border-radius:3px;font-size:13px}}
.msg .src{{font-size:11px;color:#666;margin-top:6px}}
.input-area{{display:flex;gap:8px}}
.input-area input{{flex:1;padding:12px;border-radius:8px;border:1px solid #0f3460;background:#16213e;color:#e0e0e0;font-size:14px;outline:none}}
.input-area input:focus{{border-color:#e94560}}
.input-area button{{padding:12px 20px;border-radius:8px;border:none;background:#e94560;color:white;font-size:14px;cursor:pointer;font-weight:600}}
.input-area button:hover{{background:#c73852}}
.input-area button:disabled{{opacity:.5;cursor:not-allowed}}
.loading{{display:inline-block;width:8px;height:8px;border-radius:50%;background:#e94560;animation:blink 1s infinite;margin-left:6px}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.scrape-form{{display:none;background:#16213e;border:1px solid #0f3460;border-radius:8px;padding:16px;margin:10px 20px 0 20px;max-width:800px;width:calc(100%-40px);align-self:center}}
.scrape-form.show{{display:block}}
.scrape-form h3{{font-size:14px;color:#e94560;margin-bottom:10px}}
.scrape-form .row{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}}
.scrape-form input,.scrape-form select{{padding:8px 12px;border-radius:6px;border:1px solid #0f3460;background:#1a1a2e;color:#e0e0e0;font-size:13px;flex:1;min-width:120px}}
.scrape-form input:focus,.scrape-form select:focus{{border-color:#e94560;outline:none}}
.scrape-form button{{padding:8px 20px;border-radius:6px;border:none;background:#e94560;color:white;font-size:13px;cursor:pointer;font-weight:600}}
.scrape-form button:hover{{background:#c73852}}
.scrape-form button.cancel{{background:transparent;border:1px solid #666;color:#999;margin-left:8px}}
.scrape-form button.cancel:hover{{color:#fff;border-color:#999}}
.scrape-form .status{{font-size:12px;color:#4caf50;margin-top:6px;display:none}}
.scrape-form .status.error{{color:#e94560}}
</style>
</head>
<body>
<div class="header">
  <h1>📚 Wiki Q&A Bot</h1>
  <select id="wikiSelect" onchange="switchWiki()">{wiki_opts}</select>
  <button class="add-btn" onclick="toggleScrapeForm()">+ 添加Wiki</button>
  <span class="hint">按 Enter 发送</span>
</div>
<div class="scrape-form" id="scrapeForm">
  <h3>添加新 Wiki</h3>
  <div class="row">
    <input id="scrapeUrl" placeholder="Wiki URL (例: https://mysite.fandom.com/wiki/)" style="flex:2">
    <select id="scrapeType">
      <option value="mediawiki">MediaWiki (Fandom/维基百科)</option>
      <option value="sphinx">Sphinx / ReadTheDocs</option>
      <option value="generic">通用 (单页面)</option>
    </select>
  </div>
  <div class="row">
    <input id="scrapeName" placeholder="显示名称 (例: 我的Wiki)">
    <input id="scrapeSlug" placeholder="Slug (自动生成，可不填)">
  </div>
  <button onclick="scrapeWiki()">开始爬取</button>
  <button class="cancel" onclick="toggleScrapeForm()">取消</button>
  <div class="status" id="scrapeStatus"></div>
</div>
<div class="chat">
  <div class="messages" id="messages">
    <div class="msg bot">你好！已加载 <b>{first_name}</b>。选择一个 wiki，输入问题即可查询。<br>点击 <b>+ 添加Wiki</b> 爬取新 wiki。<br>输入 <b>记住：xxx</b> 或 <b>修正：xxx</b> 来保存你的知识。</div>
  </div>
  <div class="input-area">
    <input id="question" placeholder="输入你的问题…" onkeydown="if(event.key==='Enter')ask()">
    <button id="sendBtn" onclick="ask()">发送</button>
  </div>
</div>
<script>
let currentWiki = '{wikis[0]["slug"] if wikis else ""}';
function switchWiki() {{ currentWiki = document.getElementById('wikiSelect').value; chatHistory = []; document.getElementById('messages').innerHTML = ''; }}
function toggleScrapeForm() {{
  const f = document.getElementById('scrapeForm');
  f.classList.toggle('show');
  document.getElementById('scrapeStatus').style.display = 'none';
}}
async function scrapeWiki() {{
  const url = document.getElementById('scrapeUrl').value.trim();
  const type = document.getElementById('scrapeType').value;
  const name = document.getElementById('scrapeName').value.trim();
  const slug = document.getElementById('scrapeSlug').value.trim();
  const status = document.getElementById('scrapeStatus');

  if (!url || !name) {{ status.style.display = 'block'; status.className = 'status error'; status.textContent = 'URL and Name are required.'; return; }}

  status.style.display = 'block'; status.className = 'status'; status.textContent = '正在爬取... 可能需要几分钟。';
  try {{
    const resp = await fetch('/scrape', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{url,type,name,slug}})}});
    const data = await resp.json();
    if (data.ok) {{
      status.textContent = '完成! '+data.name+': '+data.pages+' 页, '+Math.round(data.total_chars/1000)+'k 字。页面即将刷新...';
      setTimeout(() => location.reload(), 2000);
    }} else {{
      status.className = 'status error';
      status.textContent = '错误: ' + data.error;
    }}
  }} catch(e) {{
    status.className = 'status error';
    status.textContent = 'Network error: ' + e.message;
  }}
}}
let chatHistory = [];
async function ask() {{
  const input = document.getElementById('question');
  const btn = document.getElementById('sendBtn');
  const q = input.value.trim();
  if (!q) return;
  input.value = ''; btn.disabled = true; btn.innerHTML = 'Sending...<span class=\"loading\"></span>';
  const msgs = document.getElementById('messages');
  msgs.innerHTML += '<div class=\"msg user\">' + esc(q) + '</div>';
  try {{
    const resp = await fetch('/ask', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question:q,wiki:currentWiki,history:chatHistory}})}});
    const data = await resp.json();
    let src = data.sources && data.sources.length ? '<div class=\"src\">Sources: '+data.sources.map(s=>s.page).join(', ')+'</div>' : '';
    msgs.innerHTML += '<div class=\"msg bot\">' + fmt(data.answer) + src + '</div>';
    chatHistory.push({{role:'user',content:q}});
    chatHistory.push({{role:'assistant',content:data.answer}});
    if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);
  }} catch(e) {{ msgs.innerHTML += '<div class=\"msg bot\" style=\"color:#e94560\">Error: '+e.message+'</div>'; }}
  btn.disabled = false; btn.innerHTML = 'Send'; msgs.scrollTop = msgs.scrollHeight;
}}
function esc(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}}
function fmt(t){{return t.replace(/```(\\w*)\\n([\\s\\S]*?)```/g,'<pre><code>$2</code></pre>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\\n/g,'<br>')}}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _render_ui()


if __name__ == "__main__":
    import uvicorn
    mkb = get_kb(embedding_model=EMBEDDING_MODEL)
    print(f"Loaded {len(mkb.wikis)} wiki(s): {', '.join(w['name'] for w in mkb.list_wikis())}")
    if not API_KEY:
        print("⚠ No API key — /ask disabled, /search still works")

    # Version check
    try:
        ver_file = Path(__file__).parent / "VERSION"
        if ver_file.exists():
            print(f"Version: {ver_file.read_text().strip()}")
    except:
        pass

    print(f"✓ http://localhost:{PORT}")
    print(f"  Embedding: {EMBEDDING_MODEL}")
    print(f"  Hot reload: enabled (drop .json into data/ dir)")
    print(f"  Update wiki: POST /update")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
