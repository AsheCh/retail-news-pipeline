from pathlib import Path

import notion_push as n

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "weekly_summary_sample.md"


def test_parse_inline_bold_and_plain():
    parts = n.parse_inline("**來源：** 數位時代")
    assert parts[0]["annotations"]["bold"] is True
    assert parts[1]["text"]["content"].strip() == "數位時代"


def test_link_line_is_clickable():
    block = n.link_line_blocks("**連結：** https://example.com/a")[0]
    link = block["paragraph"]["rich_text"][1]["text"]["link"]
    assert link["url"] == "https://example.com/a"


def test_md_to_blocks_types():
    md = "# 標題\n\n## 分節\n### #1 文章\n- 切入點\n---\n內文"
    types = [b["type"] for b in n.md_to_blocks(md)]
    assert types == ["heading_1", "heading_2", "heading_3",
                     "bulleted_list_item", "divider", "paragraph"]


def test_sample_summary_converts():
    # 範例週報整份可轉換，且每段文字都在 Notion 2000 字上限內
    blocks = n.md_to_blocks(SAMPLE.read_text(encoding="utf-8"))
    assert len(blocks) > 50
    for b in blocks:
        for rt in b[b["type"]].get("rich_text", []):
            assert len(rt["text"]["content"]) <= 2000
