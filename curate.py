#!/usr/bin/env python3
"""
零售週報 AI 精選策展
讀取爬蟲結果，使用 Claude AI 依據讀者關注的主題挑選最相關的文章，
並產出 Markdown 摘要（可直接貼入 Notion）。

執行方式：
  python3 curate.py
  ANTHROPIC_API_KEY=sk-... python3 curate.py
"""

import os
import json
import anthropic
from datetime import datetime
from pathlib import Path

# ==================== 路徑設定 ====================

SCRIPT_DIR = Path(__file__).parent
ARTICLES_JSON = SCRIPT_DIR / "reports" / "articles.json"
OUTPUT_DIR    = SCRIPT_DIR / "summary"

# ==================== 使用者背景（可自行修改） ====================

USER_PROFILE = """
【讀者】
關注台灣零售與消費品產業的數據分析者，需要從產業新聞中找出可用數據驗證的趨勢與策略議題。

【產業關注範疇】
通路零售（超商、超市、量販）、餐飲連鎖（速食、咖啡、手搖）、美妝保養、食品飲料

【挑選優先順序】
1. ★★★ 競品動態：超商/超市/量販的新策略、新服務、跨界合作、海外動向
2. ★★★ 消費者行為：購物決策、世代偏好改變、通路轉換、消費降級/升級
3. ★★★ 日本便利商店動態（日本超商是台灣市場的直接參考指標，7-Eleven、全家、羅森）
4. ★★  食品飲料新食材、話題新品（可能成為商品企劃/聯名靈感）
5. ★★  零售科技：AI應用、無人店、數據行銷、支付創新的實際落地案例
6. ★   海外零售趨勢（歐美市場大方向）

【明確過濾掉的內容】
- 純財經/股市/ETF/房市資訊
- 與零售無關的純技術 AI 文章（晶片、基礎模型研究、資安）
- 政治、地緣政治
- 純公關發布（無實質產業洞察的活動通知）
"""

# ==================== AI 精選核心邏輯 ====================

SYSTEM_PROMPT = f"""你是一位零售產業情報分析師助理，專門為以下背景的分析師準備每週新聞簡報：

{USER_PROFILE}

你的任務：
1. 從提供的新聞清單中，挑選 8～12 篇最值得深入分析的文章
2. 為每篇文章撰寫中文摘要與切入點
3. 以繁體中文回覆

評選原則：
- 優先選擇有具體數據、案例或策略方向的文章（而非只有標題黨）
- 日本便利商店相關動態視為高優先
- 同主題只選最有代表性的 1 篇（去重）
- 若文章標題或摘要與零售/消費/食品無關，直接排除

輸出格式（嚴格遵守 JSON，不要加額外文字）：
{{
  "week": "2026-05-17",
  "total_reviewed": 132,
  "total_selected": 10,
  "selected": [
    {{
      "rank": 1,
      "category": "競品動態",
      "title": "文章標題（原文）",
      "source": "來源名稱",
      "date": "2026-05-15",
      "url": "https://...",
      "summary": "2-3句摘要，說明這篇文章的核心內容與數據亮點",
      "relevance": "一句話說明產業意涵（例如：可參考日本超商做法，評估台灣市場可行性）",
      "talking_points": [
        "切入點1：具體討論角度或延伸問題",
        "切入點2：可用數據驗證的延伸問題"
      ]
    }}
  ]
}}

category 只能是以下五種之一：競品動態 / 消費趨勢 / 食品新品 / 零售科技 / 海外市場
"""


def load_articles() -> list[dict]:
    """Load articles from the JSON file produced by the scraper."""
    if not ARTICLES_JSON.exists():
        raise FileNotFoundError(
            f"找不到 {ARTICLES_JSON}\n"
            "請先執行：python3 retail_news_scraper.py"
        )
    data = json.loads(ARTICLES_JSON.read_text(encoding="utf-8"))
    return data["articles"]


def build_article_list(articles: list[dict]) -> str:
    """Format articles into a compact numbered list for the prompt."""
    lines = [f"以下共 {len(articles)} 篇新聞，請依指示挑選並輸出 JSON：\n"]
    for i, a in enumerate(articles, 1):
        line = f"[{i}] [{a['source']}] {a['date_str']} | {a['title']}"
        if a.get("summary"):
            line += f"\n    摘要：{a['summary'][:150]}"
        line += f"\n    URL：{a['url']}"
        lines.append(line)
    return "\n".join(lines)


def call_claude(article_list_text: str, total: int) -> dict:
    """Call Claude API with prompt caching on the system prompt."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError(
            "請設定 ANTHROPIC_API_KEY 環境變數\n"
            "例如：export ANTHROPIC_API_KEY=sk-ant-..."
        )

    client = anthropic.Anthropic(api_key=api_key)

    print("  正在呼叫 Claude API（claude-sonnet-5-5）...")
    response = client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # prompt caching
            }
        ],
        messages=[
            {
                "role": "user",
                "content": article_list_text,
            }
        ],
    )

    usage = response.usage
    print(f"  Token 用量 — 輸入: {usage.input_tokens:,}  輸出: {usage.output_tokens:,}")
    if hasattr(usage, "cache_read_input_tokens") and usage.cache_read_input_tokens:
        print(f"  快取命中: {usage.cache_read_input_tokens:,} tokens（省費用）")

    raw = response.content[0].text.strip()

    # Strip markdown code block if Claude wraps it
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    result = json.loads(raw)
    result["total_reviewed"] = total
    return result


# ==================== 報告生成 ====================

CATEGORY_ICONS = {
    "競品動態": "🏪",
    "消費趨勢": "👥",
    "食品新品": "🍱",
    "零售科技": "🤖",
    "海外市場": "🌏",
}


def generate_weekly_summary_md(result: dict) -> str:
    now = datetime.now()
    selected = result.get("selected", [])

    lines = [
        f"# 零售產業週報精選",
        f"",
        f"**AI 策展日期：** {now.strftime('%Y-%m-%d')}",
        f"**本週爬取：** {result.get('total_reviewed', '—')} 篇 → 精選 **{len(selected)} 篇**",
        "",
        "---",
        "",
    ]

    # Group by category
    categories = {}
    for a in selected:
        cat = a.get("category", "其他")
        categories.setdefault(cat, []).append(a)

    for cat, items in categories.items():
        icon = CATEGORY_ICONS.get(cat, "📌")
        lines.append(f"## {icon} {cat}")
        lines.append("")
        for a in items:
            title = a.get("title", "")
            url = a.get("url", "")
            rank = a.get("rank", "")
            lines.append(f"### #{rank} {title}")
            lines.append(f"**來源：** {a.get('source', '')} · {a.get('date', '')}")
            lines.append(f"**連結：** {url}")
            lines.append("")
            lines.append(a.get("summary", ""))
            lines.append("")
            lines.append(f"💡 {a.get('relevance', '')}")
            lines.append("")
            lines.append("**延伸分析切入點**")
            for tp in a.get("talking_points", []):
                lines.append(f"- {tp}")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ==================== 主程式 ====================

def main():
    print("=== 零售週報 AI 精選策展 ===")

    # Load articles
    print(f"載入文章：{ARTICLES_JSON}")
    try:
        articles = load_articles()
    except FileNotFoundError as e:
        print(f"錯誤：{e}")
        return

    print(f"共 {len(articles)} 篇文章待審閱")

    # Build article list for prompt
    article_text = build_article_list(articles)

    # Call Claude
    try:
        result = call_claude(article_text, len(articles))
    except ValueError as e:
        print(f"\n❌ {e}")
        return
    except json.JSONDecodeError as e:
        print(f"\n❌ Claude 回傳格式解析失敗：{e}")
        return
    except Exception as e:
        print(f"\n❌ API 呼叫失敗：{e}")
        return

    selected = result.get("selected", [])
    print(f"精選結果：{len(selected)} 篇")

    # Write Markdown to summary/
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_stamp = datetime.now().strftime("%Y%m%d")

    md_path     = OUTPUT_DIR / f"weekly_summary_{date_stamp}.md"
    latest_md   = OUTPUT_DIR / "weekly_summary_latest.md"

    md_content = generate_weekly_summary_md(result)

    md_path.write_text(md_content, encoding="utf-8")
    latest_md.write_text(md_content, encoding="utf-8")

    print()
    print("=" * 50)
    for a in selected:
        cat = CATEGORY_ICONS.get(a.get("category",""), "📌") + " " + a.get("category","")
        print(f"  #{a.get('rank','?'):>2} [{cat:<10}] {a.get('title','')[:55]}")
    print("=" * 50)
    print()
    print(f"📋 Notion 週報：{md_path}")
    print()
    print("💡 複製到 Notion：")
    print(f"   open {md_path}")


if __name__ == "__main__":
    main()
