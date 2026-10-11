#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# اطلاع‌رسانی وضعیت ربات ارزش منطقه‌ای به ادمین (از طریق API بله)
#
#   notify.sh start    ← systemd: ExecStartPost   (ربات بالا آمد)
#   notify.sh stop     ← systemd: ExecStopPost    (ربات قطع/متوقف شد)
#   notify.sh health   ← تایمر پایش هر ۵ دقیقه    (ربات فعال نیست ← راه‌اندازی)
#   notify.sh test     ← ارسال یک پیام آزمایشی
#
# توکن و آیدی از .env همین ربات خوانده می‌شود:
#   BOT_TOKEN / ADMIN_ID / BALE_API_BASE
# در صورت نیاز می‌توان پیام‌ها را از ربات دیگری فرستاد:
#   NOTIFY_BOT_TOKEN / NOTIFY_CHAT_ID
# ═══════════════════════════════════════════════════════════════
set -u

EVENT="${1:-test}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
SERVICE="${SERVICE_NAME:-regional-value-bot}"
STATE_DIR="/var/lib/$SERVICE"
mkdir -p "$STATE_DIR" 2>/dev/null || STATE_DIR="$APP_DIR/data"

# خواندن یک کلید از .env بدون source کردن فایل (مقادیر فارسی/فاصله‌دار مشکلی ایجاد نکنند)
env_get() {
    [ -f "$ENV_FILE" ] || return 0
    grep -E "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" | tail -n1 | cut -d= -f2- \
        | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

TOKEN="$(env_get NOTIFY_BOT_TOKEN)"; [ -n "$TOKEN" ] || TOKEN="$(env_get BOT_TOKEN)"
CHAT_ID="$(env_get NOTIFY_CHAT_ID)"; [ -n "$CHAT_ID" ] || CHAT_ID="$(env_get ADMIN_ID)"
API_BASE="$(env_get BALE_API_BASE)"; [ -n "$API_BASE" ] || API_BASE="https://tapi.bale.ai"
API_BASE="${API_BASE%/}"
HOST="$(hostname)"
NOW="$(TZ=Asia/Tehran date '+%Y-%m-%d %H:%M:%S')"

send() {
    if [ -z "$TOKEN" ] || [ -z "$CHAT_ID" ]; then
        echo "notify: BOT_TOKEN یا ADMIN_ID در $ENV_FILE تنظیم نشده" >&2
        return 0
    fi
    local text="$1"
    text="${text:0:3800}"
    for _ in 1 2 3; do
        if curl -fsS -m 15 -o /dev/null "$API_BASE/bot$TOKEN/sendMessage" \
            --data-urlencode "chat_id=$CHAT_ID" --data-urlencode "text=$text"; then
            return 0
        fi
        sleep 5
    done
    echo "notify: ارسال پیام به ادمین ناموفق بود" >&2
    return 0   # خطای اطلاع‌رسانی نباید روی سرویس اثر بگذارد
}

# جلوگیری از سیل پیام در حلقهٔ کرش: ۳ پیام اول در هر ساعت، بعد هر دهمین کرش
should_alert_crash() {
    local f="$STATE_DIR/crash_times" now count
    now=$(date +%s)
    { [ -f "$f" ] && awk -v n="$now" '$1 > n-3600' "$f"; echo "$now"; } > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f"
    count=$(wc -l < "$f" 2>/dev/null || echo 1)
    echo "$count" > "$STATE_DIR/crash_count_hour"
    [ "$count" -le 3 ] || [ $((count % 10)) -eq 0 ]
}

case "$EVENT" in
start)
    if [ -f "$STATE_DIR/crashed" ]; then
        reason="$(cat "$STATE_DIR/crashed")"
        rm -f "$STATE_DIR/crashed"
        if [ -f "$STATE_DIR/crash_alerted" ]; then
            rm -f "$STATE_DIR/crash_alerted"
            send "✅ ربات ارزش منطقه‌ای پس از قطعی دوباره راه‌اندازی شد
علت قطعی: $reason
🖥 $HOST | 🕒 $NOW"
        fi
    elif [ ! -f "/run/$SERVICE.booted" ]; then
        send "🔄 ربات ارزش منطقه‌ای اجرا شد (راه‌اندازی سرور یا شروع سرویس)
🖥 $HOST | 🕒 $NOW"
    else
        send "▶️ ربات ارزش منطقه‌ای دوباره اجرا شد
🖥 $HOST | 🕒 $NOW"
    fi
    touch "/run/$SERVICE.booted" 2>/dev/null || true
    ;;

stop)
    result="${SERVICE_RESULT:-unknown}"
    if [ "$result" = "success" ]; then
        # توقف دستی (systemctl stop/restart) — اطلاع کوتاه
        send "⏹ ربات ارزش منطقه‌ای متوقف شد (دستی)
🖥 $HOST | 🕒 $NOW"
        exit 0
    fi
    reason="$result / ${EXIT_CODE:-?} ${EXIT_STATUS:-?}"
    echo "$reason" > "$STATE_DIR/crashed"
    if should_alert_crash; then
        touch "$STATE_DIR/crash_alerted"
        logs="$(journalctl -u "$SERVICE" -n 15 --no-pager -o cat 2>/dev/null | tail -c 1800)"
        cnt="$(cat "$STATE_DIR/crash_count_hour" 2>/dev/null || echo 1)"
        send "⚠️ ربات ارزش منطقه‌ای قطع شد!
علت: $reason
تعداد قطعی در یک ساعت اخیر: $cnt
⏳ تا چند ثانیهٔ دیگر خودکار راه‌اندازی می‌شود.
🖥 $HOST | 🕒 $NOW

آخرین لاگ‌ها:
$logs"
    fi
    ;;

health)
    state="$(systemctl is-active "$SERVICE" 2>/dev/null)"
    case "$state" in
        active|activating|reloading|deactivating) exit 0 ;;
    esac
    # فقط اگر سرویس فعال (enabled) است و کسی آن را عمداً متوقف نکرده
    systemctl is-enabled --quiet "$SERVICE" || exit 0
    [ -f "$STATE_DIR/manual_stop" ] && exit 0
    systemctl reset-failed "$SERVICE" 2>/dev/null
    systemctl start "$SERVICE"
    sleep 15
    if systemctl is-active --quiet "$SERVICE"; then
        send "🩺 پایشگر: ربات ارزش منطقه‌ای در وضعیت «$state» بود و دوباره راه‌اندازی شد.
🖥 $HOST | 🕒 $NOW"
    else
        logs="$(journalctl -u "$SERVICE" -n 20 --no-pager -o cat 2>/dev/null | tail -c 2000)"
        send "🚨 پایشگر: ربات ارزش منطقه‌ای بالا نمی‌آید! بررسی دستی لازم است.
🖥 $HOST | 🕒 $NOW

$logs"
    fi
    ;;

test)
    send "🔔 پیام آزمایشی سیستم اطلاع‌رسانی ربات ارزش منطقه‌ای
🖥 $HOST | 🕒 $NOW"
    echo "پیام آزمایشی ارسال شد (اگر در بله دریافت نکردید، ADMIN_ID و استارت ربات را بررسی کنید)."
    ;;
*)
    echo "usage: $0 start|stop|health|test" >&2; exit 1 ;;
esac
exit 0
