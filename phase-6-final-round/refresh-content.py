#!/usr/bin/env python3
"""Refresh index.html's captured University News content from the live site.

Prototype-only helper. Run: python3 refresh-content.py  (from this directory)

Pulls the current category feed and one feed per subtopic tag from
news.northeastern.edu's REST API, inlines the images, and rewrites the
captured (static fallback) sections of index.html in place: the featured
block, The Latest river, the subtopic bands, and the captured grid pool
behind the subtopic views. Most Read and Seen around campus are curated
by hand and are left alone.
"""
import base64
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "https://news.northeastern.edu/wp-json/wp/v2"
CATEGORY = 7  # University News
NOW = datetime.now()

# One entry per subtopic in the nav, in display order. A topic may pool
# several tags (Commencement); the first slug is where "More X" points.
TOPICS = [
    ("Global Network", ["global-network"]),
    ("Co-op", ["co-op"]),
    ("Experiential Learning", ["experiential-learning"]),
    ("Commencement", ["commencement", "commencement-2026", "graduation", "graduates"]),
    ("Awards", ["awards"]),
]

HERE = Path(__file__).parent
PAGE = HERE / "index.html"


def get(path, **params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{BASE}/{path}?{qs}", headers={"User-Agent": "ngn-prototype-refresh"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def esc(markup):
    return html.escape(html.unescape(re.sub(r"<[^>]+>", "", markup or "")).strip(), quote=False)


_img_cache = {}


def img_uri(post):
    med = (post.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0] or {}
    sizes = med.get("media_details", {}).get("sizes", {})
    url = None
    for s in ("medium_large", "large", "medium"):
        if s in sizes:
            url = sizes[s]["source_url"]
            break
    url = url or med.get("source_url")
    if not url:
        return None
    if url not in _img_cache:
        req = urllib.request.Request(url, headers={"User-Agent": "ngn-prototype-refresh"})
        with urllib.request.urlopen(req) as r:
            mime = r.headers.get_content_type()
            _img_cache[url] = f"data:{mime};base64," + base64.b64encode(r.read()).decode()
    return _img_cache[url]


def long_date(iso):
    return datetime.fromisoformat(iso).strftime("%B %-d, %Y")


def rel_date(iso):
    d = datetime.fromisoformat(iso)
    hrs = (NOW - d).total_seconds() / 3600
    if hrs < 1:
        return "Just now"
    if hrs < 24:
        return f"{int(hrs)} hours ago"
    days = round(hrs / 24)
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return long_date(iso)


def story_bits(p):
    return {
        "url": p["link"],
        "title": esc(p["title"]["rendered"]),
        "blurb": esc(p["excerpt"]["rendered"]),
        "img": img_uri(p),
        "iso": p["date"],
        "author": esc((p.get("_embedded", {}).get("author") or [{}])[0].get("name", "")),
    }


def lead_html(s):
    return (
        f'    <article class="story featured-bi__lead"><a href="{s["url"]}">\n'
        f'      <div class="media-frame"><img src="{s["img"]}" alt=""></div>\n'
        f'      <div class="lead-box"><h3 class="headline">{s["title"]}</h3>\n'
        f'        <p class="blurb">{s["blurb"]}</p>\n'
        f'        <div class="post-meta"><time>{long_date(s["iso"])}</time></div></div>\n'
        f'    </a></article>'
    )


def top_html(s):
    return (
        f'      <article class="story story--top"><a href="{s["url"]}">\n'
        f'        <div class="media-frame"><img src="{s["img"]}" alt=""></div>\n'
        f'        <h3 class="headline">{s["title"]}</h3>\n'
        f'        <div class="post-meta"><time>{long_date(s["iso"])}</time></div>\n'
        f'      </a></article>'
    )


def thumb_html(s):
    return (
        f'<article class="story story--thumb-row"><a href="{s["url"]}">'
        f'<div class="story__text"><h3 class="headline">{s["title"]}</h3>'
        f'<div class="post-meta"><time>{long_date(s["iso"])}</time></div></div>'
        f'<div class="media-frame"><img src="{s["img"]}" alt=""></div></a></article>'
    )


def river_html(s):
    return (
        f'<article class="story is-mode-rich"><a href="{s["url"]}">'
        f'<div class="story--rich"><div class="story__text"><h3 class="headline">{s["title"]}</h3>'
        f'<p class="blurb">{s["blurb"]}</p>'
        f'<div class="post-meta"><time>{rel_date(s["iso"])}</time></div></div>'
        f'<div class="media-frame" style=""><img src="{s["img"]}" alt="" style="aspect-ratio:3 / 2"></div>'
        f'</div></a></article>'
    )


def band_story_html(s):
    return (
        f'<article class="story is-mode-stacked"><a href="{s["url"]}">'
        f'<div class="media-frame" style=""><img src="{s["img"]}" alt="" style="aspect-ratio:3 / 2"></div>'
        f'<div class="story__text"><h3 class="headline" style="font-size:17px">{s["title"]}</h3>'
        f'<div class="post-meta"><time>{long_date(s["iso"])}</time></div></div></a></article>'
    )


def band_html(name, slug, stories):
    return (
        f'  <section class="topic-group">\n'
        f'    <div class="section-label"><h2 class="section-label__title">{name}</h2></div>\n'
        f'    <div class="band-grid">\n'
        f'      {"".join(band_story_html(s) for s in stories)}\n'
        f'    </div>\n'
        f'    <a class="more-btn" href="https://news.northeastern.edu/tag/{slug}/" data-topic="{name}">More {name}</a>\n'
        f'  </section>'
    )


def replace_once(s, old, new, label):
    n = s.count(old)
    assert n == 1, f"{label}: expected 1 occurrence, found {n}"
    return s.replace(old, new)


def main():
    # Tag slugs -> numeric IDs. The posts endpoint ignores slug filters, so
    # every tag has to be resolved to its ID first.
    all_slugs = [slug for _, slugs in TOPICS for slug in slugs]
    tags = {t["slug"]: t["id"] for t in get("tags", slug=",".join(all_slugs), per_page=50)}
    missing = [s for s in all_slugs if s not in tags]
    assert not missing, f"tags not found on the live site: {missing}"

    print("pulling category feed…")
    feed = [p for p in get("posts", categories=CATEGORY, per_page=26, _embed="author,wp:featuredmedia")
            if p.get("link") and p.get("title")]
    assert len(feed) >= 22, f"category feed too small: {len(feed)}"
    stories = [story_bits(p) for p in feed]
    stories = [s for s in stories if s["img"]]

    bands = []
    for name, slugs in TOPICS:
        ids = ",".join(str(tags[s]) for s in slugs)
        print(f"pulling {name}…")
        posts = [p for p in get("posts", tags=ids, per_page=6, _embed="author,wp:featuredmedia")]
        band = [s for s in (story_bits(p) for p in posts) if s["img"]][:4]
        assert len(band) == 4, f"{name}: only {len(band)} usable stories"
        bands.append((name, slugs[0], ids, band))

    page = PAGE.read_text(encoding="utf-8")

    # Featured block: lead + rail (one top story, two thumb rows).
    featured = (
        '<section class="featured-bi">\n'
        '  <div class="wrapper wrapper--wide featured-bi__grid">\n'
        + lead_html(stories[0]) + '\n'
        + '    <div class="featured-bi__rail">\n'
        + top_html(stories[1]) + '\n'
        + '      ' + ''.join(thumb_html(s) for s in stories[2:4]) + '\n'
        + '    </div>\n'
        + '  </div>\n'
        + '</section>'
    )
    m = re.search(r'<section class="featured-bi">.*?\n</section>', page, re.S)
    assert m, "featured block not found"
    page = page[:m.start()] + featured + page[m.end():]

    # The Latest river: six rich stories.
    river_start = page.index('<h2 class="river-label">The Latest</h2>')
    river_end = page.index('\n  </div>', river_start)
    page = (page[:river_start]
            + '<h2 class="river-label">The Latest</h2>\n    '
            + ''.join(river_html(s) for s in stories[4:10])
            + page[river_end:])

    # Subtopic bands: one section per nav topic.
    bands_start = page.index('  <section class="topic-group">')
    bands_end = page.index('\n</div>\n<script>', bands_start)
    page = (page[:bands_start]
            + '\n'.join(band_html(name, slug, band) for name, slug, _, band in bands)
            + page[bands_end:])

    # Nav links, tag map, keyword fallback, captured grid pool.
    nav = ''.join(f'<a href="#">{name}</a>' for name, _ in TOPICS)
    page = replace_once(
        page,
        re.search(r'<div class="topic-line cat-bar__links">\n      (<a href="#">.*?)\n', page).group(1),
        nav, "topic-line nav")

    tag_map = ',\n'.join(f"    '{name}': '{ids}'" for name, _, ids, _ in bands)
    m = re.search(r'const TOPIC_TAGS = \{.*?\};', page, re.S)
    assert m, "TOPIC_TAGS not found"
    page = page[:m.start()] + 'const TOPIC_TAGS = {\n' + tag_map + ',\n  };' + page[m.end():]

    m = re.search(r"const KW = \{[^\n]*\};", page)
    assert m, "KW not found"
    page = page[:m.start()] + "const KW = { 'Commencement': ['commencement','graduate','grad '] };" + page[m.end():]

    grid = [{"t": s["title"], "u": s["url"], "a": s["author"], "d": s["iso"][:10], "img": s["img"]}
            for s in stories[10:22]]
    m = re.search(r'var TV_GRID = \[[^\n]*\];', page)
    assert m, "TV_GRID not found"
    page = page[:m.start()] + 'var TV_GRID = ' + json.dumps(grid) + ';' + page[m.end():]

    PAGE.write_text(page, encoding="utf-8")
    print(f"wrote {PAGE.name} ({len(page) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
