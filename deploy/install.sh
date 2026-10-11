#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# نصب ربات «ارزش منطقه‌ای» روی VPS، کنار ربات اصلی (online.judicial.services.ble)
#
#   sudo bash deploy/install.sh
#
# کارهایی که انجام می‌دهد:
#   ۱. بررسی تداخل با ربات اصلی (توکن، پوشه، نام سرویس، پروسهٔ تکراری)
#   ۲. ساخت venv جداگانه و نصب وابستگی‌ها
#   ۳. سرویس systemd با ری‌استارت خودکار و اجرا پس از ریبوت سرور
#   ۴. اطلاع به ادمین در بله هنگام قطعی / راه‌اندازی دوباره
#   ۵. تایمر پایش هر ۵ دقیقه (اگر سرویس به هر دلیلی پایین بود، بالا می‌آورد)
#   ۶. دستور مدیریتی rvbot
# اجرای دوباره امن است (به‌روزرسانی/تعمیر نصب).
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SERVICE="regional-value-bot"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-root}"
[ "$RUN_USER" = "" ] && RUN_USER=root

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    red "❌ با sudo اجرا کنید:  sudo bash deploy/install.sh"; exit 1
fi
command -v systemctl >/dev/null || { red "❌ systemd روی این سرور نیست."; exit 1; }

echo "📁 پوشهٔ ربات: $APP_DIR"
echo "👤 کاربر اجرا: $RUN_USER"
echo

env_get() {  # env_get FILE KEY
    grep -E "^[[:space:]]*$2[[:space:]]*=" "$1" 2>/dev/null | tail -n1 | cut -d= -f2- \
        | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# ─────────────── ۱. فایل .env ───────────────
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$RUN_USER": "$APP_DIR/.env"; chmod 600 "$APP_DIR/.env"
    yellow "📝 فایل .env ساخته شد. BOT_TOKEN، ADMIN_ID و NESHAN_API_KEY را وارد کنید:"
    echo "   nano $APP_DIR/.env"
    echo "سپس دوباره همین اسکریپت را اجرا کنید."
    exit 0
fi
chmod 600 "$APP_DIR/.env"
TOKEN="$(env_get "$APP_DIR/.env" BOT_TOKEN)"
ADMIN="$(env_get "$APP_DIR/.env" ADMIN_ID)"
[ -n "$TOKEN" ] || { red "❌ BOT_TOKEN در .env خالی است."; exit 1; }
[ -n "$ADMIN" ] || yellow "⚠️  ADMIN_ID خالی است — پیام قطعی به کسی ارسال نمی‌شود."

# ─────────────── ۲. بررسی تداخل‌ها ───────────────
echo "🔍 بررسی تداخل با ربات‌های دیگر روی سرور..."
CONFLICT=0

# (الف) توکن تکراری: دو ربات با یک توکن ← هر دو از کار می‌افتند (خطای Conflict در getUpdates)
while IFS= read -r f; do
    [ "$(readlink -f "$f")" = "$(readlink -f "$APP_DIR/.env")" ] && continue
    other="$(env_get "$f" BOT_TOKEN)"; [ -n "$other" ] || other="$(env_get "$f" TELEGRAM_BOT_TOKEN)"
    if [ -n "$other" ] && [ "$other" = "$TOKEN" ]; then
        red "❌ توکن این ربات با توکن ربات دیگری یکسان است: $f"
        red "   برای ربات ارزش منطقه‌ای یک توکن جدا از @BotFather بله بگیرید."
        CONFLICT=1
    fi
done < <(find /root /home /opt /srv /var/www -maxdepth 4 -name .env -type f 2>/dev/null)

# (ب) پوشه‌ها نباید یکی باشند
if [ -f "$APP_DIR/bot.py" ] || [ -f "$APP_DIR/scenarios.py" ]; then
    red "❌ این پوشه، پوشهٔ ربات اصلی است! ربات ارزش منطقه‌ای را در پوشهٔ جدا clone کنید (مثلاً /opt/regional_value)."
    CONFLICT=1
fi

# (ج) پروسهٔ دستیِ همین ربات (python main.py در screen/tmux/nohup) ← دو نمونه با یک توکن
for pid in $(pgrep -f "main.py" 2>/dev/null); do
    cwd="$(readlink -f /proc/$pid/cwd 2>/dev/null || true)"
    if [ "$cwd" = "$(readlink -f "$APP_DIR")" ] && ! systemctl show -p MainPID --value "$SERVICE" 2>/dev/null | grep -qx "$pid"; then
        red "❌ ربات ارزش منطقه‌ای همین الان به‌صورت دستی اجراست (PID $pid). اول آن را ببندید:  kill $pid"
        CONFLICT=1
    fi
done

[ "$CONFLICT" -eq 0 ] || { red "نصب متوقف شد؛ موارد بالا را برطرف کنید."; exit 1; }

# گزارش ربات‌های دیگر (فقط اطلاع، بدون تغییر)
echo "   سرویس‌های ربات موجود روی سرور (دست نخورده باقی می‌مانند):"
systemctl list-units --type=service --all --no-legend 2>/dev/null \
    | grep -iE "bot|judicial|bale" | grep -v "$SERVICE" | sed 's/^/     • /' || echo "     (موردی پیدا نشد)"
if command -v pm2 >/dev/null; then pm2 ls 2>/dev/null | sed 's/^/     /' || true; fi
green "✅ تداخلی پیدا نشد."
echo

# ─────────────── ۳. venv و وابستگی‌ها ───────────────
echo "📦 نصب وابستگی‌ها در venv جداگانه..."
PY="$(command -v python3)"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
    red "❌ Python 3.9 یا بالاتر لازم است."; exit 1
fi
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    "$PY" -m venv "$APP_DIR/venv" 2>/dev/null || {
        apt-get update -qq && apt-get install -y -qq python3-venv && "$PY" -m venv "$APP_DIR/venv"; }
fi
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
mkdir -p "$APP_DIR/data"
chown -R "$RUN_USER": "$APP_DIR"
chmod +x "$APP_DIR/deploy/"*.sh
green "✅ وابستگی‌ها نصب شد."

# ─────────────── ۴. سرویس systemd ───────────────
echo "⚙️  ساخت سرویس $SERVICE ..."
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Bale bot - Regional Value (arzesh mantaghei)
After=network-online.target
Wants=network-online.target
# هرگز از ری‌استارت دست نکش (حتی بعد از کرش‌های پشت‌سرهم)
StartLimitIntervalSec=0

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
# systemd محیط تمیز می‌دهد (export‌های shell یا ربات اصلی به اینجا نشت نمی‌کنند)
# و config.py فقط .env کنار خودش را می‌خواند.
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=SERVICE_NAME=$SERVICE
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/main.py
# اطلاع به ادمین (+ یعنی با دسترسی root، برای خواندن لاگ)
ExecStartPost=+$APP_DIR/deploy/notify.sh start
ExecStopPost=+$APP_DIR/deploy/notify.sh stop
Restart=always
RestartSec=10
TimeoutStopSec=30
KillMode=mixed
# اگر حافظهٔ سرور کم شد، این ربات زودتر از ربات اصلی قربانی شود
OOMScoreAdjust=200
Nice=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE

[Install]
WantedBy=multi-user.target
EOF

# ─────────────── ۵. تایمر پایش ───────────────
cat > "/etc/systemd/system/$SERVICE-watchdog.service" <<EOF
[Unit]
Description=Health check for $SERVICE

[Service]
Type=oneshot
Environment=SERVICE_NAME=$SERVICE
ExecStart=$APP_DIR/deploy/notify.sh health
EOF

cat > "/etc/systemd/system/$SERVICE-watchdog.timer" <<EOF
[Unit]
Description=Check $SERVICE every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# ─────────────── ۶. دستور مدیریتی ───────────────
cat > /usr/local/bin/rvbot <<EOF
#!/usr/bin/env bash
S=$SERVICE; D=$APP_DIR; ST=/var/lib/\$S
mkdir -p "\$ST"
case "\${1:-status}" in
  status)  systemctl status \$S --no-pager -l | head -20 ;;
  logs)    journalctl -u \$S -f -n "\${2:-100}" ;;
  restart) rm -f "\$ST/manual_stop"; systemctl restart \$S && echo "ری‌استارت شد." ;;
  stop)    touch "\$ST/manual_stop"; systemctl stop \$S && echo "متوقف شد (پایشگر تا 'rvbot start' آن را بالا نمی‌آورد)." ;;
  start)   rm -f "\$ST/manual_stop"; systemctl start \$S && echo "اجرا شد." ;;
  update)  cd "\$D" && sudo -u $RUN_USER git pull --ff-only && "\$D/venv/bin/pip" install -q -r requirements.txt && systemctl restart \$S && echo "به‌روزرسانی و ری‌استارت شد." ;;
  test)    SERVICE_NAME=\$S "\$D/deploy/notify.sh" test ;;
  *) echo "rvbot status|logs [n]|restart|stop|start|update|test" ;;
esac
EOF
chmod +x /usr/local/bin/rvbot

mkdir -p "/var/lib/$SERVICE"
rm -f "/var/lib/$SERVICE/manual_stop"
systemctl daemon-reload
systemctl enable --now "$SERVICE-watchdog.timer" >/dev/null
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

sleep 8
echo
if systemctl is-active --quiet "$SERVICE"; then
    green "╔══════════════════════════════════════════════╗"
    green "║  ✅ ربات ارزش منطقه‌ای نصب و اجرا شد          ║"
    green "╚══════════════════════════════════════════════╝"
else
    red "⚠️  سرویس بالا نیامد. لاگ:"
    journalctl -u "$SERVICE" -n 30 --no-pager
fi
cat <<EOF

دستورها:
  rvbot status     وضعیت
  rvbot logs       لاگ زنده
  rvbot restart    ری‌استارت
  rvbot stop/start توقف/اجرا (پایشگر هنگام توقف دستی دخالت نمی‌کند)
  rvbot update     git pull + نصب وابستگی + ری‌استارت
  rvbot test       ارسال پیام آزمایشی به ادمین
EOF
