#!/bin/bash
# 零售週報爬蟲（每週日 20:00 由 cron 執行）
# crontab 範例：0 20 * * 0 /path/to/retail-news-pipeline/run_weekly.sh
# 策展與 Notion 推送由 Claude Code 的 /retail-news 執行（見 claude/retail-news.md）

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/weekly_$(date +%Y%m%d).log"
PYTHON="${PYTHON:-python3}"

log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

# macOS 桌面通知；其他系統略過
notify() {
    command -v osascript >/dev/null && osascript -e "display notification \"$1\" with title \"$2\""
}

log "===== 零售週報爬蟲開始 ====="

if ! "$PYTHON" "$SCRIPT_DIR/retail_news_scraper.py" >> "$LOG_FILE" 2>&1; then
    log "❌ 爬蟲失敗，請查看 logs/"
    notify "爬蟲失敗，請查看 logs/" "零售週報 ❌"
    exit 1
fi

ARTICLE_COUNT=$("$PYTHON" -c "import json; d=json.load(open('$SCRIPT_DIR/reports/articles.json')); print(len(d['articles']))" 2>/dev/null || echo "?")
log "✅ 爬取完成：$ARTICLE_COUNT 篇"
notify "爬取 ${ARTICLE_COUNT} 篇完成，請執行 /retail-news 策展" "零售週報 ✅"
