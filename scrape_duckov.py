#!/usr/bin/env python3
"""Scrape escapefromduckov.net using Camoufox. Uses JS innerText for reliable extraction."""
import json, re, sys
from pathlib import Path
from camoufox import Camoufox

DATA_DIR = Path("/home/hsyhi/wiki-bot/data")
BASE = "https://escapefromduckov.net/zh"
PAGES = [
    "items", "wiki/weapons", "wiki/equipment", "wiki/totems",
    "wiki/keys", "wiki/medicine", "wiki/food", "wiki/creatures",
    "wiki/achievements", "wiki/buffs", "perks", "buildings",
    "shops", "quests", "maps", "tips", "notes", "guide",
]
ARGS = ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage']

def clean(raw):
    return re.sub(r'\n{3,}', '\n\n', raw).strip()

print("Starting Camoufox...", flush=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

with Camoufox(headless=True, args=ARGS) as browser:
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    print("Browser ready.", flush=True)
    results = {}

    for i, subpath in enumerate(PAGES):
        url = f"{BASE}/{subpath}"
        name = subpath.replace("/", "_")
        try:
            sys.stdout.write(f"[{i+1}/{len(PAGES)}] {name} ... "); sys.stdout.flush()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            text = clean(page.evaluate("document.body.innerText"))
            if len(text) > 100:
                results[name] = text
            print(f"{len(text)} chars", flush=True)
        except Exception as e:
            print(f"FAIL: {e}", flush=True)

    out = DATA_DIR / "duckov.json"
    out.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    (DATA_DIR / "duckov.meta.json").write_text(json.dumps({"name": "逃离鸭科夫"}, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v) for v in results.values())
    print(f"\nDone: {len(results)} pages, {total:,} chars → {out}", flush=True)

    # Auto-sync to Desktop
    dst = Path("/mnt/c/Users/hsyhi/Desktop/WikiBot分享包/data")
    if dst.parent.exists():
        import shutil
        for f in DATA_DIR.glob("*.json"):
            shutil.copy2(f, dst / f.name)
        shutil.copy2(Path(__file__), Path("/mnt/c/Users/hsyhi/Desktop/WikiBot分享包/scrape_duckov.py"))
        print("✓ Synced to Desktop/WikiBot分享包/", flush=True)
