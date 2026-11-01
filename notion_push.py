#!/usr/bin/env python3
"""
Notion 週報推送腳本
將最新的 weekly_summary_*.md 推送到指定 Notion 頁面，自動建立子頁面。

首次設定：
  1. 到 https://www.notion.so/my-integrations 建立 integration，複製 token
  2. 在目標 Notion 頁面右上角 → ... → Connect to → 選你的 integration
  3. 複製頁面 URL 中的 ID（最後那串，不含 ?v=...）

執行方式：
  export NOTION_TOKEN=secret_xxx
  export NOTION_PAGE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  python3 notion_push.py
"""

import os
import re
import requests
from pathlib import Path
from datetime import datetime
from notion_client import Client

# ==================== 設定 ====================

SCRIPT_DIR = Path(__file__).parent
SUMMARY_DIR = SCRIPT_DIR / "summary"

def _load_env_file():
    """環境變數缺少時，從同目錄 .env 補載（不覆蓋已存在的環境變數）"""
    env_path = SCRIPT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# 憑證一律放 .env（同目錄），不要寫死在程式碼裡
_load_env_file()
NOTION_TOKEN   = os.environ.get("NOTION_TOKEN", "")
NOTION_PAGE_ID = os.environ.get("NOTION_PAGE_ID", "")

# ==================== Notion Block 建構工具 ====================

def rt(content: str, bold=False, italic=False, url=None) -> dict:
    """Build a single rich_text element."""
    elem = {
        "type": "text",
        "text": {"content": content},
    }
    if bold or italic:
        elem["annotations"] = {}
        if bold:
            elem["annotations"]["bold"] = True
        if italic:
            elem["annotations"]["italic"] = True
    if url:
        elem["text"]["link"] = {"url": url}
    return elem


def parse_inline(line: str) -> list[dict]:
    """
    Parse inline markdown markers into rich_text list.
    Handles **bold**, *italic*, and bare URLs.
    """
    result = []
    # Pattern: **bold**, *italic*, or plain text
    token_re = re.compile(r'\*\*(.+?)\*\*|\*(.+?)\*|([^*]+)')
    for m in token_re.finditer(line):
        if m.group(1):                      # **bold**
            result.append(rt(m.group(1), bold=True))
        elif m.group(2):                    # *italic*
            result.append(rt(m.group(2), italic=True))
        elif m.group(3) and m.group(3).strip():
            result.append(rt(m.group(3)))
    return result if result else [rt(line)]


def link_line_blocks(line: str) -> list[dict]:
    """
    Convert '**連結：** https://...' into a paragraph with a clickable URL.
    """
    url = line.replace("**連結：**", "").strip()
    return [{
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                rt("連結：", bold=True),
                rt(url, url=url),
            ]
        }
    }]


# ==================== Markdown → Notion Blocks ====================

def md_to_blocks(md_text: str) -> list[dict]:
    blocks = []

    for raw_line in md_text.splitlines():
        line = raw_line.rstrip()

        if not line:
            continue

        # Divider
        if line.strip() == "---":
            blocks.append({"type": "divider", "divider": {}})

        # H3 (check before H2/H1 to avoid prefix conflicts)
        elif line.startswith('### '):
            blocks.append({
                "type": "heading_3",
                "heading_3": {"rich_text": parse_inline(line[4:])}
            })

        # H2
        elif line.startswith('## '):
            blocks.append({
                "type": "heading_2",
                "heading_2": {"rich_text": parse_inline(line[3:])}
            })

        # H1
        elif line.startswith('# '):
            blocks.append({
                "type": "heading_1",
                "heading_1": {"rich_text": parse_inline(line[2:])}
            })

        # Bullet point
        elif line.startswith("- "):
            blocks.append({
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": parse_inline(line[2:])}
            })

        # Link line
        elif line.startswith("**連結：**"):
            blocks.extend(link_line_blocks(line))

        # Everything else → paragraph
        else:
            blocks.append({
                "type": "paragraph",
                "paragraph": {"rich_text": parse_inline(line)}
            })

    return blocks


# ==================== 主流程 ====================

def find_latest_summary() -> Path:
    files = sorted(SUMMARY_DIR.glob("weekly_summary_2*.md"), reverse=True)
    if not files:
        raise FileNotFoundError(
            f"找不到 weekly_summary_*.md\n"
            "請先透過 Claude Code 策展產出週報"
        )
    return files[0]


def detect_parent_type(client: Client, object_id: str) -> str:
    """Return 'database' or 'page' based on what the ID points to."""
    try:
        client.databases.retrieve(database_id=object_id)
        return "database"
    except Exception:
        return "page"


def get_title_prop(token: str, database_id: str) -> str:
    """Use raw HTTP to get database schema and find the title property name."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
    }
    # Try databases endpoint first
    resp = requests.get(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=headers,
    )
    if resp.ok:
        props = resp.json().get("properties", {})
        print(f"  資料庫欄位：{list(props.keys())}")
        for name, prop in props.items():
            if prop.get("type") == "title":
                return name

    # Fallback: query one page and inspect its properties
    resp2 = requests.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers={**headers, "Content-Type": "application/json"},
        json={"page_size": 1},
    )
    if resp2.ok:
        pages = resp2.json().get("results", [])
        if pages:
            props = pages[0].get("properties", {})
            print(f"  資料庫欄位（從查詢）：{list(props.keys())}")
            for name, prop in props.items():
                if prop.get("type") == "title":
                    return name

    return None


def push_to_notion(md_path: Path, token: str, parent_id: str) -> str:
    client = Client(auth=token)
    _token = token  # keep for raw HTTP calls

    text = md_path.read_text(encoding="utf-8")
    all_blocks = md_to_blocks(text)

    # Date from filename
    date_match = re.search(r'weekly_summary_(\d{8})', md_path.name)
    date_str = date_match.group(1) if date_match else datetime.now().strftime("%Y%m%d")
    page_title = f"零售週報 {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    # Auto-detect: database or page
    parent_type = detect_parent_type(client, parent_id)
    print(f"  父層類型：{parent_type}")

    print(f"  建立頁面：{page_title}")
    print(f"  共 {len(all_blocks)} 個 block")

    first_batch = all_blocks[:100]

    if parent_type == "database":
        title_prop = get_title_prop(_token, parent_id)
        if title_prop is None:
            raise Exception(
                "找不到 title 欄位名稱。\n"
                "請把終端機輸出的『資料庫欄位：[...]』那行貼給我看。"
            )
        print(f"  title 欄位：{title_prop}")
        response = client.pages.create(
            parent={"database_id": parent_id},
            properties={
                title_prop: {
                    "title": [{"type": "text", "text": {"content": page_title}}]
                },
                "狀態": {"status": {"name": "週報整理"}},
            },
            children=first_batch,
        )
    else:
        response = client.pages.create(
            parent={"page_id": parent_id},
            properties={
                "title": {
                    "title": [{"type": "text", "text": {"content": page_title}}]
                }
            },
            children=first_batch,
        )

    new_page_id = response["id"]

    # Append remaining blocks in batches
    for start in range(100, len(all_blocks), 100):
        batch = all_blocks[start:start + 100]
        client.blocks.children.append(block_id=new_page_id, children=batch)
        print(f"  追加 block {start+1}~{start+len(batch)}")

    return response.get("url", f"https://notion.so/{new_page_id.replace('-','')}")


def main():
    print("=== Notion 週報推送 ===")

    token   = NOTION_TOKEN
    page_id = NOTION_PAGE_ID

    if not token:
        print("❌ 請設定 NOTION_TOKEN 環境變數")
        print("   export NOTION_TOKEN=secret_xxx")
        return

    if not page_id:
        print("❌ 請設定 NOTION_PAGE_ID 環境變數")
        print("   export NOTION_PAGE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        return

    try:
        md_path = find_latest_summary()
        print(f"  讀取：{md_path.name}")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return

    try:
        url = push_to_notion(md_path, token, page_id)
        print()
        print(f"✅ 推送成功！")
        print(f"   {url}")
    except Exception as e:
        print(f"❌ 推送失敗：{e}")
        raise


if __name__ == "__main__":
    main()
