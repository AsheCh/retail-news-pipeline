import time
from datetime import datetime, timedelta, timezone

import retail_news_scraper as s


def test_parse_date_rfc2822():
    dt = s.parse_date("Mon, 26 Oct 2026 08:00:00 +0000")
    assert dt == datetime(2026, 10, 26, 8, tzinfo=timezone.utc)


def test_parse_date_iso_with_z():
    assert s.parse_date("2026-10-26T08:00:00Z").tzinfo is not None


def test_parse_date_cjk_formats():
    # 中日文網站常見的日期寫法
    for raw in ["2026年10月26日", "2026/10/26", "2026.10.26"]:
        assert s.parse_date(raw).date().isoformat() == "2026-10-26"


def test_parse_date_struct_time():
    st = time.strptime("2026-10-26", "%Y-%m-%d")
    assert s.parse_date(st).date().isoformat() == "2026-10-26"


def test_parse_date_invalid_returns_none():
    assert s.parse_date("not a date") is None
    assert s.parse_date(None) is None


def test_is_recent_window():
    now = datetime.now(timezone.utc)
    assert s.is_recent((now - timedelta(days=1)).isoformat())
    assert not s.is_recent((now - timedelta(days=30)).isoformat())
    # 抓不到日期的文章保留，交給策展判斷
    assert s.is_recent(None)


def test_make_article_normalizes_fields():
    a = s.make_article("  標題 ", " https://example.com/a ", "2026/10/26", "測試來源", "x" * 500)
    assert a["title"] == "標題"
    assert a["url"] == "https://example.com/a"
    assert a["date_str"] == "2026-10-26"
    assert len(a["summary"]) == 300
