# نصب روی VPS (کنار ربات اصلی)

```bash
cd /opt
sudo git clone https://github.com/hadimhm2000/regional_value.git
cd regional_value
sudo bash deploy/install.sh      # بار اول فقط .env را می‌سازد
sudo nano .env                   # BOT_TOKEN, ADMIN_ID, NESHAN_API_KEY, BALE_WALLET_TOKEN
sudo bash deploy/install.sh      # نصب و اجرا
rvbot test                       # باید پیام آزمایشی در بله برسد
```

> ادمین باید یک بار ربات ارزش منطقه‌ای را در بله `/start` کرده باشد تا پیام‌ها برسد.

## چه چیزی نصب می‌شود
| بخش | کار |
|---|---|
| `regional-value-bot.service` | اجرای دائمی، `Restart=always` (۱۰ ثانیه پس از هر قطعی)، اجرای خودکار پس از ریبوت |
| `notify.sh` | پیام بله به ادمین: قطعی (با علت و آخرین لاگ‌ها)، راه‌اندازی دوباره، ریبوت سرور |
| `regional-value-bot-watchdog.timer` | هر ۵ دقیقه: اگر سرویس پایین بود بالا می‌آورد و خبر می‌دهد |
| `rvbot` | `status`، `logs`، `restart`، `stop`، `start`، `update`، `test` |

## بررسی تداخل با ربات اصلی
| مورد | وضعیت |
|---|---|
| توکن | **باید جدا باشد**؛ اسکریپت `.env`های دیگر سرور را چک می‌کند و در صورت تکرار متوقف می‌شود |
| پورت | این ربات پورتی باز نمی‌کند (polling)؛ پنل ربات اصلی روی 3000 دست نمی‌خورد |
| دیتابیس | `data/bot.sqlite3` داخل پوشهٔ همین ربات؛ مستقل از ربات اصلی |
| پکیج‌ها | venv جداگانه؛ نسخه‌های aiogram/playwright ربات اصلی تغییر نمی‌کند |
| متغیرهای محیطی | systemd محیط تمیز می‌دهد و هر ربات فقط `.env` کنار خودش را می‌خواند |
| حافظه | `OOMScoreAdjust=200` و `Nice=5`: در کمبود منابع، ربات اصلی (Chromium) اولویت دارد |

کرش‌های پشت‌سرهم: در هر ساعت ۳ پیام اول ارسال می‌شود و بعد هر دهمین، تا پیام‌ها سیل نشوند.
