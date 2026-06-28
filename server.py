"""
Wiki Q&A Bot — multi-wiki RAG server.
Auto-discovers wikis from data/*.json, switchable via API and web UI.
"""
import os
import sys
import re
from pathlib import Path
import yaml

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx

from knowledge_base import get_kb

# ── Load config ──────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.yaml"
config = {}
if CONFIG_PATH.exists():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

wiki_cfg = config.get("wiki", {})
llm_cfg = config.get("llm", {})
srv_cfg = config.get("server", {})

API_KEY = llm_cfg.get("api_key") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
API_BASE = llm_cfg.get("base_url") or "https://api.deepseek.com/v1"
MODEL = llm_cfg.get("model") or "deepseek-chat"
HOST = srv_cfg.get("host", "0.0.0.0")
PORT = int(srv_cfg.get("port", 8080))

# ── App ──────────────────────────────────────────────────────
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_kb()  # preload on startup
    yield


app = FastAPI(title="Wiki Q&A Bot", version="3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ───────────────────────────────────────────────────
class QuestionRequest(BaseModel):
    question: str
    wiki: str | None = None   # wiki slug, e.g. "isaac"
    top_k: int = 8

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


@app.get("/info")
async def system_info():
    """System info: which APIs loaded, costs, etc."""
    mkb = get_kb()
    return {
        "api_provider": "deepseek" if "deepseek" in API_BASE else "openai",
        "model": MODEL,
        "cost_estimate": "≈¥0.002/question (DeepSeek: ¥1/M tokens)",
        "hot_reload": "enabled (5s polling, or POST /reload)",
        "wikis": mkb.list_wikis(),
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

    chunks = kb.search(request.question, top_k=15)  # Get more candidates

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
            chunks = merged[:15]

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
        context_parts.append(f"--- {c['page']} | {c['section']} ---\n{c['text']}")

    sp = (
        f"You are a knowledgeable Q&A bot for {kb.name}. "
        "First, answer using the wiki context provided below. "
        "If the wiki context doesn't fully answer the question, use web search to supplement your knowledge. "
        "Always respond in the same language as the user's question. "
        "Include specific facts, stats, or steps. Be thorough and helpful."
    )
    user_prompt = f"Context:\n\n{'\\\\n\\\\n'.join(context_parts) if context_parts else '(No wiki results found — answer based on your knowledge and web search)'}\n\nQuestion: {request.question}\n\nAnswer:"

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "system", "content": sp}, {"role": "user", "content": user_prompt}], "temperature": 0.3, "max_tokens": 2000, "enable_search": True},
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
    wiki_opts = "\n".join(f'<option value="{w["slug"]}" {"selected" if i == 0 else ""}>{w["name"]} ({w["pages"]} pages)</option>' for i, w in enumerate(wikis))
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
</style>
</head>
<body>
<div class="header">
  <h1>📚 Wiki Q&A Bot</h1>
  <select id="wikiSelect" onchange="switchWiki()">{wiki_opts}</select>
  <span class="hint">按 Enter 发送</span>
</div>
<div class="chat">
  <div class="messages" id="messages">
    <div class="msg bot">你好！已加载 <b>{first_name}</b>。选一个 wiki，输入问题即可查询。</div>
  </div>
  <div class="input-area">
    <input id="question" placeholder="输入你的问题…" onkeydown="if(event.key==='Enter')ask()">
    <button id="sendBtn" onclick="ask()">发送</button>
  </div>
</div>
<script>
let currentWiki = '{wikis[0]["slug"] if wikis else ""}';
function switchWiki() {{ currentWiki = document.getElementById('wikiSelect').value; }}
async function ask() {{
  const input = document.getElementById('question');
  const btn = document.getElementById('sendBtn');
  const q = input.value.trim();
  if (!q) return;
  input.value = ''; btn.disabled = true; btn.innerHTML = '发送中<span class="loading"></span>';
  const msgs = document.getElementById('messages');
  msgs.innerHTML += '<div class="msg user">' + esc(q) + '</div>';
  try {{
    const resp = await fetch('/ask', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question:q,wiki:currentWiki}})}});
    const data = await resp.json();
    let src = data.sources && data.sources.length ? '<div class="src">Sources: '+data.sources.map(s=>s.page).join(', ')+'</div>' : '';
    msgs.innerHTML += '<div class="msg bot">' + fmt(data.answer) + src + '</div>';
  }} catch(e) {{ msgs.innerHTML += '<div class="msg bot" style="color:#e94560">Error: '+e.message+'</div>'; }}
  btn.disabled = false; btn.innerHTML = '发送'; msgs.scrollTop = msgs.scrollHeight;
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
    mkb = get_kb()
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
    print(f"  Hot reload: enabled (drop .json into data/ dir)")
    print(f"  Update wiki: POST /update")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
