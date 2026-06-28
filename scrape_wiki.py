#!/usr/bin/env python3
"""
Multi-wiki scraper. Reads config.yaml, scrapes wiki pages, saves as JSON.
Supports: sphinx/rtd, mediawiki (Fandom/Wikipedia), generic
"""
import urllib.request
import ssl
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"
HEADERS = {
    "User-Agent": "WikiBot/2.0 (Q&A bot; contact@example.com)",
    "Accept": "text/html,application/json",
}


class TextExtractor(HTMLParser):
    """Extract visible text, skip scripts/styles/nav."""
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_tags = {'script', 'style', 'nav', 'header', 'footer', 'svg', 'head', 'aside'}
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if self.skip_depth > 0 or tag in self.skip_tags:
            self.skip_depth += 1
        elif tag in ('h1','h2','h3','h4'):
            self.text.append('\n## ')
        elif tag in ('p','li','br','div','section'):
            self.text.append('\n')
        elif tag == 'code':
            self.text.append('`')
        elif tag == 'pre':
            self.text.append('\n```\n')

    def handle_endtag(self, tag):
        if self.skip_depth > 0:
            self.skip_depth -= 1
        elif tag == 'code':
            self.text.append('`')
        elif tag == 'pre':
            self.text.append('\n```\n')

    def handle_data(self, data):
        if self.skip_depth == 0 and data.strip():
            self.text.append(data.strip())


def clean_text(raw: str) -> str:
    text = re.sub(r'\n{3,}', '\n\n', raw)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


# ── Sphinx/RTD scraper ───────────────────────────────────────
def scrape_sphinx(base_url: str) -> dict:
    """Scrape Sphinx/ReadTheDocs documentation site."""
    # First fetch index to discover pages
    req = urllib.request.Request(base_url + "/", headers=HEADERS)
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=15) as resp:
        index_html = resp.read().decode('utf-8', errors='replace')

    # Find all internal .html links
    links = set(re.findall(r'href="([^"]*\.html[^"]*)"', index_html))
    pages = {l.split('#')[0] for l in links if l.startswith(('setup','usage','openai','other','tool','schema','template','fragment','alias','python','log','related','help','contribut','change','embed','plugin','index'))}

    # Ensure homepage
    pages.add("index.html")

    results = {}
    ctx = ssl.create_default_context()

    for page in sorted(pages):
        url = f"{base_url}/{page}"
        print(f"  [{len(results)+1}/{len(pages)}] {page} ...", end=" ", flush=True)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                content = resp.read().decode('utf-8', errors='replace')
                extractor = TextExtractor()
                extractor.feed(content)
                text = clean_text(" ".join(extractor.text))
                results[page] = text
                print(f"OK ({len(text)} chars)")
        except Exception as e:
            print(f"FAIL: {e}")

    return results


# ── MediaWiki scraper ────────────────────────────────────────
def strip_wikitext(text: str) -> str:
    """Convert MediaWiki wikitext to readable plain text."""
    import html
    # Remove HTML comments
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # Remove templates {{...}} (nested)
    depth = 0
    result = []
    i = 0
    while i < len(text):
        if text[i:i+2] == '{{' and depth == 0:
            depth = 1
            i += 2
            continue
        if depth > 0:
            if text[i:i+2] == '{{':
                depth += 1
                i += 2
                continue
            elif text[i:i+2] == '}}':
                depth -= 1
                i += 2
                continue
            i += 1
            continue
        result.append(text[i])
        i += 1
    text = ''.join(result)
    # Remove tables {|...|}
    text = re.sub(r'\{\|.*?\|\}', '', text, flags=re.DOTALL)
    # Links: [[target|display]] -> display, [[target]] -> target
    text = re.sub(r'\[\[([^|\]]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    # External links: [url text] -> text
    text = re.sub(r'\[https?://[^\s\]]+\s+([^\]]+)\]', r'\1', text)
    text = re.sub(r'\[https?://[^\s\]]+\]', '', text)
    # Remove ref tags
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)
    # Remove other HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Bold/italic
    text = re.sub(r"'''(.+?)'''", r'\1', text)
    text = re.sub(r"''(.+?)''", r'\1', text)
    # Lists
    text = re.sub(r'^[*#:;]+\s*', '', text, flags=re.MULTILINE)
    # Headers == ... ==
    text = re.sub(r'^=+\s*(.+?)\s*=+$', r'## \1', text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r'^-{4,}', '', text)
    # Decode HTML entities
    text = html.unescape(text)
    # Clean whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def scrape_mediawiki(api_url: str, base_url: str) -> dict:
    """
    Scrape MediaWiki site (Wikipedia, Fandom, etc.) via API.
    Uses revisions API for wikitext, then strips formatting.
    """
    import urllib.parse

    results = {}
    ctx = ssl.create_default_context()

    # Get all page titles
    print("Fetching page list...")
    page_titles = []
    apcontinue = None
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "allpages",
            "aplimit": "500",
            "apfilterredir": "nonredirects",
        }
        if apcontinue:
            params["apcontinue"] = apcontinue
        url = api_url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read())
        for page in data["query"]["allpages"]:
            page_titles.append(page["title"])
        if "continue" in data:
            apcontinue = data["continue"]["apcontinue"]
        else:
            break

    # Filter out non-content pages
    page_titles = [t for t in page_titles if not t.startswith(("Template:", "Category:", "File:", "Module:", "MediaWiki:"))]
    print(f"Found {len(page_titles)} pages. Fetching content...")

    # Fetch page content in batches of 25 (revisions API is heavier)
    for i in range(0, len(page_titles), 25):
        batch = page_titles[i:i+25]
        params = {
            "action": "query",
            "format": "json",
            "prop": "revisions",
            "rvprop": "content",
            "titles": "|".join(batch),
        }
        url = api_url + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                data = json.loads(resp.read())
            for page_id, page_data in data["query"]["pages"].items():
                title = page_data.get("title", page_id)
                revisions = page_data.get("revisions", [])
                if revisions and "*" in revisions[0]:
                    raw = revisions[0]["*"]
                    text = strip_wikitext(raw)
                    if len(text) > 100:
                        results[title] = text
            print(f"  [{min(i+25, len(page_titles))}/{len(page_titles)}] OK")
        except Exception as e:
            print(f"  Batch failed: {e}")

    return results


# ── Generic scraper ──────────────────────────────────────────
def scrape_generic(urls: list[str]) -> dict:
    """Scrape a list of specific URLs."""
    results = {}
    ctx = ssl.create_default_context()
    for url in urls:
        name = url.rstrip("/").split("/")[-1] or "index"
        print(f"  {name} ...", end=" ", flush=True)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                content = resp.read().decode('utf-8', errors='replace')
                extractor = TextExtractor()
                extractor.feed(content)
                text = clean_text(" ".join(extractor.text))
                results[name] = text
                print(f"OK ({len(text)} chars)")
        except Exception as e:
            print(f"FAIL: {e}")
    return results


# ── Main ─────────────────────────────────────────────────────
def main():
    if not CONFIG_PATH.exists():
        print(f"ERROR: {CONFIG_PATH} not found. Create config.yaml first.")
        sys.exit(1)

    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Support single wiki or wikis list
    wiki_list = config.get("wikis", [])
    if not wiki_list:
        wiki = config.get("wiki", {})
        if wiki.get("url"):
            wiki_list = [wiki]

    if not wiki_list:
        print("ERROR: Neither 'wiki' nor 'wikis' configured in config.yaml")
        sys.exit(1)

    for i, wiki in enumerate(wiki_list):
        wiki_type = wiki.get("type", "sphinx")
        wiki_url = wiki.get("url", "").rstrip("/")
        wiki_name = wiki.get("name", "Wiki")

        if not wiki_url:
            print(f"ERROR: wiki #{i+1} has no url")
            continue

        print(f"\n{'='*50}")
        print(f"[{i+1}/{len(wiki_list)}] {wiki_name} (type={wiki_type})")
        print(f"URL: {wiki_url}\n")

        if wiki_type == "mediawiki":
            api_url = wiki.get("api_url", "")
            if not api_url:
                for suffix in ["/api.php", "/w/api.php"]:
                    test_url = wiki_url + suffix
                    try:
                        req = urllib.request.Request(test_url, headers=HEADERS)
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            api_url = test_url
                            print(f"Auto-detected API: {api_url}")
                            break
                    except:
                        continue
            if not api_url:
                print("ERROR: Could not auto-detect MediaWiki API. Set wiki.api_url in config.yaml")
                continue
            results = scrape_mediawiki(api_url, wiki_url)

        elif wiki_type == "sphinx":
            results = scrape_sphinx(wiki_url)

        elif wiki_type == "generic":
            urls = wiki.get("urls", [wiki_url])
            results = scrape_generic(urls)

        else:
            print(f"ERROR: Unknown wiki type: {wiki_type}")
            continue

        # Save
        slug = wiki.get("slug", "") or re.sub(r'[^a-z0-9]+', '_', wiki_name.lower().strip()).strip('_')
        output = Path(__file__).parent / "data" / f"{slug}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False)

        total_chars = sum(len(v) for v in results.values())
        print(f"✓ Saved {len(results)} pages ({total_chars:,} chars) → {output}")
        print(f"  Slug: {slug}")

    print(f"\nDone. Server will hot-reload new wikis within 5 seconds.")

    # Auto-sync to Desktop sharing package
    dst = Path("/mnt/c/Users/hsyhi/Desktop/WikiBot分享包/data")
    if dst.parent.exists():
        import shutil
        for f in DATA_DIR.glob("*.json"):
            shutil.copy2(f, dst / f.name)
        print("✓ Synced to Desktop/WikiBot分享包/")


if __name__ == "__main__":
    main()
