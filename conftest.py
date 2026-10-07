# -*- coding: utf-8 -*-
"""
تنظیمات مشترک تست‌ها (pytest این فایل را قبل از هر تستی بارگذاری می‌کند):
متغیرهای محیطی لازم برای config.py و یک پوشهٔ دادهٔ موقت جدا از دادهٔ واقعی ربات.
"""
import os
import tempfile

os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
os.environ["ADMIN_ID"] = "999"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="rv_test_data_")
os.environ.setdefault("BOT_USERNAME", "Regionalvalue_bot")
os.environ.setdefault("NESHAN_API_KEY", "")
