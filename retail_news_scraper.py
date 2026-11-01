#!/usr/bin/env python3
"""
零售產業週報爬蟲 - Retail Industry Weekly News Scraper
收集過去7天來自各主要來源的零售相關新聞
"""

import os
import re
import time
import logging
import calendar
import urllib.parse
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime

# ==================== 設定 ====================

DAYS_BACK = 7
OUTPUT_DIR = Path(__file__).parent / "reports"
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,ja;q=0.8,en;q=0.7",
    # Omit Accept-Encoding — let requests auto-negotiate gzip/deflate.
    # Explicitly advertising 'br' without the brotli package installed causes
    # servers to send Brotli-compressed responses that requests can't decode.
}

# ==================== 日誌 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ==================== 工具函數 ====================

def get_cutoff_date():
    return datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)


def parse_date(date_input):
    """Parse various date formats → UTC-aware datetime"""
    if date_input is None:
        return None
    try:
        if hasattr(date_input, "tm_year"):            # feedparser time.struct_time
            ts = calendar.timegm(date_input)
            return datetime.fromtimestamp(ts, tz=timezone.utc)

        if isinstance(date_input, str):
            s = date_input.strip()
            try:
                return parsedate_to_datetime(s)       # RFC 2822
            except Exception:
                pass
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
            # 2024年01月15日 / 2024/01/15 / 2024.01.15
            m = re.search(r"(\d{4})[/\-\.年](\d{1,2})[/\-\.月](\d{1,2})", s)
            if m:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                tzinfo=timezone.utc)
    except Exception as e:
        logger.debug(f"Date parse error: {e} | {date_input!r}")
    return None


def is_recent(date_input):
    dt = parse_date(date_input)
    return True if dt is None else dt >= get_cutoff_date()


def fetch_page(url, encoding=None, timeout=15):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        if encoding:
            resp.encoding = encoding
        return resp.text
    except requests.RequestException as e:
        logger.warning(f"Fetch failed [{url}]: {e}")
        return None


def fetch_rss(url):
    """Fetch RSS via requests first, then parse — avoids feedparser's own HTTP issues."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        return feed
    except Exception as e:
        logger.warning(f"RSS failed [{url}]: {e}")
        return None


def make_article(title, url, date, source, summary=""):
    dt = parse_date(date)
    return {
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "date": date,
        "date_str": dt.strftime("%Y-%m-%d") if dt else "—",
        "source": source,
        "summary": (summary or "").strip()[:300],
    }


def rss_to_articles(feed_url, source_name, max_items=80):
    """Generic RSS → article list."""
    articles = []
    feed = fetch_rss(feed_url)
    if not feed:
        return articles
    for entry in feed.entries[:max_items]:
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        if not is_recent(pub):
            continue
        title = entry.get("title", "")
        link = entry.get("link", "")
        raw = entry.get("summary", "") or entry.get("description", "")
        summary = BeautifulSoup(raw, "html.parser").get_text()[:300]
        if title and link:
            articles.append(make_article(title, link, pub, source_name, summary))
    return articles


def soup_articles(page_url, source_name, base_url="", encoding=None,
                  max_items=30, date_required=True):
    """
    Generic BeautifulSoup scraper.
    Tries <article> then common class patterns.
    date_required=False → include even if no date found (for sites with no visible date).
    """
    articles = []
    html = fetch_page(page_url, encoding=encoding)
    if not html:
        return articles

    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.find_all("article") or soup.find_all(
        ["div", "li"],
        class_=re.compile(r"post|article|card|entry|item|release", re.I),
    )

    seen: set[str] = set()
    for el in candidates[:max_items]:
        a_tag = el.find("a", href=True)
        title_tag = el.find(["h1", "h2", "h3", "h4"])
        date_tag = el.find("time") or el.find(
            ["span", "p"], class_=re.compile(r"date|time|published", re.I)
        )
        if not (a_tag and title_tag):
            continue

        title = title_tag.get_text(strip=True)
        url = a_tag["href"]
        if url.startswith("/"):
            url = (base_url or "") + url
        if not url.startswith("http") or url in seen:
            continue

        date = None
        if date_tag:
            date = date_tag.get("datetime") or date_tag.get_text(strip=True)

        if not date_required or is_recent(date):
            seen.add(url)
            articles.append(make_article(title, url, date, source_name))

    return articles


def try_rss_then_html(rss_candidates, html_url, source_name,
                      base_url="", encoding=None, date_required=True):
    for rss_url in rss_candidates:
        articles = rss_to_articles(rss_url, source_name)
        if articles:
            return articles
    return soup_articles(html_url, source_name, base_url=base_url,
                         encoding=encoding, date_required=date_required)


# ==================== 個別爬蟲 ====================

def scrape_bnext():
    """數位時代 - HTML scraping (no public RSS).
    Structure: <h2>/<h3> holds title, <a> is in parent/grandparent element.
    """
    html = fetch_page("https://www.bnext.com.tw/")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen: set[str] = set()

    for h_tag in soup.find_all(["h2", "h3"]):
        title = h_tag.get_text(strip=True)
        if not title or len(title) < 10:
            continue

        # Walk up to find the nearest <a> with /article/ href
        a_tag = None
        for ancestor in [h_tag.parent, h_tag.parent.parent if h_tag.parent else None]:
            if ancestor:
                a_tag = ancestor.find("a", href=re.compile(r"/article/"))
                if a_tag:
                    break

        if not a_tag:
            continue

        url = a_tag["href"]
        if not url.startswith("http"):
            url = "https://www.bnext.com.tw" + url
        if url in seen:
            continue

        date_tag = h_tag.parent.find("time") if h_tag.parent else None
        date = date_tag.get("datetime") if date_tag else None

        seen.add(url)
        articles.append(make_article(title, url, date, "數位時代"))

    return articles[:20]


def scrape_fc_bnext():
    """未來商務 – JS-rendered site; articles live at /articles/view/<id>.
    Links with non-empty text are the article title; empty-text dups are image links.
    No dates visible in list view → include all (homepage list = recent content).
    """
    html = fetch_page("https://fc.bnext.com.tw/category/trends")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen: set[str] = set()

    for a_tag in soup.find_all("a", href=re.compile(r"/articles/view/")):
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 8:  # skip empty image-only links
            continue
        url = a_tag["href"]
        if not url.startswith("http"):
            url = "https://fc.bnext.com.tw" + url
        if url not in seen:
            seen.add(url)
            articles.append(make_article(title, url, None, "未來商務"))

    return articles[:15]


def scrape_foodnext():
    """食力 – 爬首頁內容區的最新文章。

    導覽列的 .subnav-posts 也使用 <article>，但是專題推薦而非時間排序；
    只解析 .article-list 內含日期的 ContentContainer，避免舊文混入週報。
    """
    html = fetch_page("https://www.foodnext.net/")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen: set[str] = set()

    for el in soup.select(".article-list .ContentContainer"):
        date_tag = el.find(class_="date")
        date = date_tag.get_text(strip=True) if date_tag else None
        if not date or not is_recent(date):
            continue

        # 同一張卡片可能有標題、摘要、「閱讀更多」等重複連結，第一個有文字的文章連結是標題。
        article_links = el.find_all("a", href=re.compile(r"/paper/\d+"))
        a_tag = next(
            (a for a in article_links if a.get_text(" ", strip=True) and "閱讀更多" not in a.get_text()),
            None,
        )
        if not a_tag:
            continue

        url = a_tag["href"]
        if url.startswith("/"):
            url = "https://www.foodnext.net" + url
        if url in seen:
            continue

        title = a_tag.get_text(" ", strip=True)
        if not title:
            continue

        summary = ""
        for other_a in article_links:
            text = other_a.get_text(" ", strip=True)
            if other_a.get("href") == a_tag.get("href") and text not in {title, "閱讀更多"}:
                summary = text
                break

        seen.add(url)
        articles.append(make_article(title, url, date, "食力", summary))

    return articles


def scrape_techorange():
    """科技報橘 – 使用主 feed 過濾新零售關鍵字"""
    RETAIL_KW = re.compile(
        r"零售|電商|超商|便利商店|賣場|購物|新零售|omnichannel|retail|"
        r"消費|通路|POS|門市|店|逛", re.I
    )
    articles = []
    # Try newretail category feed first; fall back to main feed with keyword filter
    for feed_url in [
        "https://techorange.com/category/newretail/feed/",
        "https://techorange.com/feed/",
    ]:
        feed = fetch_rss(feed_url)
        if not feed:
            continue
        for entry in feed.entries[:80]:
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if not is_recent(pub):
                continue
            title = entry.get("title", "")
            link = entry.get("link", "")
            raw = entry.get("summary", "") or ""
            summary = BeautifulSoup(raw, "html.parser").get_text()[:300]
            # For main feed, filter by retail keywords; category feed is pre-filtered
            if "newretail" in feed_url or RETAIL_KW.search(title + summary):
                articles.append(make_article(title, link, pub, "科技報橘", summary))
        if articles:
            return articles
    return articles


def scrape_gvm():
    """遠見雜誌 – Service category.
    Structure: <a href=/article/NNN> wraps only image; title is in parent/grandparent text.
    """
    html = fetch_page("https://www.gvm.com.tw/category/service")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen: set[str] = set()

    for a_tag in soup.find_all("a", href=re.compile(r"/article/\d+")):
        url = a_tag["href"]
        if not url.startswith("http"):
            url = "https://www.gvm.com.tw" + url
        if url in seen:
            continue

        # Title is in the parent or grandparent container, not in <a> itself
        title = ""
        for container in [a_tag.parent, a_tag.parent.parent if a_tag.parent else None]:
            if not container:
                continue
            for tag in container.find_all(["h1", "h2", "h3", "h4", "p", "strong"]):
                t = tag.get_text(strip=True)
                if len(t) > 10:
                    title = t
                    break
            if title:
                break

        if not title:
            continue

        seen.add(url)
        articles.append(make_article(title, url, None, "遠見雜誌"))

    return articles[:20]


def scrape_businessweekly():
    """商周 – HTML scraping (RSS returns empty)"""
    html = fetch_page("https://www.businessweekly.com.tw/")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen: set[str] = set()

    for el in soup.find_all("article"):
        # BW puts title in <a title="..."> attribute or as text strings
        a_tag = el.find("a", href=True)
        if not a_tag:
            continue

        title = (a_tag.get("title") or
                 " ".join(s for s in el.stripped_strings if len(s) > 10)[:80])
        if not title:
            img = a_tag.find("img", alt=True)
            title = img["alt"] if img else ""

        url = a_tag["href"]
        if url.startswith("/"):
            url = "https://www.businessweekly.com.tw" + url
        if not url.startswith("http") or url in seen or not title:
            continue

        seen.add(url)
        articles.append(make_article(title, url, None, "商周"))

    return articles[:20]


def _youtube_rss(handle, source_name):
    """Extract channel ID from YT page and use XML feed.
    Uses a minimal User-Agent request (no Accept-Encoding) to get the full
    SSR JSON embedded in the page, which contains 'externalId'.
    """
    YT_HEADERS = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
    }
    try:
        resp = requests.get(
            f"https://www.youtube.com/@{handle}",
            headers=YT_HEADERS, timeout=15
        )
        html = resp.text
    except Exception as e:
        logger.warning(f"YouTube page fetch failed ({handle}): {e}")
        return []

    # 'externalId' is the channel's own ID in the page's ytInitialData JSON
    m = (re.search(r'"externalId":"(UC[a-zA-Z0-9_-]{22})"', html) or
         re.search(r'"channelId":"(UC[a-zA-Z0-9_-]{22})"', html))
    if not m:
        ids = re.findall(r"UC[a-zA-Z0-9_-]{22}", html)
        if not ids:
            logger.warning(f"No channel ID found for @{handle}")
            return []
        m_val = ids[0]
    else:
        m_val = m.group(1)

    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={m_val}"
    return rss_to_articles(rss_url, source_name)


def scrape_youtube_channel(handle, source_name):
    """YouTube: API (if key set) → RSS fallback"""
    if YOUTUBE_API_KEY:
        try:
            base = "https://www.googleapis.com/youtube/v3"
            r = requests.get(f"{base}/channels",
                             params={"part":"id","forHandle":handle,"key":YOUTUBE_API_KEY},
                             timeout=10)
            items = r.json().get("items", [])
            if items:
                channel_id = items[0]["id"]
                r2 = requests.get(f"{base}/search", params={
                    "part":"snippet","channelId":channel_id,"maxResults":20,
                    "order":"date","type":"video",
                    "publishedAfter": get_cutoff_date().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "key": YOUTUBE_API_KEY,
                }, timeout=10)
                articles = []
                for item in r2.json().get("items", []):
                    s = item.get("snippet", {})
                    vid = item.get("id", {}).get("videoId", "")
                    if vid:
                        articles.append(make_article(
                            s.get("title",""),
                            f"https://www.youtube.com/watch?v={vid}",
                            s.get("publishedAt"),
                            source_name,
                            s.get("description","")[:300],
                        ))
                if articles:
                    return articles
        except Exception as e:
            logger.warning(f"YT API error ({handle}): {e}")
    return _youtube_rss(handle, source_name)


def scrape_bwnet_youtube():
    return scrape_youtube_channel("bwnet", "商周 YouTube")


def scrape_shokuhin():
    """食品新聞 – ryutu (流通) category feed"""
    return try_rss_then_html(
        [
            "https://shokuhin.net/category/ryutu/feed/",
            "https://shokuhin.net/feed/",
        ],
        "https://shokuhin.net/category/ryutu/",
        "食品新聞",
        base_url="https://shokuhin.net",
        encoding="utf-8",
    )


def scrape_thinkfood():
    """식품음료신문（韓國食品飲料新聞）– 全部文章 feed"""
    return try_rss_then_html(
        ["https://www.thinkfood.co.kr/rss/allArticle.xml"],
        "https://www.thinkfood.co.kr",
        "식품음료신문",
        base_url="https://www.thinkfood.co.kr",
        encoding="utf-8",
    )


def scrape_itmedia():
    """ITmedia Business Online"""
    return try_rss_then_html(
        ["https://rss.itmedia.co.jp/rss/2.0/business.xml"],
        "https://www.itmedia.co.jp/business/",
        "ITmedia",
        base_url="https://www.itmedia.co.jp",
        encoding="utf-8",
    )


def scrape_diamond_rm():
    """Diamond Chain Store Online"""
    return try_rss_then_html(
        ["https://diamond-rm.net/feed/"],
        "https://diamond-rm.net/",
        "Diamond Chain Store",
        base_url="https://diamond-rm.net",
        encoding="utf-8",
    )


def scrape_prtimes():
    """PR TIMES – コンビニグルメ topic"""
    keyword = urllib.parse.quote("コンビニグルメ", safe="")
    topic_url = f"https://prtimes.jp/topics/keywords/{keyword}"
    return try_rss_then_html(
        [f"{topic_url}/rss2.0.xml"],
        topic_url,
        "PR TIMES",
        base_url="https://prtimes.jp",
        encoding="utf-8",
    )


def scrape_retaildive():
    """Retail Dive"""
    return try_rss_then_html(
        ["https://www.retaildive.com/feeds/news/"],
        "https://www.retaildive.com/",
        "Retail Dive",
        base_url="https://www.retaildive.com",
    )


def scrape_csnews():
    """Convenience Store News – site blocks scrapers; try multiple RSS paths"""
    CSNEWS_HEADERS = {**HEADERS, "Referer": "https://www.google.com/"}
    for rss_url in [
        "https://csnews.com/news/feed",
        "https://csnews.com/category/top-stories/feed",
        "https://csnews.com/feed",
    ]:
        try:
            resp = requests.get(rss_url, headers=CSNEWS_HEADERS, timeout=15)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                articles = []
                for entry in feed.entries[:60]:
                    pub = entry.get("published_parsed") or entry.get("updated_parsed")
                    if not is_recent(pub):
                        continue
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    raw = entry.get("summary","") or ""
                    summary = BeautifulSoup(raw,"html.parser").get_text()[:300]
                    if title and link:
                        articles.append(make_article(title, link, pub, "Convenience Store News", summary))
                if articles:
                    return articles
        except Exception as e:
            logger.warning(f"CSNews [{rss_url}]: {e}")
    return []


# 台灣連鎖暨加盟協會（TCFA）新聞類別：class 編號 → 類別名稱
TCFA_CLASSES = {
    1:  "國際流通",
    2:  "產業要聞",
    3:  "日經流通",
    4:  "零售情報",
    6:  "流行快訊",
    12: "餐飲情報",
}


def scrape_tcfa(class_id):
    """TCFA 連鎖加盟協會 – announce.asp 列表頁（表格：類別標籤｜日期｜標題）"""
    source_name = f"TCFA {TCFA_CLASSES[class_id]}"
    html = fetch_page(f"https://www.tcfa.org.tw/announce.asp?class={class_id}", encoding="utf-8")
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    # 列表頁一次列出上千筆、大致依日期新→舊；只掃前 150 列即可涵蓋一週
    for tr in soup.find_all("tr")[:150]:
        tds = tr.find_all("td")
        a_tag = tr.find("a", href=re.compile(r"announce_detail\.asp"))
        if len(tds) < 3 or not a_tag:
            continue
        date = tds[1].get_text(strip=True)          # 例：2026/10/13
        if not is_recent(date):
            continue
        url = urllib.parse.urljoin("https://www.tcfa.org.tw/", a_tag["href"])
        articles.append(make_article(a_tag.get_text(strip=True), url, date, source_name))
    return articles


# ==================== 報告生成 ====================

def _sort_articles(articles):
    return sorted(
        articles,
        key=lambda a: parse_date(a["date"]) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def generate_markdown(all_articles):
    now = datetime.now()
    cutoff = now - timedelta(days=DAYS_BACK)
    total = sum(len(v) for v in all_articles.values())

    lines = [
        "# 零售產業週報 Retail Industry Weekly",
        "",
        f"**生成時間：** {now.strftime('%Y-%m-%d %H:%M')}",
        f"**涵蓋期間：** {cutoff.strftime('%Y-%m-%d')} ～ {now.strftime('%Y-%m-%d')}",
        f"**總計：** {total} 篇",
        "",
        "---",
        "",
    ]

    for source, articles in all_articles.items():
        if not articles:
            continue
        lines.append(f"## {source}（{len(articles)} 篇）")
        lines.append("")
        for a in _sort_articles(articles):
            lines.append(f"- **[{a['title']}]({a['url']})** — {a['date_str']}")
            if a["summary"]:
                lines.append(f"  > {a['summary']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def generate_html(all_articles):
    now = datetime.now()
    cutoff = now - timedelta(days=DAYS_BACK)
    total = sum(len(v) for v in all_articles.values())

    toc = "\n".join(
        f'<li><a href="#{src.replace(" ","-")}">{src}</a> ({len(arts)} 篇)</li>'
        for src, arts in all_articles.items() if arts
    )

    sections = ""
    for source, articles in all_articles.items():
        if not articles:
            continue
        anchor = source.replace(" ", "-")
        cards = ""
        for a in _sort_articles(articles):
            summary_html = f'<p class="summary">{a["summary"]}</p>' if a["summary"] else ""
            cards += f"""
<div class="card">
  <h3><a href="{a['url']}" target="_blank" rel="noopener">{a['title']}</a></h3>
  <span class="date">{a['date_str']}</span>
  {summary_html}
</div>"""
        sections += f"""
<section id="{anchor}">
  <h2>{source} <small>({len(articles)} 篇)</small></h2>
  {cards}
</section>"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>零售週報 {now.strftime('%Y-%m-%d')}</title>
<style>
  :root{{--accent:#1a56db;--border:#e5e7eb;--bg:#f3f4f6;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
        max-width:860px;margin:0 auto;padding:24px 16px;color:#111;line-height:1.6;}}
  h1{{font-size:1.8rem;border-bottom:3px solid var(--accent);padding-bottom:10px;margin-bottom:8px;}}
  .meta{{color:#666;font-size:.9rem;margin-bottom:20px;}}
  .toc{{background:var(--bg);border-radius:8px;padding:16px 20px;margin:20px 0;}}
  .toc h2{{font-size:1rem;margin-bottom:10px;color:#374151;}}
  .toc ul{{list-style:none;columns:2;gap:10px;}}
  .toc a{{color:var(--accent);text-decoration:none;font-size:.9rem;}}
  .toc a:hover{{text-decoration:underline;}}
  section{{margin:28px 0;}}
  h2{{font-size:1.25rem;color:#1e3a5f;border-left:4px solid var(--accent);
      padding-left:10px;margin-bottom:14px;}}
  h2 small{{font-weight:normal;font-size:.85rem;color:#6b7280;}}
  .card{{border-bottom:1px solid var(--border);padding:10px 0;}}
  .card:last-child{{border-bottom:none;}}
  .card h3{{font-size:.95rem;font-weight:600;}}
  .card a{{color:var(--accent);text-decoration:none;}}
  .card a:hover{{text-decoration:underline;}}
  .date{{display:inline-block;font-size:.8rem;color:#9ca3af;margin:2px 0 4px;}}
  .summary{{font-size:.875rem;color:#4b5563;margin-top:4px;}}
  footer{{text-align:center;color:#9ca3af;font-size:.8rem;margin-top:40px;
          padding-top:20px;border-top:1px solid var(--border);}}
  @media(max-width:600px){{.toc ul{{columns:1;}}}}
</style>
</head>
<body>
<h1>零售產業週報</h1>
<p class="meta">
  生成時間：{now.strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp;
  涵蓋期間：{cutoff.strftime('%Y-%m-%d')} ～ {now.strftime('%Y-%m-%d')} &nbsp;|&nbsp;
  總計：{total} 篇
</p>
<div class="toc">
  <h2>目錄 Contents</h2>
  <ul>{toc}</ul>
</div>
{sections}
<footer>Generated by Retail News Scraper · {now.strftime('%Y-%m-%d')}</footer>
</body>
</html>"""


# ==================== 主程式 ====================

SCRAPERS = [
    ("數位時代",               scrape_bnext),
    ("未來商務",               scrape_fc_bnext),
    ("食力",                   scrape_foodnext),
    ("科技報橘",               scrape_techorange),
    ("遠見雜誌",               scrape_gvm),
    ("商周",                   scrape_businessweekly),
    ("商周 YouTube",           scrape_bwnet_youtube),
    ("食品新聞",               scrape_shokuhin),
    ("식품음료신문",           scrape_thinkfood),
    ("ITmedia",                scrape_itmedia),
    ("Diamond Chain Store",    scrape_diamond_rm),
    ("PR TIMES コンビニグルメ", scrape_prtimes),
    ("Retail Dive",            scrape_retaildive),
    ("Convenience Store News", scrape_csnews),
    # 台灣連鎖暨加盟協會：每個類別獨立一個來源
    *[(f"TCFA {name}", lambda c=cid: scrape_tcfa(c)) for cid, name in TCFA_CLASSES.items()],
]


def generate_notebooklm_txt(all_articles):
    """
    Generate a plain-text export optimised for NotebookLM.
    NotebookLM reads this as a single document source — each article becomes
    a clear chunk so the AI can cite individual stories.
    """
    now = datetime.now()
    cutoff = now - timedelta(days=DAYS_BACK)
    total = sum(len(v) for v in all_articles.values())

    lines = [
        f"零售產業週報 Retail Industry Weekly Report",
        f"生成日期：{now.strftime('%Y-%m-%d')}",
        f"涵蓋期間：{cutoff.strftime('%Y-%m-%d')} 至 {now.strftime('%Y-%m-%d')}",
        f"總計：{total} 篇新聞",
        "",
        "=" * 60,
        "",
    ]

    for source, articles in all_articles.items():
        if not articles:
            continue
        lines.append(f"【{source}】")
        lines.append("")
        for a in _sort_articles(articles):
            lines.append(f"標題：{a['title']}")
            lines.append(f"日期：{a['date_str']}")
            lines.append(f"連結：{a['url']}")
            if a["summary"]:
                lines.append(f"摘要：{a['summary']}")
            lines.append("")
        lines.append("-" * 60)
        lines.append("")

    return "\n".join(lines)


def upload_to_google_drive(file_path: Path, folder_id: str = "") -> str:
    """
    Upload a file to Google Drive using the Drive API.
    Requires: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib

    Set GOOGLE_DRIVE_FOLDER_ID env var to upload into a specific folder.
    Returns the shareable file URL, or empty string on failure.
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        import pickle

        SCOPES = ["https://www.googleapis.com/auth/drive.file"]
        token_path = Path(__file__).parent / "token.pickle"
        creds_path = Path(__file__).parent / "credentials.json"

        creds = None
        if token_path.exists():
            with open(token_path, "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif creds_path.exists():
                flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
                creds = flow.run_local_server(port=0)
                with open(token_path, "wb") as f:
                    pickle.dump(creds, f)
            else:
                logger.warning("Google Drive: credentials.json 不存在，跳過上傳")
                return ""

        service = build("drive", "v3", credentials=creds)

        file_meta = {"name": file_path.name}
        if folder_id:
            file_meta["parents"] = [folder_id]

        media = MediaFileUpload(str(file_path), mimetype="text/plain")
        uploaded = service.files().create(
            body=file_meta, media_body=media, fields="id,webViewLink"
        ).execute()

        # Make it readable by anyone with the link (so NotebookLM can access it)
        service.permissions().create(
            fileId=uploaded["id"],
            body={"role": "reader", "type": "anyone"},
        ).execute()

        url = uploaded.get("webViewLink", "")
        logger.info(f"  Google Drive 上傳成功: {url}")
        return url

    except ImportError:
        logger.warning("Google Drive 上傳需要: pip install google-api-python-client google-auth-oauthlib")
        return ""
    except Exception as e:
        logger.warning(f"Google Drive 上傳失敗: {e}")
        return ""


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"=== 零售週報爬蟲啟動 | 回溯 {DAYS_BACK} 天 ===")
    logger.info(f"截止日期: {get_cutoff_date().strftime('%Y-%m-%d %H:%M UTC')}")

    all_articles: dict[str, list] = {}

    for source_name, scraper in SCRAPERS:
        logger.info(f"▶ {source_name}")
        try:
            raw = scraper()
            seen: set[str] = set()
            unique = [a for a in raw if a["url"] and a["url"] not in seen and not seen.add(a["url"])]
            all_articles[source_name] = unique
            logger.info(f"  → {len(unique)} 篇")
        except Exception as e:
            logger.error(f"  → 錯誤: {e}")
            all_articles[source_name] = []
        time.sleep(1.5)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    md_content   = generate_markdown(all_articles)
    html_content = generate_html(all_articles)
    txt_content  = generate_notebooklm_txt(all_articles)

    # Flat article list for AI curation
    import json as _json
    flat = [a for arts in all_articles.values() for a in arts]
    json_content = _json.dumps(
        {"generated": datetime.now().isoformat(), "articles": flat},
        ensure_ascii=False, indent=2
    )

    files = {
        OUTPUT_DIR / f"retail_news_{stamp}.md":   md_content,
        OUTPUT_DIR / f"retail_news_{stamp}.html": html_content,
        OUTPUT_DIR / "latest.md":                 md_content,
        OUTPUT_DIR / "latest.html":               html_content,
        OUTPUT_DIR / "notebooklm_source.txt":     txt_content,
        OUTPUT_DIR / "articles.json":             json_content,  # for curate.py
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")

    total = sum(len(v) for v in all_articles.values())
    logger.info(f"\n✓ 完成！共收集 {total} 篇")

    # Optional: upload to Google Drive for NotebookLM
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    drive_url = upload_to_google_drive(OUTPUT_DIR / "notebooklm_source.txt", folder_id)

    print("\n" + "=" * 52)
    print(f"{'來源':<30} {'篇數':>5}")
    print("-" * 52)
    for src, arts in all_articles.items():
        print(f"{src:<30} {len(arts):>5}")
    print("=" * 52)
    print(f"{'總計':<30} {total:>5}")
    print()
    print(f"📄 HTML 報告  : {OUTPUT_DIR / 'latest.html'}")
    print(f"📋 NotebookLM : {OUTPUT_DIR / 'notebooklm_source.txt'}")
    if drive_url:
        print(f"☁️  Google Drive: {drive_url}")
        print("   → 在 NotebookLM 中新增來源，選「Google 雲端硬碟」並貼上此連結")


if __name__ == "__main__":
    main()
