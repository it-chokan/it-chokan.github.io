#!/usr/bin/env python3
"""Build the static site into _site/. (v16)
v16: glossary index search box (static/glossary_search.js). Filters terms by term / full name / aliases / short
     (NFKC, lowercase, katakana = hiragana). ?q= supported. No hit -> link to the article search.
v15: Bing Webmaster verification meta (msvalidate.01) / IndexNow: key file at the site root and
     _indexnow/urls.json (pages changed today or yesterday JST) for the workflow to ping after deploy.
v14: article pages /t/<date>-<id>.html (one topic per page, stable id = article data-id, else the topic number) /
     <section class="deep"> inside an article = shown only on the article page (issue page stays a 5-min read) /
     article page: facts open, matched sources, related past articles (same product names / terms), share bar
     (X, LINE, Threads, Bluesky, Facebook, Hatena, copy, native share), follow + bookmark box, link to the issue /
     meta "corrections" (note box + dateModified) and "withdrawn" (noindex stub page) /
     search + glossary point to article pages / sitemap includes article pages.
v13: SEO. Issues are kept forever (archive LIST still shows the last KEEP_DAYS days) /
     search covers the last SEARCH_DAYS days / glossary pages: /glossary/ + one page per term
     ("<term>とは") listing the issues that used it / tooltip sheet links to the term page /
     WebSite JSON-LD alternateName "it-chokan" + publisher logo / sitemap includes glossary.
v12: follow buttons add Instagram / Facebook / TikTok (X on by default). Empty string in config.json "follow" hides a button.
     TikTok stays hidden until the username is changed to it_chokan (after 2026-12-15).
v11: kandoku card note adds "private browsing loses stamps when closed".
Inputs: config.json, glossary.json, posts/YYYY-MM-DD.html (first line: <!--meta {json} -->), static/
Optional: site_ids.json {"google_site_verification": "", "goatcounter": "", "show_counter": false}
SITE_URL: env SITE_URL, else derived from GITHUB_REPOSITORY (owner/repo) -> https://owner.github.io/repo/
v6: sitemap.xml, robots.txt, NewsArticle JSON-LD, about page, Search Console meta, GoatCounter tag.
v7: allowlist HTML sanitizer, Content-Security-Policy meta, counter script as static file.
v8: top page = today's issue in full / archive page (last 90 days, missing weekdays = "休刊") /
    /latest/ redirect (fixed URL for QR & SNS) / 404 page that leads to today's issue /
    cache-busting ?v=hash on static assets / publish time 07:00 JST / recovered-issue badge /
    posts older than KEEP_DAYS are ignored (the VPS publish script deletes them).
v10: issue page = cover (date + 3 lines with 1-line summaries + "3 lines OK / read all 5 min") /
     kandoku card (完読！ + 朝刊スタンプ, cumulative titles 新人/記者/デスク/編集長/主筆, stamps in localStorage only) /
     reading bar (bottom) / "this is an old issue -> latest" band / logo icon recolored by title /
     web app manifest (home screen opens the top page) / /latest/ now redirects to the top page.
v9: full-text search. search.json (one entry per topic, NFKC-normalized plain text) + /search/ page
    + static/search.js (substring AND match, works for Japanese without word splitting).
"""
import json, os, re, shutil, html, pathlib, unicodedata, hashlib
from urllib.parse import quote
from datetime import datetime, date, timedelta, timezone
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "_site"
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
glossary = json.loads((ROOT / "glossary.json").read_text(encoding="utf-8-sig"))
_ids = ROOT / "site_ids.json"
ids = json.loads(_ids.read_text(encoding="utf-8-sig")) if _ids.exists() else {}
GSV = (ids.get("google_site_verification") or "").strip()
BSV = (ids.get("bing_site_verification") or "5C3F5D5BE8B834C431D19F72735A5C58").strip()   # v15: Bing Webmaster Tools
INDEXNOW_KEY = (ids.get("indexnow_key") or "01b867e2fd5b788622345cbfa5a5fe3b").strip()   # v15: public by design
GC = (ids.get("goatcounter") or "").strip()
SHOW_COUNTER = bool(ids.get("show_counter")) and bool(GC)
# v10: official SNS (config.json "follow" overrides; empty string hides a button)
FOLLOW = {"x": "https://x.com/it_chokan", "threads": "https://www.threads.com/@it_chokan",
          "instagram": "https://www.instagram.com/it_chokan", "facebook": "https://www.facebook.com/it.chokan",
          "tiktok": "", "bluesky": "https://bsky.app/profile/it-chokan.bsky.social"} | (cfg.get("follow") or {})
ICON_SRC = ROOT / "static" / "icon" / "source.png"   # square brand icon; rank variants are generated at build time
HAS_ICON = False                                     # set by make_icons()

JST = timezone(timedelta(hours=9))
PUB_TIME = "T07:00:00+09:00"   # publish time of the daily post (JST)
KEEP_DAYS = 90                 # archive LIST length (days, including today). v13: issue pages are never deleted
SEARCH_DAYS = 365              # v13: search.json covers this many days (keeps the search page light)
CLOSED_AFTER_HOUR = 10         # today's issue is shown as 休刊 only after 10:00 JST
_now = os.environ.get("BUILD_NOW_JST")  # for tests: "2026-11-10T09:00"
NOW = datetime.fromisoformat(_now).replace(tzinfo=JST) if _now else datetime.now(JST)
TODAY = NOW.date()
CUTOFF = TODAY - timedelta(days=KEEP_DAYS - 1)
WD = "月火水木金土日"

def site_url():
    u = os.environ.get("SITE_URL")
    if u:
        return u.rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner.lower()}.github.io/"
        return f"https://{owner.lower()}.github.io/{name}/"
    return "http://localhost:8000/"

BASE = site_url()

def jdate(d):
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return f"{d.month}月{d.day}日（{WD[d.weekday()]}）"

# Title rules: one topic per line, max 3 lines, each line <= 30 width units
# (full-width char = 2, half-width = 1 -> about 15 Japanese chars)
# A line starting with "T:" is a tech topic (blue marker + TECH badge).
TITLE_MAX_LINES = 3
TITLE_MAX_WIDTH = 30

def _w(ch):
    return 2 if unicodedata.east_asian_width(ch) in "WFA" else 1

def title_lines(title, slug=""):
    raw = [x.strip() for x in title.split("／") if x.strip()]
    if len(raw) > TITLE_MAX_LINES:
        print(f"WARN {slug}: title has {len(raw)} lines, keeping {TITLE_MAX_LINES}")
        raw = raw[:TITLE_MAX_LINES]
    out = []
    for ln in raw:
        kind = "sec"
        if ln[:2] in ("T:", "T："):
            kind, ln = "tech", ln[2:].strip()
        if sum(_w(c) for c in ln) > TITLE_MAX_WIDTH:
            print(f"WARN {slug}: title line too long, truncated: {ln}")
            acc, cut = 0, ""
            for c in ln:
                if acc + _w(c) > TITLE_MAX_WIDTH - 2:
                    break
                acc += _w(c)
                cut += c
            ln = cut + "…"
        out.append((ln, kind))
    return out

def title_plain(title):
    return " / ".join(t for t, _ in title_lines(title))

def title_html(title, slug=""):
    return "".join('<span class="line ' + k + '">' + html.escape(t) + '</span>' for t, k in title_lines(title, slug))


# ---- v7: allowlist sanitizer for AI-written post HTML ----
ALLOWED = {
    "h2": {"class", "id"}, "h3": set(), "span": {"class"}, "b": set(), "strong": set(), "em": set(), "br": set(),
    "article": {"class", "data-sev", "data-tag", "data-short", "data-id"}, "header": set(), "section": {"class"},
    "p": {"class"}, "ul": {"class"}, "ol": set(), "li": set(), "dl": {"class"}, "dt": set(), "dd": set(),
    "a": {"href"}, "script": {"type", "id"},
}
VOID = {"br"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "noscript", "form", "textarea", "select"}
SAFE_VAL = re.compile(r"^[^<>\"`]*$")

class Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.removed, self.skip, self.in_json = [], [], 0, False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script":
            if a.get("type") == "application/json" and a.get("id") == "post-terms" and not self.skip:
                self.in_json, self.json_buf = True, []
            else:
                self.removed.append("script"); self.skip += 1
            return
        if self.skip:
            if tag in DROP_WITH_CONTENT: self.skip += 1
            return
        if tag in DROP_WITH_CONTENT:
            self.removed.append(tag); self.skip += 1; return
        if tag not in ALLOWED:
            self.removed.append(tag); return
        parts = [tag]
        for k, v in attrs:
            if k not in ALLOWED[tag] or v is None or not SAFE_VAL.match(v):
                self.removed.append(f"{tag}@{k}"); continue
            if tag == "a" and k == "href" and not re.match(r"^https?://", v, re.I):
                self.removed.append("a@href(non-http)"); continue
            parts.append(f'{k}="{html.escape(html.unescape(v), quote=True)}"')
        if tag == "a":
            parts.append('rel="noopener noreferrer"')
        self.out.append("<" + " ".join(parts) + ">")
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ALLOWED and tag not in VOID and not self.skip:
            self.out.append(f"</{tag}>")
    def handle_endtag(self, tag):
        if tag == "script" and self.in_json:
            raw = "".join(self.json_buf); self.in_json = False
            try:
                terms = json.loads(raw)
                clean = [{k: str(t.get(k, "")) for k in ("term", "full", "short", "long")} | {"aliases": [str(x) for x in t.get("aliases", [])]}
                         for t in terms if isinstance(t, dict) and t.get("term")]
                self.out.append('<script type="application/json" id="post-terms">' + json.dumps(clean, ensure_ascii=False).replace("<", "\\u003c") + "</script>")
            except Exception:
                self.removed.append("post-terms(invalid json)")
            return
        if self.skip:
            if tag in DROP_WITH_CONTENT: self.skip -= 1
            return
        if tag in ALLOWED and tag not in VOID:
            self.out.append(f"</{tag}>")
    def handle_data(self, d):
        if self.in_json: self.json_buf.append(d); return
        if not self.skip: self.out.append(html.escape(html.unescape(d), quote=False))
    def handle_entityref(self, name):
        if not self.skip and not self.in_json: self.out.append(f"&{name};")
    def handle_charref(self, name):
        if not self.skip and not self.in_json: self.out.append(f"&#{name};")
    def handle_comment(self, d):
        pass

def sanitize(body, slug=""):
    s = Sanitizer(); s.feed(body); s.close()
    if s.removed:
        print(f"WARN {slug}: sanitizer removed: {sorted(set(s.removed))}")
    return "".join(s.out)

CSP = ("default-src 'self'; script-src 'self' https://gc.zgo.at; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data: https://*.goatcounter.com; "
       "connect-src 'self' https://*.goatcounter.com; object-src 'none'; base-uri 'self'; form-action 'none'")

MAX_TOPICS = 6
CAT_LABEL = {"cat-incident": "インシデント", "cat-tech": "新技術"}

def enhance_body(body, slug=""):
    """Assign ids to topics, fold facts into <details>, and build TOC + category tabs."""
    items, cat, n = [], "cat-incident", 0
    out, pos = [], 0
    tok = re.compile(r'<h2 class="part" id="(cat-[a-z]+)"|<article class="topic"([^>]*)>|<dl class="facts">(.*?)</dl>', re.S)
    for m in tok.finditer(body):
        out.append(body[pos:m.start()])
        if m.group(1):
            cat = m.group(1)
            out.append(m.group(0))
        elif m.group(2) is not None:
            n += 1
            attrs = m.group(2)
            tag = (re.search(r'data-tag="([^"]*)"', attrs) or [None, ""])[1]
            short = (re.search(r'data-short="([^"]*)"', attrs) or [None, ""])[1]
            if not short:
                h3 = re.search(r"<h3>(.*?)</h3>", body[m.end():], re.S)
                short = re.sub(r"<[^>]+>", "", h3.group(1)) if h3 else f"topic {n}"
            items.append((n, cat, short, tag))
            out.append(f'<article class="topic" id="t{n}"{attrs}>')
        else:
            out.append(f'<details class="more"><summary>詳細（対象・バージョン）</summary><dl class="facts">{m.group(3)}</dl></details>')
        pos = m.end()
    out.append(body[pos:])
    if n > MAX_TOPICS:
        print(f"WARN {slug}: {n} topics (max {MAX_TOPICS})")
    toc = '<nav class="toc" aria-label="今日のトピック"><h2>今日のトピック</h2><ol>'
    for i, c, short, tag in items:
        chip = f'<span class="tag">{html.escape(tag)}</span>' if tag else ""
        toc += f'<li class="{c}"><a href="#t{i}"><span class="tt">{short}</span>{chip}</a></li>'
    toc += "</ol></nav>"
    cats = [c for c in CAT_LABEL if f'id="{c}"' in body]
    tabs = ""
    if len(cats) > 1:
        tabs = '<nav class="tabs" aria-label="カテゴリ">' + "".join(
            f'<a href="#{c}" data-cat="{c}">{CAT_LABEL[c]}<span>{sum(1 for it in items if it[1] == c)}</span></a>' for c in cats) + "</nav>"
    return toc, tabs, "".join(out), items

esc = html.escape
FONTS = '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=M+PLUS+Rounded+1c:wght@400;700;800&family=JetBrains+Mono:wght@500;700&display=swap">'

# ---- v8: cache busting ----
VER = {}

def asset(rel, name):
    v = VER.get(name)
    return f"{rel}static/{name}" + (f"?v={v}" if v else "")

def jsonld(obj):
    return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False).replace("</", "<\\/") + "</script>"

def head(title, desc, url, rel, og_type="article", extra="", robots=""):
    tw = f'<meta name="twitter:site" content="@{esc(cfg["x_handle"])}">' if cfg.get("x_handle") else ""
    gsv = f'<meta name="google-site-verification" content="{esc(GSV)}">' if GSV else ""
    gsv += f'<meta name="msvalidate.01" content="{esc(BSV)}">' if BSV else ""
    gc = f'<script data-goatcounter="https://{esc(GC)}.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>' if GC else ""
    rb = f'<meta name="robots" content="{robots}">' if robots else ""
    canon = f'<link rel="canonical" href="{url}">' if url else ""
    icons = (f'<link rel="manifest" href="{BASE}manifest.webmanifest"><meta name="theme-color" content="#FDD446">'
             + (f'<link rel="icon" type="image/png" href="{BASE}static/icon/icon-192.png"><link rel="apple-touch-icon" href="{BASE}static/icon/icon-192.png">' if HAS_ICON else ""))
    logo = (f'<img class="logo-ic" id="logoic" src="{rel}static/icon/rank0.png" width="40" height="40" alt="">' if HAS_ICON
            else '<span class="prompt">&gt;_</span>')
    return f"""<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{CSP}"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">{rb}
{canon}
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="{esc(cfg['site_title'])}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{url or BASE}">
<meta property="og:image" content="{BASE}static/ogp.png">
<meta property="og:locale" content="ja_JP">
<meta name="twitter:card" content="summary_large_image">{tw}{gsv}
<link rel="alternate" type="application/rss+xml" title="{esc(cfg['site_title'])}" href="{BASE}feed.xml">
{FONTS}
<link rel="stylesheet" href="{asset(rel, 'style.css')}">{icons}{extra}{gc}
</head><body><div class="wrap">
<div class="mast"><a href="{rel}"><p class="logo">{logo}{esc(cfg['site_title'])}<span class="rank-chip" id="rankchip" hidden></span></p></a><span class="tag">// security × tech, every morning</span></div>
<nav class="navlinks" aria-label="サイト内"><a href="{rel}">今日の朝刊</a><a href="{rel}archive/">バックナンバー</a><a href="{rel}glossary/">用語集</a><a href="{rel}search/">検索</a></nav>
"""

def foot(rel):
    return f"""<footer>記事は公開時点の情報です。対応する前に、出典の公式発表を確認してください。黄色いマーカーの用語はタップで解説が出ます。<br><a href="{rel}archive/">バックナンバー</a> ・ <a href="{rel}glossary/">用語集</a> ・ <a href="{rel}about.html">このサイトについて</a></footer>
</div></body></html>"""

SHEET = """<div class="sheet" id="sheet" hidden role="dialog" aria-live="polite"><div class="in">
<div class="t"><b id="sT"></b><i id="sF" hidden></i></div><p class="s" id="sS"></p><p id="sL"></p><a id="sG" class="sg" hidden>この用語をくわしく →</a>
<div class="ctrl"><button class="btn" id="sX" type="button">閉じる</button></div>
</div></div>"""

def counter_html(rel):
    if not SHOW_COUNTER:
        return ""
    return f'<p class="views" id="views" hidden></p><script src="{asset(rel, "counter.js")}" defer></script>\n'

COUNTER_JS = """(()=>{const e=document.getElementById('views');if(!e)return;
fetch('https://%s.goatcounter.com/counter/'+encodeURIComponent(location.pathname)+'.json')
.then(r=>r.ok?r.json():null).then(j=>{if(j&&j.count){e.textContent='\\u3053\\u306e\\u8a18\\u4e8b\\u306f '+j.count+' \\u56de\\u8aad\\u307e\\u308c\\u307e\\u3057\\u305f';e.hidden=false}}).catch(()=>{})})();
"""

def load_posts():
    posts = []
    for p in sorted((ROOT / "posts").glob("*.html"), reverse=True):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            print(f"WARN skip (bad file name): {p.name}")
            continue
        txt = p.read_text(encoding="utf-8-sig")
        m = re.match(r"\s*<!--meta\s+(\{.*?\})\s*-->", txt, re.S)
        if not m:
            raise SystemExit(f"meta missing: {p}")
        meta = json.loads(m.group(1))
        if meta.get("date") != p.stem:
            print(f"WARN {p.stem}: meta date {meta.get('date')} differs from file name; using file name")
            meta["date"] = p.stem
        meta["slug"] = p.stem
        meta["body"] = sanitize(txt[m.end():], p.stem)
        posts.append(meta)
    return posts

def recovered_note(p):
    if not p.get("recovered"):
        return ""
    when = p.get("published_jst", "")
    when = f"（{esc(when)} 掲載）" if when else ""
    return f'<p class="recovered">この号は配信トラブルのため、遅れて掲載しました{when}。</p>\n'

# ---- v10: cover / kandoku card / reading bar ----
def _txt(fragment):
    t = re.sub(r"<script\b.*?</script>", "", fragment, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t)).strip()

def first_sentence(article_html, limit=50):
    m = re.search(r'<p class="what">(.*?)</p>', article_html, re.S)
    if not m:
        return ""
    t = _txt(m.group(1))
    cut = t.find("。")
    t = t[:cut + 1] if cut >= 0 else t
    return t if len(t) <= limit else t[:limit - 1] + "…"

def read_minutes(body):
    parts = re.findall(r'<p class="(?:what|ex|act)">(.*?)</p>|<ul class="leaks">(.*?)</ul>', body, re.S)
    chars = sum(len(html.unescape(_txt(a or b))) for a, b in parts)
    return max(1, min(9, -(-chars // 400)))

def cover_html(p, body, items, rel, is_current, latest):
    arts = {int(n): inner for n, inner in re.findall(r'<article class="topic" id="t(\d+)"[^>]*>(.*?)</article>', body, re.S)}
    pools = {"sec": [n for n, c, _, _ in items if c == "cat-incident"], "tech": [n for n, c, _, _ in items if c == "cat-tech"]}
    used, rows = set(), []
    for i, (line, kind) in enumerate(title_lines(p["title"], p["slug"]), 1):
        pool = [n for n in pools[kind] if n not in used] or [n for n, *_ in items if n not in used]
        n = pool[0] if pool else None
        if n: used.add(n)
        sub = first_sentence(arts.get(n, "")) if n else ""
        tech = '<span class="cv-tech">新技術</span>' if kind == "tech" else ""
        rows.append(f'<a class="cv-row {kind}" href="#t{n or 1}"><span class="cv-no">{i}</span>'
                     f'<span class="cv-tx"><span class="cv-h">{tech}{esc(line)}</span><span class="cv-s">{sub}</span></span></a>')
    mins = read_minutes(body)
    band = ""
    if not is_current and latest:
        band = (f'<a class="latest-band" href="{rel}">これは{jdate(p["date"])}号です。'
                f'<b>最新号（{jdate(latest["date"])}）はこちら →</b></a>\n')
    badge = "平日 毎朝7時の最新号" if is_current else "バックナンバー"
    return band + f"""<section class="cover" aria-label="けさの3行">
<p class="cv-badge">{badge}</p>
<p class="cv-date">{jdate(p["date"]).replace("（", '<small>（').replace("）", "）</small>")}</p>
<p class="cv-sub">けさの3行 <span>ここまで1分</span></p>
<h1 class="cv-lines">{"".join(rows)}</h1>
<div class="cv-cta"><a href="#kandoku" class="cv-quick">3行でOK<small>{"スタンプだけ押す" if is_current else "最後へ"}</small></a><a href="#t1" class="cv-all">{mins}分で全部読む<small>{len(items)}本・用語つき</small></a></div>
</section>
""", mins

def kandoku_html(p, rel, is_current):
    fl = [(k, lab) for k, lab in (("x", "X"), ("threads", "Threads"), ("instagram", "Instagram"), ("facebook", "Facebook"), ("tiktok", "TikTok"), ("bluesky", "Bluesky")) if FOLLOW.get(k)]
    btns = "".join(f'<a href="{esc(FOLLOW[k])}" rel="noopener" target="_blank">{lab}</a>' for k, lab in fl)
    btns += f'<a href="{BASE}feed.xml">RSS</a>'
    head_t = "けさのIT、これで全部です。" if is_current else "この号は、これで全部です。"
    note = "" if is_current else '<p class="kd-old">スタンプは最新号を完読すると押されます。</p>'
    return f"""<section class="kandoku" id="kandoku" aria-label="完読">
<p class="kd-stamp">完読！</p>
<p class="kd-head">{head_t}</p>
<p class="kd-next" id="kdnext">次号は 平日の朝7:00 に届きます</p>
<div class="kd-card">
<p class="kd-title">朝刊スタンプ <span id="kdcount"></span></p>
<div class="kd-week" id="kdweek"></div>
<p class="kd-rank" id="kdrank">最新号を完読すると、スタンプが押されます。</p>
{note}<p class="kd-note">記録はこの端末のブラウザだけに保存されます。プライベートブラウズでは、閉じると消えます。</p>
</div>
<p class="kd-follow-h">明日の朝刊も受け取る</p>
<div class="kd-follow">{btns}</div>
<p class="kd-bm">ブックマークは <a href="{BASE}">トップページ</a> へ。毎朝7時に最新号へ切り替わります。</p>
<div class="kd-btns"><button type="button" id="kdshare" hidden>けさの3行をシェア</button>
<details class="kd-home"><summary>ホーム画面に追加</summary><p>iPhone（Safari）: 共有ボタン → 「ホーム画面に追加」<br>Android（Chrome）: 右上の︙ → 「ホーム画面に追加」<br>追加したアイコンからは、いつでも最新号が開きます。</p></details></div>
<p class="kd-msg" id="kdmsg" aria-live="polite"></p>
</section>
"""

def issue_data(p, rel, is_current, mins, issue_dates):
    d = {"date": p["date"], "current": is_current, "min": mins, "issues": issue_dates,
         "top": BASE, "icon": f"{rel}static/icon/" if HAS_ICON else "",
         "url": f"{BASE}posts/{p['slug']}.html",
         "share": "けさのIT朝刊（" + jdate(p["date"]) + "）\n" + "\n".join(f"{i}. {t}" for i, (t, _) in enumerate(title_lines(p["title"]), 1))}
    return '<script type="application/json" id="issue-data">' + json.dumps(d, ensure_ascii=False).replace("<", "\\u003c") + "</script>"

RBAR = """<div class="rbar" id="rbar" hidden><div class="rb-l"><span class="rb-track"><i id="rbfill"></i></span><span id="rbtext"></span></div><a class="rb-btn" id="rbbtn" href="#kandoku">フォロー</a></div>"""

def render_issue(p, rel, url, gl_json, is_top=False, extra_head="", latest=None, issue_dates=()):
    """Full issue page. Used for posts/YYYY-MM-DD.html and for the top page."""
    toc, tabs, body, items = enhance_body(DEEP_RE.sub("", p["body"]), p["slug"])
    is_current = bool(latest) and latest["date"] == p["date"]
    tps = topics_of(p)
    _k = iter(range(len(tps)))
    def _tlink(m):
        i = next(_k, None)
        if i is None:
            return m.group(0)
        return (f'<p class="tp-link"><a href="{rel}t/{tps[i]["slug"]}.html">この記事のページ（共有・関連記事）›</a></p></article>')
    body = re.sub(r"</article>", _tlink, body)
    pub = p.get("published_jst") or (p["date"] + PUB_TIME)
    ld = jsonld({
        "@context": "https://schema.org", "@type": "NewsArticle",
        "headline": title_plain(p["title"])[:110],
        "description": p["summary"],
        "datePublished": p["date"] + PUB_TIME,
        "dateModified": modified_of(p, pub if "T" in pub else p["date"] + PUB_TIME),
        "inLanguage": "ja",
        "mainEntityOfPage": f"{BASE}posts/{p['slug']}.html",
        "url": f"{BASE}posts/{p['slug']}.html",
        "image": [f"{BASE}static/ogp.png"],
        "author": {"@type": "Organization", "name": cfg["site_title"], "url": BASE},
        "publisher": PUBLISHER,
        "about": [re.sub(r"<[^>]+>", "", s) for _, _, s, _ in items],
    })
    meta_date = f'<meta name="it-chokan-date" content="{p["date"]}">'
    if is_top:
        page = head(f"{cfg['site_title']}｜{title_plain(p['title'])}", p["summary"], url, rel, "website",
                    extra=extra_head + ld + meta_date)
    else:
        page = head(f"{title_plain(p['title'])}｜{cfg['site_title']} {p['date']}", p["summary"], url, rel,
                    extra=ld + meta_date)
    cover, mins = cover_html(p, body, items, rel, is_current, latest)
    page += recovered_note(p)
    page += corrections_html(p)
    page += cover
    page += counter_html(rel)
    page += toc + tabs
    page += f'<div id="article">{body}</div>\n'
    page += '<section class="gl"><h2>この記事の用語</h2><ul id="gllist"></ul></section>\n'
    page += kandoku_html(p, rel, is_current)
    page += f'<p class="more-issues"><a href="{rel}archive/">過去の朝刊を見る</a> ・ <a href="{rel}glossary/">IT用語集</a></p>\n'
    tail = (f'</div>{SHEET}{RBAR}<script type="application/json" id="glossary-data">{gl_json}</script>'
            + issue_data(p, rel, is_current, mins, issue_dates) +
            f'<script src="{asset(rel, "glossary.js")}"></script><script src="{asset(rel, "nav.js")}"></script><script src="{asset(rel, "stamp.js")}"></script>'
            '<a href="#" class="totop" id="totop" hidden aria-label="上へ戻る">▲</a></body>')
    page += foot(rel).replace("</div></body>", tail)
    return page

def archive_rows(posts):
    """Weekdays from the first issue (or CUTOFF) to today. Missing weekdays = 休刊."""
    by_date = {p["date"]: p for p in posts}
    if not posts:
        return []
    first = max(min(date.fromisoformat(p["date"]) for p in posts), CUTOFF)
    rows, d = [], TODAY
    while d >= first:
        key = d.isoformat()
        if key in by_date:
            rows.append((d, by_date[key]))
        elif d.weekday() < 5:
            if d < TODAY or NOW.hour >= CLOSED_AFTER_HOUR:
                rows.append((d, None))
        d -= timedelta(days=1)
    return rows

# ---- v9: full-text search ----
def _plain(fragment):
    t = re.sub(r"<script\b.*?</script>", " ", fragment, flags=re.S)
    t = re.sub(r"</(p|li|dd|dt|h3|ul|dl)>", " ", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = unicodedata.normalize("NFKC", t)
    return re.sub(r"\s+", " ", t).strip()

def search_entries(p):
    """One entry per topic. Ids t1..tN follow the same order as enhance_body()."""
    out = []
    arts = re.findall(r'<article class="topic"([^>]*)>(.*?)</article>', p["body"], re.S)
    for n, (attrs, inner) in enumerate(arts, 1):
        h3 = re.search(r"<h3>(.*?)</h3>", inner, re.S)
        h = _plain(h3.group(1)) if h3 else f"topic {n}"
        body = inner[h3.end():] if h3 else inner
        tag = (re.search(r'data-tag="([^"]*)"', attrs) or [None, ""])[1]
        tp = topics_of(p)
        u = f"t/{tp[n-1]['slug']}.html" if n <= len(tp) else f"posts/{p['slug']}.html#t{n}"
        out.append({"d": p["date"], "u": u, "h": h, "g": tag, "x": _plain(DEEP_RE.sub("", body))})
    w = re.search(r'<section class="words">(.*?)</section>', p["body"], re.S)
    if w:
        out.append({"d": p["date"], "u": f"posts/{p['slug']}.html", "h": "今日のキーワード", "g": "", "x": _plain(re.sub(r"<h2>.*?</h2>", "", w.group(1), flags=re.S))})
    return out

SEARCH_BODY = f"""<h1 class="title"><span class="line sec">記事を検索</span></h1>
<div class="search">
<label for="q" class="sr">検索語</label>
<input id="q" type="search" placeholder="例: Chrome、ニトリ、CVE-2026、漏えい" autocomplete="off" enterkeyhint="search">
<p class="hint">過去1年分の記事から探します。スペースで区切ると、すべての語を含む記事に絞り込みます。</p>
<p class="count" id="count" aria-live="polite"></p>
<ol class="results" id="results"></ol>
</div>
"""

ABOUT_BODY = f"""<h1 class="title"><span class="line sec">このサイトについて</span></h1>
<div id="article">
<section class="about">
<h2>IT朝刊とは</h2>
<p>IT朝刊（アイティーちょうかん／英字表記: it-chokan）は、IT初心者から上級者まで、その日に知るべきITのニュースを、1ページ・5分で確認できるようにまとめたサイトです。平日の毎朝7時ごろに更新します。専門用語はタップで解説が出るほか、<a href="glossary/">用語集</a>でまとめて調べられます。</p>
<h2>記事の選び方</h2>
<p>前の営業日の朝から公表された情報を対象に、話題性・新しさ・重大度の3つで点数を付け、点数の高い順に最大6件を載せます。実際に悪用されている脆弱性や大規模な被害を優先し、情報漏えいの公表は1つの記事にまとめます。</p>
<h2>事実確認と出典</h2>
<p>ベンダーの公式発表、米CISAのKEV、JPCERT/CC・JVN・IPA、被害組織の公表など、一次情報で確認できたことだけを書きます。犯行グループの主張だけの情報は載せません。各記事の末尾に出典のリンクを付けています。</p>
<h2>作り方</h2>
<p>記事の候補探し・要約・用語解説にはAIを使い、上の基準と一次情報の確認ルールに沿って作成しています。誤りに気づいた場合は、記事を訂正して差し替えます。</p>
<h2>掲載期間と休刊</h2>
<p>過去の号はずっと残します。バックナンバーの一覧には直近{KEEP_DAYS}日分を表示し、それより前の号は検索や用語集から読めます。配信トラブルで記事を作れなかった日は、バックナンバーに「休刊」と表示します。</p>
<h2>訂正・ご連絡</h2>
<p>記事の誤りや、掲載内容についてのご連絡は、<a href="https://github.com/it-chokan/it-chokan.github.io/issues">GitHub の Issues</a> からお願いします。確認のうえ、必要に応じて訂正します。</p>
<h2>アクセス解析</h2>
<p>閲覧数の把握のため、Cookieを使わないアクセス解析サービス GoatCounter を利用しています。個人を特定する情報は収集していません。</p>
</section>
</div>
"""

STAMP_JS = r'''(()=>{
const D=document.getElementById('issue-data');if(!D)return;
let I;try{I=JSON.parse(D.textContent)}catch(e){return}
const KEY='itc_stamps_v1';
const load=()=>{try{const a=JSON.parse(localStorage.getItem(KEY)||'[]');return Array.isArray(a)?a.filter(x=>/^\d{4}-\d{2}-\d{2}$/.test(x)):[]}catch(e){return []}};
const save=a=>{try{localStorage.setItem(KEY,JSON.stringify(a))}catch(e){}};
const RANKS=[[0,'新人','rank0'],[10,'記者','rank1'],[20,'デスク','rank2'],[120,'編集長','rank3'],[240,'主筆','rank4']];
const rankOf=n=>{let i=0;RANKS.forEach((r,k)=>{if(n>=r[0])i=k});return [RANKS[i],RANKS[i+1]||null]};
const iso=d=>d.toISOString().slice(0,10);
const today=iso(new Date(Date.now()+9*3600e3));
const add=(s,k)=>{const d=new Date(s+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+k);return iso(d)};
const wd=s=>new Date(s+'T00:00:00Z').getUTCDay();
const issues=new Set(I.issues||[]);
let st=load();
const $=id=>document.getElementById(id);
function paintLogo(){
  const n=st.length,r=rankOf(n)[0];
  const img=$('logoic');if(img&&I.icon){img.src=I.icon+r[2]+'.png';img.alt='称号: '+r[1]}
  const c=$('rankchip');if(c&&n>0){c.textContent=r[1];c.className='rank-chip '+r[2];c.hidden=false}
}
function paintCard(pop){
  const w=$('kdweek');if(!w)return;
  const mon=add(today,-((wd(today)+6)%7));let h='';
  for(let i=0;i<5;i++){
    const d=add(mon,i);let cls='fut',t='';
    if(st.includes(d)){cls='done';t='読了'}
    else if(d===I.date&&I.current){cls='now';t='今日'}
    else if(d<today&&issues.has(d)){cls='miss';t='－'}
    else if(d<today||(d===today&&!issues.has(d)&&!I.current)){cls='off';t='休'}
    h+='<div class="kd-day"><span class="kd-wd">'+'月火水木金'[i]+'</span><span class="kd-st '+cls+(pop===d?' pop':'')+'">'+t+'</span></div>';
  }
  w.innerHTML=h;
  const n=st.length,[r,nx]=rankOf(n);
  $('kdcount').textContent='通算 '+n+'号';
  if(n>0)$('kdrank').innerHTML='称号：<b class="rk '+r[2]+'">'+r[1]+'</b>'+(nx?'　'+nx[1]+'まで あと'+(nx[0]-n)+'号':'　最高位です');
}
function nextText(){
  let d=add(I.date,1);while(wd(d)===0||wd(d)===6)d=add(d,1);
  return (d===add(today,1)?'明日':d===today?'今日':'月火水木金土日'[(wd(d)+6)%7]+'曜')+' 7:00';
}
const nx=$('kdnext');if(nx&&I.current)nx.textContent='次号は '+nextText()+' に届きます';
function stamp(){
  if(!I.current||st.includes(I.date))return;
  const before=rankOf(st.length)[0];
  st=load();if(!st.includes(I.date))st.push(I.date);st.sort();save(st);
  const after=rankOf(st.length)[0];
  paintCard(I.date);paintLogo();
  if(after!==before&&st.length>0){const m=$('kdmsg');if(m)m.textContent='昇進！ 称号「'+after[1]+'」になりました'}
  bar();
}
const k=$('kandoku');let kv=false;
if(k&&'IntersectionObserver'in window){
  new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){stamp();kv=true}else kv=false;bar()}),{threshold:.3}).observe(k);
}
const rb=$('rbar'),fill=$('rbfill'),txt=$('rbtext'),btn=$('rbbtn');
if(btn&&!I.current){btn.textContent='最新号へ';btn.href=I.top}
function bar(){
  if(!rb||!k)return;
  const total=Math.max(1,k.offsetTop-innerHeight*.5),p=Math.min(1,Math.max(0,scrollY/total));
  fill.style.width=(p*100).toFixed(1)+'%';
  const rem=Math.ceil(I.min*(1-p));
  txt.textContent=(I.current&&st.includes(I.date))?'完読済み ✓ 次号は '+nextText():rem>0?'完読まで あと約'+rem+'分':'もうすぐ完読';
  const show=scrollY>320&&!kv;
  if(rb.hidden===show){rb.hidden=!show;document.body.classList.toggle('has-rbar',show)}
}
addEventListener('scroll',bar,{passive:true});addEventListener('resize',bar);
const sb=$('kdshare');
if(sb){sb.hidden=false;sb.addEventListener('click',async()=>{
  const m=$('kdmsg');
  try{if(navigator.share){await navigator.share({title:'IT朝刊',text:I.share,url:I.url});return}}catch(e){if(e&&e.name==='AbortError')return}
  try{await navigator.clipboard.writeText(I.share+'\n'+I.url);if(m)m.textContent='リンクをコピーしました'}catch(e){if(m)m.textContent=I.url}
})}
paintLogo();paintCard();bar();
})();
'''

# rank colors: (background, dots[, burst]) replacing the yellow (and red) of the brand icon
RANK_COLORS = {1: ((122, 190, 255), (84, 160, 240)), 2: ((216, 222, 230), (186, 194, 206)),
               3: ((235, 170, 35), (205, 135, 15)), 4: ((255, 59, 48), (214, 38, 30), (255, 210, 63))}

def make_icons():
    """static/icon/source.png -> _site/static/icon/rank0..4.png (96px, round) + icon-192.png. Needs Pillow; skips if missing."""
    global HAS_ICON
    if not ICON_SRC.exists():
        print("INFO icons: static/icon/source.png not found, text logo is used")
        return
    try:
        import colorsys
        from PIL import Image, ImageDraw
    except Exception as e:
        print(f"WARN icons skipped (Pillow not available): {e}")
        return
    out = OUT / "static" / "icon"
    out.mkdir(parents=True, exist_ok=True)
    src = Image.open(ICON_SRC).convert("RGB")
    if src.width > 400:
        src = src.resize((384, 384), Image.LANCZOS)
    W, H = src.size
    k = W / 1080
    px = list(src.get_flattened_data() if hasattr(src, "get_flattened_data") else src.getdata())
    info = []
    for i, (r, g, b) in enumerate(px):
        h, sat, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        x, y = i % W, i // W
        label = 340 * k < x < 710 * k and 780 * k < y < 945 * k
        kind = "y" if (28 < h * 360 < 62 and sat > .45 and not label) else "r" if ((h * 360 < 14 or h * 360 > 340) and sat > .5) else ""
        info.append((kind, min(1, max(0, (g - 188) / 22)), v))
    def variant(cols):
        if not cols:
            return src
        bg, dot = cols[0], cols[1]
        red = cols[2] if len(cols) > 2 else None
        new = []
        for (r, g, b), (kind, t, v) in zip(px, info):
            if kind == "y":
                new.append(tuple(int((dot[c] * (1 - t) + bg[c] * t) * v) for c in range(3)))
            elif kind == "r" and red:
                new.append(tuple(int(red[c] * v) for c in range(3)))
            else:
                new.append((r, g, b))
        im = Image.new("RGB", (W, H)); im.putdata(new)
        return im
    for n in range(5):
        im = variant(RANK_COLORS.get(n)).resize((384, 384), Image.LANCZOS).convert("RGBA")
        m = Image.new("L", (384, 384), 0); ImageDraw.Draw(m).ellipse([2, 2, 381, 381], fill=255)
        im.putalpha(m)
        im.resize((96, 96), Image.LANCZOS).quantize(64, method=Image.Quantize.FASTOCTREE).save(out / f"rank{n}.png", optimize=True)
    src.resize((192, 192), Image.LANCZOS).quantize(64).save(out / "icon-192.png", optimize=True)
    HAS_ICON = True
    print("icons: rank0-4 + icon-192 generated")

# ---- v14: article pages ----
DEEP_RE = re.compile(r'<section class="deep">.*?</section>', re.S)
_TOPIC_CACHE = {}

def _id_slug(v):
    v = re.sub(r"[^a-z0-9-]+", "-", (v or "").lower()).strip("-")
    return v[:40].strip("-")

def topics_of(p):
    """Topics of an issue in page order: dict(n, slug, attrs, inner, cat, sev, tag, short, head, pn, terms)."""
    if p["slug"] in _TOPIC_CACHE:
        return _TOPIC_CACHE[p["slug"]]
    rx, by = _term_regex()
    out, cat, seen = [], "cat-incident", set()
    tok = re.compile(r'<h2 class="part" id="(cat-[a-z]+)"|<article class="topic"([^>]*)>(.*?)</article>', re.S)
    for m in tok.finditer(p["body"]):
        if m.group(1):
            cat = m.group(1); continue
        attrs, inner = m.group(2), m.group(3)
        n = len(out) + 1
        g = lambda k: (re.search(k + r'="([^"]*)"', attrs) or [None, ""])[1]
        sid = _id_slug(g("data-id"))
        slug = f'{p["slug"]}-{sid}' if sid and sid not in seen else f'{p["slug"]}-{n}'
        seen.add(sid)
        h3 = re.search(r"<h3>(.*?)</h3>", inner, re.S)
        head_t = _plain(h3.group(1)) if h3 else f"topic {n}"
        pn = {html.unescape(x).strip() for x in re.findall(r'<span class="pn">(.*?)</span>', inner)}
        terms = {by[t.group(0)] for t in rx.finditer(_plain(inner))}
        out.append({"n": n, "slug": slug, "attrs": attrs, "inner": inner, "cat": cat, "sev": g("data-sev"),
                    "tag": g("data-tag"), "short": g("data-short") or head_t, "head": head_t, "pn": pn, "terms": terms})
    _TOPIC_CACHE[p["slug"]] = out
    return out

def modified_of(p, default):
    cs = [c.get("at", "") for c in (p.get("corrections") or []) if isinstance(c, dict) and c.get("at")]
    if not cs:
        return default
    t = max(cs).replace(" ", "T")
    return t if "+" in t else t + ":00+09:00" if len(t) == 16 else t

def corrections_html(p, topic=None):
    rows = []
    for c in p.get("corrections") or []:
        if not isinstance(c, dict) or not c.get("note"):
            continue
        cid = str(c.get("id", ""))
        if topic and cid and cid not in (topic["slug"].split(p["slug"] + "-", 1)[-1], f't{topic["n"]}', str(topic["n"])):
            continue
        rows.append(f'<li>{esc(c.get("at", ""))} {esc(c["note"])}</li>')
    return f'<div class="corr"><b>訂正</b><ul>{"".join(rows)}</ul></div>\n' if rows else ""

def follow_links():
    fl = [(k, lab) for k, lab in (("x", "X"), ("threads", "Threads"), ("instagram", "Instagram"), ("facebook", "Facebook"), ("tiktok", "TikTok"), ("bluesky", "Bluesky")) if FOLLOW.get(k)]
    return "".join(f'<a href="{esc(FOLLOW[k])}" rel="noopener" target="_blank">{lab}</a>' for k, lab in fl) + f'<a href="{BASE}feed.xml">RSS</a>'

def share_html(url, text):
    q = quote
    tx = f"{text} #IT朝刊"
    links = [("X", f"https://x.com/intent/post?text={q(tx)}&url={q(url)}"),
             ("LINE", f"https://social-plugins.line.me/lineit/share?url={q(url)}"),
             ("はてブ", f"https://b.hatena.ne.jp/add?mode=confirm&url={q(url)}"),
             ("Threads", f"https://www.threads.net/intent/post?text={q(tx + ' ' + url)}"),
             ("Bluesky", f"https://bsky.app/intent/compose?text={q(tx + ' ' + url)}"),
             ("Facebook", f"https://www.facebook.com/sharer/sharer.php?u={q(url)}")]
    a = "".join(f'<a href="{esc(h)}" rel="noopener" target="_blank">{lab}</a>' for lab, h in links)
    return (f'<section class="share" aria-label="共有"><p class="sh-h">この記事を共有</p><div class="sh-btns">{a}'
            f'<button type="button" id="shcopy" data-url="{esc(url)}">リンクをコピー</button>'
            f'<button type="button" id="shnative" data-url="{esc(url)}" data-text="{esc(tx)}" hidden>その他</button></div>'
            f'<p class="sh-msg" id="shmsg" aria-live="polite"></p></section>\n')

def _sources_for(p, t):
    m = re.search(r'<section class="src">(.*?)</section>', p["body"], re.S)
    if not m:
        return []
    keys = [k for k in t["pn"] if len(k) >= 2]
    lis = re.findall(r"<li>(.*?)</li>", m.group(1), re.S)
    return [li for li in lis if any(k.lower() in _plain(li).lower() for k in keys)]

def _related(t, p, all_topics, k=5):
    sc = []
    for q, tq in all_topics:
        if tq is t:
            continue
        s = 3 * len(t["pn"] & tq["pn"]) + len(t["terms"] & tq["terms"])
        if s >= 3:
            sc.append((s, q["date"], q, tq))
    sc.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return sc[:k]

CAT_JA = {"cat-incident": "インシデント", "cat-tech": "新技術"}

def render_topic(p, t, latest, gl_json, all_topics):
    url = f"{BASE}t/{t['slug']}.html"
    issue_url = f"../posts/{p['slug']}.html"
    first = first_sentence(t["inner"], 120) or p["summary"]
    pub = p["date"] + PUB_TIME
    ld = jsonld({"@context": "https://schema.org", "@type": "NewsArticle", "headline": t["head"][:110],
                 "description": first, "datePublished": pub, "dateModified": modified_of(p, pub), "inLanguage": "ja",
                 "mainEntityOfPage": url, "url": url, "image": [f"{BASE}static/ogp.png"],
                 "author": {"@type": "Organization", "name": cfg["site_title"], "url": BASE}, "publisher": PUBLISHER,
                 "articleSection": CAT_JA.get(t["cat"], ""), "keywords": sorted(t["pn"] | t["terms"])[:20],
                 "isPartOf": {"@type": "CreativeWork", "name": f"{cfg['site_title']} {jdate(p['date'])}号", "url": f"{BASE}posts/{p['slug']}.html"}}) + jsonld({
                 "@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": cfg["site_title"], "item": BASE},
                    {"@type": "ListItem", "position": 2, "name": f"{jdate(p['date'])}号", "item": f"{BASE}posts/{p['slug']}.html"},
                    {"@type": "ListItem", "position": 3, "name": t["short"], "item": url}]})
    page = head(f"{t['head']}｜{cfg['site_title']}", first, url, "../", "article", extra=ld)
    if latest and latest["date"] != p["date"]:
        page += (f'<a class="latest-band" href="../">これは{jdate(p["date"])}の記事です。'
                 f'<b>最新号（{jdate(latest["date"])}）はこちら →</b></a>\n')
    sev = '<span class="tp-sev">ヤバい</span>' if t["sev"] == "high" else ""
    page += (f'<p class="crumb"><a href="../">{esc(cfg["site_title"])}</a> › <a href="{issue_url}">{jdate(p["date"])}号</a></p>\n'
             f'<p class="tp-meta">{sev}<span class="tp-cat">{CAT_JA.get(t["cat"], "")}</span>'
             + (f'<span class="tp-tag">{esc(t["tag"])}</span>' if t["tag"] else "") + f'<span class="tp-date">{jdate(p["date"])} 7:00</span></p>\n')
    page += corrections_html(p, t)
    inner = re.sub(r"<header>.*?</header>", "", t["inner"], count=1, flags=re.S)
    inner = re.sub(r'<dl class="facts">(.*?)</dl>', r'<details class="more" open><summary>詳細（対象・バージョン）</summary><dl class="facts">\1</dl></details>', inner, flags=re.S)
    inner = inner.replace('<section class="deep">', '<section class="deep"><p class="deep-h">くわしく：対象かどうか・やること</p>', 1)
    page += f'<div id="article"><article class="topic tp-page" id="t1"{t["attrs"]}>\n<h1 class="tp-h1">{esc(t["head"])}</h1>\n{inner}</article>\n'
    srcs = _sources_for(p, t)
    page += ('<section class="src"><h2>この記事の出典</h2><ul>' + "".join(f"<li>{s}</li>" for s in srcs) + "</ul>"
             f'<p class="src-more"><a href="{issue_url}">この号の出典をすべて見る</a></p></section>\n') if srcs else \
            f'<section class="src"><h2>出典</h2><p><a href="{issue_url}">この記事が載った号の出典一覧を見る</a></p></section>\n'
    page += "</div>\n"
    page += share_html(url, t["head"])
    rel = _related(t, p, all_topics)
    if rel:
        page += ('<section class="rel"><h2>関連する記事</h2><ul class="archive">' + "".join(
            f'<li><a href="{tq["slug"]}.html"><span class="ad">{jdate(q["date"])}</span><span class="at">{esc(tq["head"])}</span></a></li>'
            for _, _, q, tq in rel) + "</ul></section>\n")
    page += '<section class="gl"><h2>この記事の用語</h2><ul id="gllist"></ul><p class="gl-more"><a href="../glossary/">用語集をすべて見る</a></p></section>\n'
    mins = read_minutes(DEEP_RE.sub("", p["body"]))
    page += (f'<section class="fbox" aria-label="毎朝読む"><p class="fb-h">ITニュースは、毎朝5分で。</p>'
             f'<p class="fb-s">IT朝刊は平日の朝7時に、その日に知るべきセキュリティ事件と新技術を、用語解説つきで届けます。</p>'
             f'<div class="fb-cta"><a class="fb-main" href="{issue_url}">この記事が載った号を読む<small>{jdate(p["date"])}・{mins}分</small></a>'
             f'<a class="fb-main alt" href="../">今日の朝刊を読む<small>毎朝7時に更新</small></a></div>'
             f'<p class="kd-follow-h">明日の朝刊も受け取る</p><div class="kd-follow">{follow_links()}</div>'
             f'<p class="kd-bm">ブックマークは <a href="{BASE}">トップページ</a> へ。いつ開いても最新号が読めます。</p>'
             '<details class="kd-home"><summary>ホーム画面に追加する方法</summary><p>iPhone（Safari）: 共有ボタン → 「ホーム画面に追加」<br>Android（Chrome）: 右上の︙ → 「ホーム画面に追加」</p></details>'
             '</section>\n')
    tail = (f'</div>{SHEET}<script type="application/json" id="glossary-data">{gl_json}</script>'
            f'<script src="{asset("../", "glossary.js")}"></script><script src="{asset("../", "topic.js")}"></script>'
            '<a href="#" class="totop" id="totop" hidden aria-label="上へ戻る">▲</a></body>')
    page += foot("../").replace("</div></body>", tail)
    return page

def build_topics(posts, latest, gl_json):
    tdir = OUT / "t"
    tdir.mkdir()
    all_topics = [(p, t) for p in posts for t in topics_of(p)]
    urls = []
    for p, t in all_topics:
        (tdir / f"{t['slug']}.html").write_text(render_topic(p, t, latest, gl_json, all_topics), encoding="utf-8")
        urls.append((f"{BASE}t/{t['slug']}.html", modified_of(p, p["date"])[:10]))
    live = {t["slug"] for _, t in all_topics}
    n_w = 0
    for p in posts:
        for w in p.get("withdrawn") or []:
            wid = _id_slug(str(w.get("id", ""))) if isinstance(w, dict) else ""
            if not wid:
                continue
            slug = f'{p["slug"]}-{wid}'
            if slug in live:
                continue
            page = head(f"この記事は取り下げました｜{cfg['site_title']}", cfg["site_description"], "", "../", "website", robots="noindex")
            reason = esc(w.get("note", "")) if isinstance(w, dict) else ""
            page += (f'<section class="nf"><h1 class="title"><span class="line sec">この記事は取り下げました</span></h1>'
                     f'<p>{esc(w.get("title", "")) if isinstance(w, dict) else ""}</p><p>{reason}</p>'
                     f'<a class="nf-card" href="{BASE}"><span class="go">今日の朝刊を読む →</span></a>'
                     f'<p><a href="../posts/{p["slug"]}.html">{jdate(p["date"])}号を読む</a></p></section>\n' + foot("../"))
            (tdir / f"{slug}.html").write_text(page, encoding="utf-8")
            n_w += 1
    print(f"articles: {len(all_topics)} pages, {n_w} withdrawn stubs")
    return urls

TOPIC_JS = r"""(()=>{
const c=document.getElementById('shcopy'),n=document.getElementById('shnative'),m=document.getElementById('shmsg');
const say=t=>{if(m){m.textContent=t;setTimeout(()=>{m.textContent=''},2500)}};
if(c)c.addEventListener('click',()=>{const u=c.dataset.url;
 (navigator.clipboard?navigator.clipboard.writeText(u):Promise.reject()).then(()=>say('リンクをコピーしました')).catch(()=>{prompt('URL',u)})});
if(n&&navigator.share){n.hidden=false;n.addEventListener('click',()=>{navigator.share({title:document.title,text:n.dataset.text,url:n.dataset.url}).catch(()=>{})})}
const tt=document.getElementById('totop');if(tt){addEventListener('scroll',()=>{tt.hidden=scrollY<600},{passive:true})}
})();
"""

# ---- v13: glossary pages ----
PUBLISHER = {"@type": "Organization", "name": cfg["site_title"], "alternateName": "it-chokan", "url": BASE,
             "logo": {"@type": "ImageObject", "url": f"{BASE}static/icon/icon-192.png"}}
TERM_URL = {}   # term -> file slug (unquoted)

def term_slugs():
    out, seen = {}, set()
    for t in glossary["terms"]:
        s = unicodedata.normalize("NFKC", t["term"]).strip().lower()
        s = s.replace("+", "plus").replace("#", "sharp")
        s = re.sub(r"[^0-9a-z぀-ヿ㐀-鿿._-]+", "-", s).strip("-.") or "term"
        base, i = s, 2
        while s in seen:
            s = f"{base}-{i}"; i += 1
        seen.add(s); out[t["term"]] = s
    return out

def _term_regex():
    by = {}
    for t in glossary["terms"]:
        for k in [t["term"], *(t.get("aliases") or [])]:
            if k:
                by.setdefault(unicodedata.normalize("NFKC", k), t["term"])
    keys = sorted(by, key=len, reverse=True)
    pat = "|".join((r"(?<![A-Za-z0-9-])" + re.escape(k) + r"(?![A-Za-z0-9-])") if re.fullmatch(r"[\x20-\x7e]+", k) else re.escape(k) for k in keys)
    return re.compile(pat), by

def term_usage(posts):
    """term -> [(date, url, headline)] newest first. Same longest-first matching as glossary.js."""
    rx, by = _term_regex()
    use = {}
    for p in posts:
        arts = re.findall(r'<article class="topic"([^>]*)>(.*?)</article>', p["body"], re.S)
        for n, (_, inner) in enumerate(arts, 1):
            h3 = re.search(r"<h3>(.*?)</h3>", inner, re.S)
            h = _plain(h3.group(1)) if h3 else f"topic {n}"
            for term in {by[m.group(0)] for m in rx.finditer(_plain(inner))}:
                tp = topics_of(p)
                u = f"../t/{tp[n-1]['slug']}.html" if n <= len(tp) else f"../posts/{p['slug']}.html#t{n}"
                use.setdefault(term, []).append((p["date"], u, h))
    return use

def build_glossary(posts):
    gdir = OUT / "glossary"
    gdir.mkdir()
    use = term_usage(posts)
    full_map = {}
    fp = ROOT / "static" / "glossary_full.json"
    if fp.exists():
        try: full_map = json.loads(fp.read_text(encoding="utf-8-sig"))
        except ValueError: pass
    set_ld = {"@type": "DefinedTermSet", "name": f"{cfg['site_title']} 用語集", "url": f"{BASE}glossary/"}
    urls, rows = [], []
    for t in glossary["terms"]:
        term, slug = t["term"], TERM_URL[t["term"]]
        url = f"{BASE}glossary/{quote(slug)}.html"
        full = t.get("full") or full_map.get(term, "")
        aliases = [a for a in (t.get("aliases") or []) if a and a != full]
        hits = use.get(term, [])
        name = f"{term}（{full}）" if full else term
        desc = f"{name}とは、{t['short']}のこと。{t['long']}"
        desc = desc if len(desc) <= 120 else desc[:119] + "…"
        ld = jsonld({"@context": "https://schema.org", "@type": "DefinedTerm", "name": term,
                     "alternateName": [x for x in [full, *aliases] if x], "description": t["long"],
                     "url": url, "inDefinedTermSet": set_ld}) + jsonld({
                     "@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": cfg["site_title"], "item": BASE},
                        {"@type": "ListItem", "position": 2, "name": "用語集", "item": f"{BASE}glossary/"},
                        {"@type": "ListItem", "position": 3, "name": term, "item": url}]})
        # terms not used in any article yet: reachable from tooltips, but kept out of search results (thin page)
        page = head(f"{term}とは？意味をやさしく解説｜{cfg['site_title']} 用語集", desc, url, "../", "article", extra=ld,
                    robots="" if hits else "noindex,follow")
        page += f'<p class="crumb"><a href="../">{esc(cfg["site_title"])}</a> › <a href="./">用語集</a></p>\n'
        page += f'<h1 class="title"><span class="line sec">{esc(term)}とは</span></h1>\n'
        page += f'<p class="lead">{esc(t["short"])}</p>\n<div id="article"><section class="about">\n'
        if full:
            page += f'<p class="gl-full">正式名称: {esc(full)}</p>\n'
        page += f'<h2>かんたんに言うと</h2><p>{esc(name)}は、{esc(t["short"])}のことです。</p>\n'
        page += f'<h2>もう少しくわしく</h2><p>{esc(t["long"])}</p>\n'
        if aliases:
            page += f'<p class="gl-alias">別の呼び方・表記: {esc("、".join(aliases))}</p>\n'
        page += f'<h2>IT朝刊で「{esc(term)}」が出てきた記事</h2>\n'
        if hits:
            page += '<ul class="archive">' + "".join(
                f'<li><a href="{u}"><span class="ad">{jdate(d)}</span><span class="at">{esc(h)}</span></a></li>'
                for d, u, h in hits[:30]) + "</ul>\n"
        else:
            page += "<p>まだ記事はありません。</p>\n"
        page += (f'<p class="more-issues"><a href="../">今日の朝刊を読む</a> ・ <a href="./">用語集の一覧へ</a></p>\n'
                 "</section></div>\n" + foot("../"))
        (gdir / f"{slug}.html").write_text(page, encoding="utf-8")
        if hits:
            urls.append((url, hits[0][0]))
        rows.append((term, slug, t["short"], len(hits), " ".join(x for x in [term, full, *aliases, t["short"]] if x)))
    # index
    asc = sorted([r for r in rows if re.match(r"[0-9A-Za-z]", r[0])], key=lambda r: r[0].lower())
    jp = sorted([r for r in rows if not re.match(r"[0-9A-Za-z]", r[0])], key=lambda r: r[0])
    def ul(rs):
        return '<ul class="archive">' + "".join(
            f'<li data-k="{esc(k)}"><a href="{quote(s)}.html"><span class="ad">{esc(term)}</span><span class="at">{esc(short)}</span></a></li>'
            for term, s, short, _, k in rs) + "</ul>\n"
    ld = jsonld(dict(set_ld, **{"@context": "https://schema.org", "hasDefinedTerm": [
        {"@type": "DefinedTerm", "name": term, "url": f"{BASE}glossary/{quote(s)}.html"} for term, s, _, _, _ in rows]}))
    page = head(f"IT用語集（{len(rows)}語）｜{cfg['site_title']}",
                f"セキュリティ・新技術のニュースに出てくるIT用語{len(rows)}語を、新人エンジニアにもわかる一言でやさしく解説。CVE・KEV・ランサムウェアなど。",
                f"{BASE}glossary/", "../", "website", extra=ld)
    page += (f'<h1 class="title"><span class="line sec">IT用語集</span></h1>\n'
             f'<p class="lead">IT朝刊の記事に出てきた用語{len(rows)}語を、ひと言でやさしく解説します。</p>\n'
             '<div class="search gsearch">\n<label for="gq" class="sr">用語を検索</label>\n'
             '<input id="gq" type="search" placeholder="例: KEV、ランサム、ぜいじゃく、認証" autocomplete="off" enterkeyhint="search">\n'
             '<p class="hint">用語名・正式名称・別名・ひと言解説から探します。ひらがな・カタカナ、全角・半角は区別しません。</p>\n'
             '<p class="count" id="gcount" aria-live="polite"></p>\n</div>\n'
             f'<h2 class="part">英数字</h2>\n{ul(asc)}<h2 class="part">日本語</h2>\n{ul(jp)}'
             '<p class="gnone" id="gnone" hidden></p>\n'
             f'<script src="{asset("../", "glossary_search.js")}"></script>\n' + foot("../"))
    (gdir / "index.html").write_text(page, encoding="utf-8")
    print(f"glossary: {len(rows)} terms, {sum(1 for r in rows if r[3])} with articles")
    return urls

def manifest():
    icons = ([{"src": f"{BASE}static/icon/icon-192.png", "sizes": "192x192", "type": "image/png"}] if HAS_ICON else [])
    return json.dumps({"name": cfg["site_title"], "short_name": cfg["site_title"], "start_url": BASE, "scope": BASE,
                       "display": "standalone", "background_color": "#eaf7ff", "theme_color": "#FDD446",
                       "lang": "ja", "icons": icons}, ensure_ascii=False)

def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "posts").mkdir(parents=True)
    shutil.copytree(ROOT / "static", OUT / "static")
    shutil.copy(ROOT / "glossary.json", OUT / "glossary.json")
    if SHOW_COUNTER:
        (OUT / "static" / "counter.js").write_text(COUNTER_JS % GC, encoding="utf-8")
    (OUT / "static" / "stamp.js").write_text(STAMP_JS, encoding="utf-8")
    make_icons()
    (OUT / "manifest.webmanifest").write_text(manifest(), encoding="utf-8")
    for f in (OUT / "static").iterdir():
        if f.is_file() and f.suffix in (".css", ".js"):
            VER[f.name] = hashlib.sha1(f.read_bytes()).hexdigest()[:8]
    posts = load_posts()
    TERM_URL.update(term_slugs())
    gl_out = dict(glossary, terms=[dict(t, u=f"{BASE}glossary/{quote(TERM_URL[t['term']])}.html") for t in glossary["terms"]])
    gl_json = json.dumps(gl_out, ensure_ascii=False).replace("</", "<\\/")
    latest = posts[0] if posts else None
    issue_dates = sorted(p["date"] for p in posts)

    # issues
    for p in posts:
        url = f"{BASE}posts/{p['slug']}.html"
        (OUT / "posts" / f"{p['slug']}.html").write_text(render_issue(p, "../", url, gl_json, latest=latest, issue_dates=issue_dates), encoding="utf-8")

    # top = latest issue in full
    site_ld = jsonld({"@context": "https://schema.org", "@type": "WebSite", "name": cfg["site_title"],
                      "alternateName": ["it-chokan", "IT朝刊（it-chokan）", "アイティーちょうかん"], "url": BASE,
                      "inLanguage": "ja", "description": cfg["site_description"],
                      "publisher": PUBLISHER})
    if latest:
        page = render_issue(latest, "", BASE, gl_json, is_top=True, extra_head=site_ld, latest=latest, issue_dates=issue_dates)
    else:
        page = head(cfg["site_title"], cfg["site_description"], BASE, "", "website", extra=site_ld)
        page += f'<p class="lead">{esc(cfg["site_description"])}</p><p>最初の朝刊を準備中です。</p>\n' + foot("")
    (OUT / "index.html").write_text(page, encoding="utf-8")

    # archive
    rows = archive_rows(posts)
    page = head(f"バックナンバー｜{cfg['site_title']}", f"{cfg['site_title']}の直近{KEEP_DAYS}日分の朝刊一覧。", f"{BASE}archive/", "../", "website")
    page += f'<h1 class="title"><span class="line sec">バックナンバー</span></h1>\n<p class="lead">直近{KEEP_DAYS}日分の朝刊です。それより前の号は、<a href="../search/">検索</a>や<a href="../glossary/">用語集</a>から読めます。</p>\n<ul class="archive">\n'
    month = None
    for d, p in rows:
        if (d.year, d.month) != month:
            month = (d.year, d.month)
            page += f'<li class="month">{d.year}年{d.month}月</li>\n'
        if p:
            badge = '<span class="rc">遅れて掲載</span>' if p.get("recovered") else ""
            page += (f'<li><a href="../posts/{p["slug"]}.html"><span class="ad">{jdate(d)}</span>{badge}'
                     f'<span class="at">{title_html(p["title"], p["slug"])}</span></a></li>\n')
        else:
            page += f'<li class="off"><span class="ad">{jdate(d)}</span><span class="at">休刊</span></li>\n'
    page += "</ul>\n" + foot("../")
    (OUT / "archive").mkdir()
    (OUT / "archive" / "index.html").write_text(page, encoding="utf-8")

    # /latest/ : kept for links already shared (Threads profile). v10: goes to the top page (= latest issue)
    (OUT / "latest").mkdir()
    target = "../"
    (OUT / "latest" / "index.html").write_text(
        f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="robots" content="noindex">'
        f'<meta http-equiv="refresh" content="0; url={target}"><link rel="canonical" href="{BASE}">'
        f'<title>{esc(cfg["site_title"])}</title></head><body><a href="{target}">最新の朝刊へ</a></body></html>',
        encoding="utf-8")

    # 404 : turn dead links (incl. issues past 90 days) into a way to today's issue
    page = head(f"ページが見つかりません｜{cfg['site_title']}", cfg["site_description"], "", BASE, "website", robots="noindex")
    page += '<section class="nf"><h1 class="title"><span class="line sec">ページが見つかりません</span></h1>'
    page += f'<p>お探しのページは、URLが変わったか削除された可能性があります。</p>'
    if latest:
        page += (f'<a class="nf-card" href="{BASE}"><span class="ad">{jdate(latest["date"])}の朝刊</span>'
                 f'<span class="at">{title_html(latest["title"])}</span><span class="go">今日の朝刊を読む →</span></a>')
    page += f'<p><a href="{BASE}archive/">バックナンバーを見る</a></p></section>\n' + foot(BASE)
    (OUT / "404.html").write_text(page, encoding="utf-8")

    # search (v9)
    s_cut = (TODAY - timedelta(days=SEARCH_DAYS - 1)).isoformat()
    entries = [e for p in posts if p["date"] >= s_cut for e in search_entries(p)]
    (OUT / "search.json").write_text(json.dumps(entries, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "search").mkdir()
    page = head(f"記事を検索｜{cfg['site_title']}", f"{cfg['site_title']}の過去1年分の記事を全文検索。", f"{BASE}search/", "../", "website")
    page += SEARCH_BODY + f'<script src="{asset("../", "search.js")}"></script>\n' + foot("../")
    (OUT / "search" / "index.html").write_text(page, encoding="utf-8")

    # about
    page = head(f"このサイトについて｜{cfg['site_title']}", "IT朝刊の記事の選び方、事実確認と出典の方針。", f"{BASE}about.html", "", "website")
    page += ABOUT_BODY + foot("")
    (OUT / "about.html").write_text(page, encoding="utf-8")

    # RSS
    items = "".join(
        f"<item><title>{esc(title_plain(p['title']))}</title><link>{BASE}posts/{p['slug']}.html</link><guid>{BASE}posts/{p['slug']}.html</guid><pubDate>{p['date']}</pubDate><description>{esc(p['summary'])}</description></item>"
        for p in posts[:30])
    (OUT / "feed.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{esc(cfg["site_title"])}</title><link>{BASE}</link><description>{esc(cfg["site_description"])}</description>{items}</channel></rss>',
        encoding="utf-8")

    # article pages (v14)
    (OUT / "static" / "topic.js").write_text(TOPIC_JS, encoding="utf-8")
    VER["topic.js"] = hashlib.sha1(TOPIC_JS.encode()).hexdigest()[:8]
    tp_urls = build_topics(posts, latest, gl_json)

    # glossary (v13)
    gl_urls = build_glossary(posts)

    # sitemap + robots
    ld = latest["date"] if latest else ""
    urls = [(BASE, ld), (f"{BASE}archive/", ld), (f"{BASE}search/", ld), (f"{BASE}about.html", ""), (f"{BASE}glossary/", ld)] + [(f"{BASE}posts/{p['slug']}.html", p["date"]) for p in posts] + tp_urls + gl_urls
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u, d in urls:
        sm += f"<url><loc>{u}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>\n"
    sm += "</urlset>\n"
    (OUT / "sitemap.xml").write_text(sm, encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nDisallow: /latest/\nSitemap: {BASE}sitemap.xml\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("")
    # IndexNow (v15): key file + list of recently changed URLs (the workflow posts it after deploy)
    if INDEXNOW_KEY:
        (OUT / f"{INDEXNOW_KEY}.txt").write_text(INDEXNOW_KEY, encoding="utf-8")
        recent = (TODAY - timedelta(days=1)).isoformat()
        fresh = [u for u, d in urls if d and d[:10] >= recent]
        ROOT.joinpath("_indexnow").mkdir(exist_ok=True)
        ROOT.joinpath("_indexnow", "urls.json").write_text(json.dumps(
            {"host": BASE.split("/")[2], "key": INDEXNOW_KEY, "keyLocation": f"{BASE}{INDEXNOW_KEY}.txt",
             "urlList": fresh[:10000]}, ensure_ascii=False), encoding="utf-8")
        print(f"indexnow: {len(fresh)} urls")
    closed = sum(1 for _, p in rows if p is None)
    print(f"search: {len(entries)} entries, {(OUT / 'search.json').stat().st_size} bytes")
    print(f"built {len(posts)} posts, {closed} closed days -> {OUT} (base {BASE}) latest={ld or '-'} "
          f"gsv={'on' if GSV else 'off'} goatcounter={'on' if GC else 'off'} counter={'on' if SHOW_COUNTER else 'off'}")

if __name__ == "__main__":
    build()
