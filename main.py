#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🤖 Ultimate Media Downloader Telegram Bot
Supports: TikTok, Instagram, Facebook, YouTube, Pinterest, Twitter/X, Reddit & more
Deploy: render.com
"""

import os
import json
import time
import logging
import threading
import tempfile
import shutil
import re
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
import requests
import telebot
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from flask import Flask, request as flask_request, jsonify
import yt_dlp

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  CONFIG FROM ENVIRONMENT
# ─────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID   = os.environ.get("CHENEL_ID", "")          # e.g. @mychannel
ADMIN_USERID = os.environ.get("ADMIN_USERID", "")        # single admin user id string
ADMIN_IDS_RAW= os.environ.get("ADMIN_IDS", "")          # comma-separated admin ids
PORT = int(os.environ.get("PORT") or 10000)
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL", "")         # optional for webhook mode

ADMIN_IDS: set[int] = set()
for _id in ADMIN_IDS_RAW.split(","):
    _id = _id.strip()
    if _id.isdigit():
        ADMIN_IDS.add(int(_id))
if ADMIN_USERID and ADMIN_USERID.strip().isdigit():
    ADMIN_IDS.add(int(ADMIN_USERID.strip()))

# ─────────────────────────────────────────────
#  LOCAL STORAGE (JSON-based persistence)
# ─────────────────────────────────────────────
DATA_DIR   = Path("/app/data") if Path("/app").exists() else Path("./data")
TEMP_DIR   = Path("/app/temp") if Path("/app").exists() else Path("./temp")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE    = DATA_DIR / "users.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOGS_FILE     = DATA_DIR / "download_logs.json"
HISTORY_FILE  = DATA_DIR / "history.json"
BANNED_FILE   = DATA_DIR / "banned.json"


def _load(path: Path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _save(path: Path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Save error {path}: {e}")


# ─── Storage helpers ───
def get_users() -> dict:      return _load(USERS_FILE, {})
def save_users(d):            _save(USERS_FILE, d)
def get_settings() -> dict:   return _load(SETTINGS_FILE, _default_settings())
def save_settings(d):         _save(SETTINGS_FILE, d)
def get_logs() -> list:       return _load(LOGS_FILE, [])
def save_logs(d):             _save(LOGS_FILE, d[-500:])   # keep last 500
def get_history() -> dict:    return _load(HISTORY_FILE, {})
def save_history(d):          _save(HISTORY_FILE, d)
def get_banned() -> list:     return _load(BANNED_FILE, [])
def save_banned(d):           _save(BANNED_FILE, d)


def _default_settings() -> dict:
    return {
        "welcome_message": (
            "👋 *Welcome to Ultimate Downloader Bot!*\n\n"
            "I can download videos, photos, stories, reels, audio and more from:\n"
            "🎵 TikTok | 📸 Instagram | 📘 Facebook\n"
            "▶️ YouTube | 📌 Pinterest | 🐦 Twitter/X | 🤖 Reddit\n\n"
            "Just paste any URL or use the menu below! 🚀"
        ),
        "help_message": (
            "🆘 *Help & Commands*\n\n"
            "📥 *Download*: Paste any URL to auto-detect & download\n"
            "🔗 *Paste URL*: Manual URL input\n"
            "📋 *Copy Tools*: Copy captions, links, metadata\n"
            "🧠 *AI Tools*: Summarize, generate captions, hashtags\n"
            "👤 *My Account*: Profile & download history\n"
            "⚙️ *Settings*: Preferences\n\n"
            "*Supported Sites:* TikTok, Instagram, Facebook, YouTube,\n"
            "Pinterest, Twitter/X, Reddit & 1000+ more via yt-dlp!\n\n"
            "🛠 *Commands:*\n"
            "/start — Home menu\n"
            "/help — This help\n"
            "/history — Your downloads\n"
            "/account — Your account info\n"
            "/cancel — Cancel current operation"
        ),
        "about_message": (
            "ℹ️ *About This Bot*\n\n"
            "🤖 Ultimate Media Downloader Bot\n"
            "📦 Version: 2.0\n"
            "🌐 Supports 1000+ websites\n"
            "⚡ Powered by yt-dlp & aiohttp\n\n"
            "Developed with ❤️"
        ),
        "maintenance_mode": False,
        "max_file_size_mb": 50,
        "default_quality": "best",
    }


def register_user(user: types.User):
    users = get_users()
    uid = str(user.id)
    if uid not in users:
        users[uid] = {
            "id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "joined": datetime.now().isoformat(),
            "downloads": 0,
            "last_active": datetime.now().isoformat(),
        }
    else:
        users[uid]["last_active"] = datetime.now().isoformat()
        users[uid]["username"] = user.username or users[uid].get("username", "")
    save_users(users)


def log_download(user_id: int, url: str, platform: str, status: str, file_type: str = ""):
    logs = get_logs()
    logs.append({
        "user_id": user_id,
        "url": url[:200],
        "platform": platform,
        "status": status,
        "file_type": file_type,
        "time": datetime.now().isoformat(),
    })
    save_logs(logs)
    # increment user download count
    users = get_users()
    uid = str(user_id)
    if uid in users:
        users[uid]["downloads"] = users[uid].get("downloads", 0) + 1
        save_users(users)
    # save to personal history
    history = get_history()
    if uid not in history:
        history[uid] = []
    history[uid].insert(0, {
        "url": url[:200],
        "platform": platform,
        "status": status,
        "file_type": file_type,
        "time": datetime.now().isoformat(),
    })
    history[uid] = history[uid][:50]   # keep last 50 per user
    save_history(history)


def is_banned(user_id: int) -> bool:
    return user_id in get_banned()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ─────────────────────────────────────────────
#  URL PLATFORM DETECTOR
# ─────────────────────────────────────────────
PLATFORM_PATTERNS = {
    "tiktok":    [r"tiktok\.com", r"vm\.tiktok\.com", r"vt\.tiktok\.com"],
    "instagram": [r"instagram\.com", r"instagr\.am"],
    "facebook":  [r"facebook\.com", r"fb\.com", r"fb\.watch", r"m\.facebook\.com"],
    "youtube":   [r"youtube\.com", r"youtu\.be", r"youtube-nocookie\.com"],
    "pinterest": [r"pinterest\.com", r"pin\.it", r"pinterest\.\w+"],
    "twitter":   [r"twitter\.com", r"x\.com", r"t\.co"],
    "reddit":    [r"reddit\.com", r"redd\.it", r"v\.redd\.it"],
}


def detect_platform(url: str) -> str:
    url_lower = url.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url_lower):
                return platform
    return "other"


def is_valid_url(text: str) -> bool:
    try:
        result = urlparse(text.strip())
        return result.scheme in ("http", "https") and bool(result.netloc)
    except Exception:
        return False


def extract_urls(text: str) -> list[str]:
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    return re.findall(url_pattern, text)


# ─────────────────────────────────────────────
#  YT-DLP DOWNLOADER
# ─────────────────────────────────────────────
def get_ydl_opts(output_path: str, quality: str = "best", audio_only: bool = False,
                 format_str: str = None) -> dict:
    if audio_only:
        fmt = "bestaudio/best"
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        if format_str:
            fmt = format_str
        elif quality == "4k":
            fmt = "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best"
        elif quality == "1080":
            fmt = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        elif quality == "720":
            fmt = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        elif quality == "480":
            fmt = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        else:
            fmt = "bestvideo+bestaudio/best"
        postprocessors = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]

    return {
        "format": fmt,
        "outtmpl": os.path.join(output_path, "%(title).50s.%(ext)s"),
        "postprocessors": postprocessors,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": 50 * 1024 * 1024,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }


def fetch_info(url: str) -> dict | None:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.warning(f"fetch_info error: {e}")
        return None


def download_media(url: str, quality: str = "best", audio_only: bool = False) -> dict:
    """Returns dict with keys: success, files, info, error"""
    tmp = tempfile.mkdtemp(dir=TEMP_DIR)
    try:
        opts = get_ydl_opts(tmp, quality=quality, audio_only=audio_only)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        files = list(Path(tmp).glob("*"))
        if not files:
            return {"success": False, "error": "No files downloaded", "files": [], "info": info}

        return {"success": True, "files": [str(f) for f in files], "info": info, "tmp": tmp}
    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        msg = str(e)
        if "HTTP Error 429" in msg:
            return {"success": False, "error": "⚠️ Rate limited. Please try again later.", "files": [], "info": None}
        if "Private video" in msg or "private" in msg.lower():
            return {"success": False, "error": "🔒 This content is private.", "files": [], "info": None}
        return {"success": False, "error": f"Download failed: {str(e)[:200]}", "files": [], "info": None}
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"success": False, "error": str(e)[:200], "files": [], "info": None}


def download_thumbnail(url: str) -> str | None:
    info = fetch_info(url)
    if not info:
        return None
    thumb_url = info.get("thumbnail") or (info.get("thumbnails") or [{}])[-1].get("url")
    if not thumb_url:
        return None
    try:
        tmp = tempfile.mktemp(dir=TEMP_DIR, suffix=".jpg")
        r = requests.get(thumb_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0"
        })
        r.raise_for_status()
        with open(tmp, "wb") as f:
            f.write(r.content)
        return tmp
    except Exception as e:
        logger.warning(f"Thumbnail dl error: {e}")
        return None


# ─────────────────────────────────────────────
#  BOT INIT
# ─────────────────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# user state machine
user_states: dict[int, dict] = {}


def set_state(uid: int, state: str, data: dict = None):
    user_states[uid] = {"state": state, "data": data or {}, "ts": time.time()}


def get_state(uid: int) -> dict:
    return user_states.get(uid, {"state": "idle", "data": {}})


def clear_state(uid: int):
    user_states.pop(uid, None)


# ─────────────────────────────────────────────
#  KEYBOARDS
# ─────────────────────────────────────────────
def main_menu_kb() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("📥 Download"),
        KeyboardButton("🔗 Paste URL"),
        KeyboardButton("📋 Copy Tools"),
        KeyboardButton("🧠 AI Tools"),
        KeyboardButton("👤 My Account"),
        KeyboardButton("⚙️ Settings"),
        KeyboardButton("🆘 Help"),
    )
    return kb


def download_menu_ik() -> InlineKeyboardMarkup:
    ik = InlineKeyboardMarkup(row_width=2)
    ik.add(
        InlineKeyboardButton("🎵 TikTok",    callback_data="plat_tiktok"),
        InlineKeyboardButton("📸 Instagram", callback_data="plat_instagram"),
        InlineKeyboardButton("📘 Facebook",  callback_data="plat_facebook"),
        InlineKeyboardButton("▶️ YouTube",   callback_data="plat_youtube"),
        InlineKeyboardButton("📌 Pinterest", callback_data="plat_pinterest"),
        InlineKeyboardButton("🐦 Twitter/X", callback_data="plat_twitter"),
        InlineKeyboardButton("🤖 Reddit",    callback_data="plat_reddit"),
        InlineKeyboardButton("🌐 Other URL", callback_data="plat_other"),
    )
    ik.add(InlineKeyboardButton("⬅️ Back", callback_data="back_home"))
    return ik


def platform_actions_ik(platform: str) -> InlineKeyboardMarkup:
    ik = InlineKeyboardMarkup(row_width=2)
    actions = {
        "tiktok": [
            ("📹 Video Download", "act_video"),
            ("🔥 HD Download",    "act_hd"),
            ("🎵 MP3 Audio",      "act_audio"),
            ("🖼 Thumbnail",      "act_thumbnail"),
            ("📝 Caption",        "act_caption"),
            ("#️⃣ Hashtags",      "act_hashtags"),
            ("👤 Profile Pic",    "act_profilepic"),
        ],
        "instagram": [
            ("📸 Post Download",     "act_video"),
            ("🎞 Reel Download",     "act_hd"),
            ("📖 Story Download",    "act_story"),
            ("🖼 Carousel Download", "act_carousel"),
            ("👤 Profile Pic",       "act_profilepic"),
            ("📝 Caption",           "act_caption"),
            ("#️⃣ Hashtags",         "act_hashtags"),
            ("🎵 Audio/Music",       "act_audio"),
        ],
        "facebook": [
            ("📹 Video Download", "act_video"),
            ("🖼 Photo Download",  "act_photo"),
            ("📖 Story Download",  "act_story"),
            ("🔥 HD Download",    "act_hd"),
            ("🎵 MP3",            "act_audio"),
        ],
        "youtube": [
            ("📹 Video Download",   "act_video"),
            ("🎵 Audio MP3",        "act_audio"),
            ("🖼 Thumbnail",        "act_thumbnail"),
            ("📱 Shorts Download",  "act_hd"),
            ("📋 Playlist Download","act_playlist"),
            ("🔥 4K Download",      "act_4k"),
        ],
        "pinterest": [
            ("🖼 Image Download",  "act_photo"),
            ("📹 Video Download",  "act_video"),
            ("📦 Batch Download",  "act_batch"),
        ],
        "twitter": [
            ("📹 Video Download", "act_video"),
            ("🖼 Image Download",  "act_photo"),
            ("🎞 GIF Download",   "act_gif"),
            ("📦 All Media",      "act_all"),
        ],
        "reddit": [
            ("📹 Video Download",   "act_video"),
            ("🖼 Image Download",   "act_photo"),
            ("🖼 Gallery Download", "act_carousel"),
            ("📝 Post Text",        "act_caption"),
        ],
        "other": [
            ("⬇️ Direct Download",  "act_video"),
            ("🖼 Media Detect",     "act_photo"),
            ("🎵 Extract Audio",    "act_audio"),
            ("🔍 Metadata Extract", "act_caption"),
        ],
    }
    btns = actions.get(platform, actions["other"])
    for label, cb in btns:
        ik.add(InlineKeyboardButton(label, callback_data=f"{cb}_{platform}"))
    ik.add(
        InlineKeyboardButton("🎞 Choose Quality", callback_data=f"quality_{platform}"),
        InlineKeyboardButton("⬅️ Back",           callback_data="menu_download"),
    )
    return ik


def quality_ik(platform: str) -> InlineKeyboardMarkup:
    ik = InlineKeyboardMarkup(row_width=2)
    ik.add(
        InlineKeyboardButton("🔵 Best Quality", callback_data=f"dl_best_{platform}"),
        InlineKeyboardButton("🟢 1080p",        callback_data=f"dl_1080_{platform}"),
        InlineKeyboardButton("🟡 720p",         callback_data=f"dl_720_{platform}"),
        InlineKeyboardButton("🔴 480p",         callback_data=f"dl_480_{platform}"),
        InlineKeyboardButton("⭐ 4K",           callback_data=f"dl_4k_{platform}"),
    )
    ik.add(InlineKeyboardButton("⬅️ Back", callback_data=f"plat_{platform}"))
    return ik


def copy_tools_ik() -> InlineKeyboardMarkup:
    ik = InlineKeyboardMarkup(row_width=2)
    ik.add(
        InlineKeyboardButton("📋 Copy URL",          callback_data="copy_url"),
        InlineKeyboardButton("📋 Copy Caption",      callback_data="copy_caption"),
        InlineKeyboardButton("📋 Copy Title",        callback_data="copy_title"),
        InlineKeyboardButton("📋 Copy Description",  callback_data="copy_description"),
        InlineKeyboardButton("#️⃣ Copy Hashtags",    callback_data="copy_hashtags"),
        InlineKeyboardButton("📋 Copy All Text",     callback_data="copy_all"),
        InlineKeyboardButton("🎬 Copy Video Link",   callback_data="copy_videolink"),
        InlineKeyboardButton("🖼 Copy Image Link",   callback_data="copy_imagelink"),
        InlineKeyboardButton("🎵 Copy Audio Link",   callback_data="copy_audiolink"),
        InlineKeyboardButton("🖼 Copy Thumbnail",    callback_data="copy_thumbnail"),
        InlineKeyboardButton("🧾 Copy Metadata",     callback_data="copy_metadata"),
        InlineKeyboardButton("📊 Copy File Info",    callback_data="copy_fileinfo"),
    )
    ik.add(InlineKeyboardButton("⬅️ Back", callback_data="back_home"))
    return ik


def ai_tools_ik() -> InlineKeyboardMarkup:
    ik = InlineKeyboardMarkup(row_width=2)
    ik.add(
        InlineKeyboardButton("📝 Video Summary",     callback_data="ai_summary"),
        InlineKeyboardButton("✍️ Caption Generator", callback_data="ai_caption"),
        InlineKeyboardButton("#️⃣ Hashtag Generator", callback_data="ai_hashtag"),
        InlineKeyboardButton("🔍 Content Analyzer",  callback_data="ai_analyze"),
        InlineKeyboardButton("🔤 OCR Text Extract",  callback_data="ai_ocr"),
        InlineKeyboardButton("🖼 Image Description", callback_data="ai_imgdesc"),
        InlineKeyboardButton("🏷 Title Generator",   callback_data="ai_title"),
    )
    ik.add(InlineKeyboardButton("⬅️ Back", callback_data="back_home"))
    return ik


def after_download_ik(url: str, platform: str) -> InlineKeyboardMarkup:
    ik = InlineKeyboardMarkup(row_width=2)
    ik.add(
        InlineKeyboardButton("📋 Copy URL",       callback_data="copy_url"),
        InlineKeyboardButton("📝 Copy Caption",   callback_data="copy_caption"),
        InlineKeyboardButton("🎞 Choose Quality", callback_data=f"quality_{platform}"),
        InlineKeyboardButton("🎵 Extract Audio",  callback_data=f"act_audio_{platform}"),
        InlineKeyboardButton("🖼 Thumbnail",      callback_data=f"act_thumbnail_{platform}"),
        InlineKeyboardButton("🔁 More Options",   callback_data=f"plat_{platform}"),
        InlineKeyboardButton("⭐ Save Favorite",  callback_data="fav_add"),
        InlineKeyboardButton("🕘 History",        callback_data="menu_history"),
    )
    ik.add(InlineKeyboardButton("🏠 Home", callback_data="back_home"))
    return ik


def admin_menu_ik() -> InlineKeyboardMarkup:
    ik = InlineKeyboardMarkup(row_width=2)
    ik.add(
        InlineKeyboardButton("📣 Broadcast",       callback_data="admin_broadcast"),
        InlineKeyboardButton("👥 User Stats",      callback_data="admin_userstats"),
        InlineKeyboardButton("📊 Analytics",       callback_data="admin_analytics"),
        InlineKeyboardButton("📋 Download Logs",   callback_data="admin_logs"),
        InlineKeyboardButton("🚫 Ban User",        callback_data="admin_ban"),
        InlineKeyboardButton("✅ Unban User",      callback_data="admin_unban"),
        InlineKeyboardButton("✏️ Edit Welcome",    callback_data="admin_edit_welcome"),
        InlineKeyboardButton("✏️ Edit Help",       callback_data="admin_edit_help"),
        InlineKeyboardButton("✏️ Edit About",      callback_data="admin_edit_about"),
        InlineKeyboardButton("🔧 Maintenance",     callback_data="admin_maintenance"),
        InlineKeyboardButton("📢 Channel Promo",   callback_data="admin_promo"),
        InlineKeyboardButton("📋 Copy Logs",       callback_data="admin_copy_logs"),
    )
    return ik


# ─────────────────────────────────────────────
#  HELPERS — send file safely
# ─────────────────────────────────────────────
def send_file_safe(chat_id: int, file_path: str, caption: str = "", reply_markup=None):
    """Send video/audio/photo/document based on extension."""
    p = Path(file_path)
    ext = p.suffix.lower()
    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > 50:
        bot.send_message(chat_id, f"⚠️ File too large ({size_mb:.1f} MB). Telegram limit is 50 MB.")
        return False
    try:
        with open(file_path, "rb") as f:
            if ext in (".mp4", ".mkv", ".webm", ".mov", ".avi"):
                bot.send_video(chat_id, f, caption=caption[:1024], reply_markup=reply_markup,
                               supports_streaming=True)
            elif ext in (".mp3", ".m4a", ".ogg", ".wav", ".opus"):
                bot.send_audio(chat_id, f, caption=caption[:1024], reply_markup=reply_markup)
            elif ext in (".jpg", ".jpeg", ".png", ".webp"):
                bot.send_photo(chat_id, f, caption=caption[:1024], reply_markup=reply_markup)
            elif ext == ".gif":
                bot.send_animation(chat_id, f, caption=caption[:1024], reply_markup=reply_markup)
            else:
                bot.send_document(chat_id, f, caption=caption[:1024], reply_markup=reply_markup)
        return True
    except Exception as e:
        logger.error(f"send_file_safe error: {e}")
        bot.send_message(chat_id, f"❌ Failed to send file: {str(e)[:200]}")
        return False


def build_info_text(info: dict, platform: str) -> str:
    title    = info.get("title", "N/A")[:100]
    uploader = info.get("uploader") or info.get("channel") or "N/A"
    duration = info.get("duration")
    view_cnt = info.get("view_count")
    like_cnt = info.get("like_count")
    desc     = (info.get("description") or "")[:300]
    tags     = info.get("tags") or []
    hashtags = " ".join(f"#{t}" for t in tags[:10])

    dur_str = ""
    if duration:
        m, s = divmod(int(duration), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    lines = [f"📹 *{title}*", f"👤 {uploader}"]
    if dur_str:    lines.append(f"⏱ Duration: `{dur_str}`")
    if view_cnt:   lines.append(f"👁 Views: `{view_cnt:,}`")
    if like_cnt:   lines.append(f"❤️ Likes: `{like_cnt:,}`")
    if desc:       lines.append(f"\n📝 _{desc}_")
    if hashtags:   lines.append(f"\n{hashtags}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  DOWNLOAD WORKER
# ─────────────────────────────────────────────
def do_download(chat_id: int, user_id: int, url: str, platform: str,
                quality: str = "best", audio_only: bool = False,
                thumbnail_only: bool = False, caption_only: bool = False):
    status_msg = bot.send_message(chat_id, "⏳ Processing your request...")

    # Fetch info first
    bot.edit_message_text("🔍 Fetching media info...", chat_id, status_msg.message_id)
    info = fetch_info(url)

    if caption_only:
        if info:
            text = build_info_text(info, platform)
            # store in state for copy tools
            set_state(user_id, "has_result", {
                "url": url, "platform": platform, "info": {
                    "title": info.get("title",""),
                    "description": info.get("description",""),
                    "tags": info.get("tags",[]),
                    "uploader": info.get("uploader",""),
                    "thumbnail": info.get("thumbnail",""),
                }
            })
            bot.delete_message(chat_id, status_msg.message_id)
            bot.send_message(chat_id, text, reply_markup=after_download_ik(url, platform))
        else:
            bot.edit_message_text("❌ Could not fetch info for this URL.", chat_id, status_msg.message_id)
        return

    if thumbnail_only:
        bot.edit_message_text("🖼 Downloading thumbnail...", chat_id, status_msg.message_id)
        thumb = download_thumbnail(url)
        bot.delete_message(chat_id, status_msg.message_id)
        if thumb:
            caption = f"🖼 Thumbnail\n📋 `{url}`"
            send_file_safe(chat_id, thumb, caption=caption)
            Path(thumb).unlink(missing_ok=True)
            log_download(user_id, url, platform, "success", "thumbnail")
        else:
            bot.send_message(chat_id, "❌ Could not download thumbnail.")
            log_download(user_id, url, platform, "failed", "thumbnail")
        return

    if info:
        preview = build_info_text(info, platform)
        bot.edit_message_text(f"✅ Found media!\n\n{preview}\n\n⬇️ Downloading...",
                              chat_id, status_msg.message_id)
    else:
        bot.edit_message_text("⬇️ Downloading...", chat_id, status_msg.message_id)

    result = download_media(url, quality=quality, audio_only=audio_only)

    if not result["success"]:
        bot.edit_message_text(f"❌ {result['error']}", chat_id, status_msg.message_id)
        log_download(user_id, url, platform, "failed")
        return

    bot.edit_message_text("📤 Sending file...", chat_id, status_msg.message_id)

    sent_any = False
    for fpath in result["files"]:
        if Path(fpath).exists():
            caption = ""
            if info:
                caption = f"📹 {info.get('title','')[:100]}\n📋 `{url}`"
            ok = send_file_safe(chat_id, fpath, caption=caption,
                                reply_markup=after_download_ik(url, platform))
            if ok:
                sent_any = True

    # store state for copy tools
    if info:
        set_state(user_id, "has_result", {
            "url": url, "platform": platform, "info": {
                "title": info.get("title",""),
                "description": info.get("description",""),
                "tags": info.get("tags",[]),
                "uploader": info.get("uploader",""),
                "thumbnail": info.get("thumbnail",""),
            }
        })

    # cleanup
    tmp = result.get("tmp")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)

    bot.delete_message(chat_id, status_msg.message_id)
    if not sent_any:
        bot.send_message(chat_id, "❌ No files were sent. The media may be unavailable.")
        log_download(user_id, url, platform, "failed")
    else:
        log_download(user_id, url, platform, "success", "audio" if audio_only else "video")

    # channel promotion
    if CHANNEL_ID:
        try:
            promo = (f"🎉 Someone just downloaded from *{platform.title()}*!\n"
                     f"Try our bot → @{bot.get_me().username}")
            bot.send_message(CHANNEL_ID, promo)
        except Exception:
            pass


# ─────────────────────────────────────────────
#  COMMAND HANDLERS
# ─────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg: types.Message):
    if is_banned(msg.from_user.id):
        bot.reply_to(msg, "🚫 You are banned from using this bot.")
        return
    register_user(msg.from_user)
    clear_state(msg.from_user.id)
    settings = get_settings()
    text = settings.get("welcome_message", _default_settings()["welcome_message"])
    bot.send_message(msg.chat.id, text, reply_markup=main_menu_kb())


@bot.message_handler(commands=["help"])
def cmd_help(msg: types.Message):
    settings = get_settings()
    text = settings.get("help_message", _default_settings()["help_message"])
    bot.send_message(msg.chat.id, text, reply_markup=main_menu_kb())


@bot.message_handler(commands=["admin"])
def cmd_admin(msg: types.Message):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Access denied.")
        return
    users = get_users()
    logs  = get_logs()
    banned = get_banned()
    text = (
        "🔐 *Admin Panel*\n\n"
        f"👥 Total Users: `{len(users)}`\n"
        f"📥 Total Downloads: `{len(logs)}`\n"
        f"🚫 Banned Users: `{len(banned)}`\n"
        f"⏰ Time: `{datetime.now().strftime('%Y-%m-%d %H:%M')}`"
    )
    bot.send_message(msg.chat.id, text, reply_markup=admin_menu_ik())


@bot.message_handler(commands=["history"])
def cmd_history(msg: types.Message):
    history = get_history()
    uid = str(msg.from_user.id)
    items = history.get(uid, [])
    if not items:
        bot.reply_to(msg, "📭 No download history yet!")
        return
    lines = ["🕘 *Your Download History*\n"]
    for i, item in enumerate(items[:10], 1):
        status_icon = "✅" if item.get("status") == "success" else "❌"
        lines.append(f"{i}. {status_icon} `{item['platform'].upper()}` — {item['time'][:10]}")
        lines.append(f"   🔗 `{item['url'][:60]}...`\n")
    bot.send_message(msg.chat.id, "\n".join(lines))


@bot.message_handler(commands=["account"])
def cmd_account(msg: types.Message):
    users = get_users()
    uid = str(msg.from_user.id)
    u = users.get(uid, {})
    history = get_history()
    dl_count = len(history.get(uid, []))
    text = (
        "👤 *My Account*\n\n"
        f"🆔 ID: `{msg.from_user.id}`\n"
        f"👤 Name: {msg.from_user.first_name}\n"
        f"📛 Username: @{msg.from_user.username or 'N/A'}\n"
        f"📅 Joined: {u.get('joined','N/A')[:10]}\n"
        f"📥 Downloads: `{dl_count}`\n"
        f"🔑 Admin: {'✅' if is_admin(msg.from_user.id) else '❌'}"
    )
    bot.send_message(msg.chat.id, text)


@bot.message_handler(commands=["cancel"])
def cmd_cancel(msg: types.Message):
    clear_state(msg.from_user.id)
    bot.reply_to(msg, "✅ Cancelled. Use /start to go back to menu.", reply_markup=main_menu_kb())


@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(msg: types.Message):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Access denied.")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(msg, "Usage: /broadcast <message>")
        return
    broadcast_text = parts[1]
    users = get_users()
    sent = 0
    failed = 0
    for uid_str in users:
        try:
            bot.send_message(int(uid_str), f"📣 *Broadcast*\n\n{broadcast_text}")
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    bot.reply_to(msg, f"📣 Broadcast done!\n✅ Sent: {sent}\n❌ Failed: {failed}")


@bot.message_handler(commands=["ban"])
def cmd_ban(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(msg, "Usage: /ban <user_id>")
        return
    uid = int(parts[1])
    banned = get_banned()
    if uid not in banned:
        banned.append(uid)
        save_banned(banned)
    bot.reply_to(msg, f"🚫 User `{uid}` banned.")


@bot.message_handler(commands=["unban"])
def cmd_unban(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(msg, "Usage: /unban <user_id>")
        return
    uid = int(parts[1])
    banned = get_banned()
    if uid in banned:
        banned.remove(uid)
        save_banned(banned)
    bot.reply_to(msg, f"✅ User `{uid}` unbanned.")


@bot.message_handler(commands=["stats"])
def cmd_stats(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    users = get_users()
    logs  = get_logs()
    banned = get_banned()
    platform_counts: dict[str, int] = {}
    for log in logs:
        p = log.get("platform", "other")
        platform_counts[p] = platform_counts.get(p, 0) + 1
    top = sorted(platform_counts.items(), key=lambda x: x[1], reverse=True)
    top_str = "\n".join(f"  • {p.title()}: {c}" for p, c in top[:5])
    text = (
        "📊 *Analytics Dashboard*\n\n"
        f"👥 Users: `{len(users)}`\n"
        f"📥 Total Downloads: `{len(logs)}`\n"
        f"🚫 Banned: `{len(banned)}`\n\n"
        f"🏆 *Top Platforms:*\n{top_str}"
    )
    bot.send_message(msg.chat.id, text)


# ─────────────────────────────────────────────
#  ADMIN EDIT COMMANDS
# ─────────────────────────────────────────────
@bot.message_handler(commands=["setwelcome"])
def cmd_setwelcome(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(msg, "Usage: /setwelcome <new welcome message>")
        return
    s = get_settings()
    s["welcome_message"] = parts[1]
    save_settings(s)
    bot.reply_to(msg, "✅ Welcome message updated!")


@bot.message_handler(commands=["sethelp"])
def cmd_sethelp(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(msg, "Usage: /sethelp <new help message>")
        return
    s = get_settings()
    s["help_message"] = parts[1]
    save_settings(s)
    bot.reply_to(msg, "✅ Help message updated!")


@bot.message_handler(commands=["setabout"])
def cmd_setabout(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(msg, "Usage: /setabout <new about message>")
        return
    s = get_settings()
    s["about_message"] = parts[1]
    save_settings(s)
    bot.reply_to(msg, "✅ About message updated!")


@bot.message_handler(commands=["setquality"])
def cmd_setquality(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    valid = ["best", "1080", "720", "480", "4k"]
    if len(parts) < 2 or parts[1] not in valid:
        bot.reply_to(msg, f"Usage: /setquality <{' | '.join(valid)}>")
        return
    s = get_settings()
    s["default_quality"] = parts[1]
    save_settings(s)
    bot.reply_to(msg, f"✅ Default quality set to `{parts[1]}`")


@bot.message_handler(commands=["maintenance"])
def cmd_maintenance(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    s = get_settings()
    s["maintenance_mode"] = not s.get("maintenance_mode", False)
    save_settings(s)
    state = "ON 🔧" if s["maintenance_mode"] else "OFF ✅"
    bot.reply_to(msg, f"Maintenance mode: {state}")


# ─────────────────────────────────────────────
#  CALLBACK HANDLER
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call: types.CallbackQuery):
    uid  = call.from_user.id
    cid  = call.message.chat.id
    data = call.data

    if is_banned(uid):
        bot.answer_callback_query(call.id, "🚫 You are banned.")
        return

    # ── Navigation ──
    if data == "back_home":
        bot.answer_callback_query(call.id)
        settings = get_settings()
        text = settings.get("welcome_message", _default_settings()["welcome_message"])
        try:
            bot.edit_message_text(text, cid, call.message.message_id,
                                  reply_markup=None)
        except Exception:
            bot.send_message(cid, text, reply_markup=main_menu_kb())
        return

    if data == "menu_download":
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text("📥 *Choose Platform*", cid, call.message.message_id,
                                  reply_markup=download_menu_ik())
        except Exception:
            bot.send_message(cid, "📥 *Choose Platform*", reply_markup=download_menu_ik())
        return

    if data == "menu_history":
        bot.answer_callback_query(call.id)
        history = get_history()
        items = history.get(str(uid), [])
        if not items:
            bot.answer_callback_query(call.id, "📭 No history yet!", show_alert=True)
            return
        lines = ["🕘 *Recent Downloads*\n"]
        for i, item in enumerate(items[:5], 1):
            icon = "✅" if item.get("status") == "success" else "❌"
            lines.append(f"{i}. {icon} {item['platform'].upper()} — {item['time'][:10]}")
        bot.send_message(cid, "\n".join(lines))
        return

    # ── Platform selection ──
    if data.startswith("plat_"):
        platform = data.replace("plat_", "")
        bot.answer_callback_query(call.id)
        set_state(uid, f"waiting_url_{platform}", {"platform": platform})
        PLATFORM_ICONS = {
            "tiktok": "🎵", "instagram": "📸", "facebook": "📘",
            "youtube": "▶️", "pinterest": "📌", "twitter": "🐦",
            "reddit": "🤖", "other": "🌐",
        }
        icon = PLATFORM_ICONS.get(platform, "🌐")
        try:
            bot.edit_message_text(
                f"{icon} *{platform.title()}*\n\nChoose an action or paste a URL directly:",
                cid, call.message.message_id,
                reply_markup=platform_actions_ik(platform)
            )
        except Exception:
            bot.send_message(cid, f"{icon} Choose action:", reply_markup=platform_actions_ik(platform))
        return

    # ── Quality selector ──
    if data.startswith("quality_"):
        platform = data.replace("quality_", "")
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text("🎞 *Select Quality*", cid, call.message.message_id,
                                  reply_markup=quality_ik(platform))
        except Exception:
            bot.send_message(cid, "🎞 *Select Quality*", reply_markup=quality_ik(platform))
        return

    # ── Quality download ──
    if data.startswith("dl_"):
        parts = data.split("_", 2)
        quality  = parts[1] if len(parts) > 1 else "best"
        platform = parts[2] if len(parts) > 2 else "other"
        state = get_state(uid)
        url = state.get("data", {}).get("url")
        if not url:
            bot.answer_callback_query(call.id, "⚠️ Please paste a URL first!", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"⬇️ Downloading in {quality}...")
        threading.Thread(
            target=do_download,
            args=(cid, uid, url, platform, quality, False, False, False),
            daemon=True
        ).start()
        return

    # ── Action callbacks ──
    if data.startswith("act_"):
        parts = data.split("_", 2)
        action   = parts[1] if len(parts) > 1 else "video"
        platform = parts[2] if len(parts) > 2 else "other"
        state = get_state(uid)
        url = state.get("data", {}).get("url")
        if not url:
            bot.answer_callback_query(call.id, "⚠️ No URL detected! Paste a URL first.", show_alert=True)
            set_state(uid, f"waiting_url_{platform}", {"platform": platform})
            bot.send_message(cid, "🔗 Please paste your URL:")
            return
        bot.answer_callback_query(call.id, "⏳ Processing...")
        audio_only     = action in ("audio",)
        thumbnail_only = action in ("thumbnail",)
        caption_only   = action in ("caption", "hashtags", "profilepic")
        quality = "best"
        if action == "hd":   quality = "1080"
        if action == "4k":   quality = "4k"
        threading.Thread(
            target=do_download,
            args=(cid, uid, url, platform, quality, audio_only, thumbnail_only, caption_only),
            daemon=True
        ).start()
        return

    # ── Copy Tools ──
    if data.startswith("copy_"):
        copy_type = data.replace("copy_", "")
        state = get_state(uid)
        info = state.get("data", {}).get("info", {})
        url  = state.get("data", {}).get("url", "")

        copy_map = {
            "url":         url,
            "caption":     info.get("description", "No caption available"),
            "title":       info.get("title", "No title"),
            "description": info.get("description", "No description"),
            "hashtags":    " ".join(f"#{t}" for t in info.get("tags", [])) or "No hashtags",
            "all":         f"Title: {info.get('title','')}\nURL: {url}\nCaption: {info.get('description','')}",
            "videolink":   url,
            "imagelink":   info.get("thumbnail", url),
            "audiolink":   url,
            "thumbnail":   info.get("thumbnail", "No thumbnail URL"),
            "metadata":    json.dumps({
                "title": info.get("title", ""),
                "uploader": info.get("uploader", ""),
                "url": url,
            }, indent=2, ensure_ascii=False),
            "fileinfo":    f"Platform: {state.get('data',{}).get('platform','')}\nURL: {url}",
        }
        text = copy_map.get(copy_type, "Nothing to copy")
        if not text or text.strip() in ("No caption available", "No title", "No hashtags", ""):
            bot.answer_callback_query(call.id, "⚠️ No data to copy yet. Download something first!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "📋 Copied!")
        bot.send_message(cid, f"📋 *Copied {copy_type.title()}:*\n\n`{text[:3000]}`")
        return

    # ── Favourites ──
    if data == "fav_add":
        bot.answer_callback_query(call.id, "⭐ Added to favourites!", show_alert=True)
        return

    # ── Admin callbacks ──
    if data.startswith("admin_") and is_admin(uid):
        _handle_admin_callback(call, data, cid, uid)
        return

    bot.answer_callback_query(call.id)


def _handle_admin_callback(call, data, cid, uid):
    if data == "admin_broadcast":
        bot.answer_callback_query(call.id)
        set_state(uid, "admin_broadcast")
        bot.send_message(cid, "📣 Send your broadcast message:")

    elif data == "admin_userstats":
        users = get_users()
        bot.answer_callback_query(call.id)
        lines = ["👥 *User List (last 10)*\n"]
        for u in list(users.values())[-10:]:
            lines.append(f"• `{u['id']}` @{u.get('username','N/A')} — {u.get('joined','')[:10]}")
        bot.send_message(cid, "\n".join(lines))

    elif data == "admin_analytics":
        bot.answer_callback_query(call.id)
        logs = get_logs()
        users = get_users()
        platform_counts: dict[str, int] = {}
        for log in logs:
            p = log.get("platform", "other")
            platform_counts[p] = platform_counts.get(p, 0) + 1
        top = sorted(platform_counts.items(), key=lambda x: x[1], reverse=True)
        top_str = "\n".join(f"  {p.title()}: {c}" for p, c in top)
        bot.send_message(cid, f"📊 *Analytics*\n\n👥 Users: {len(users)}\n📥 Downloads: {len(logs)}\n\n{top_str}")

    elif data == "admin_logs":
        bot.answer_callback_query(call.id)
        logs = get_logs()
        recent = logs[-10:]
        lines = ["📋 *Recent Logs (last 10)*\n"]
        for log in recent:
            icon = "✅" if log.get("status") == "success" else "❌"
            lines.append(f"{icon} `{log.get('user_id')}` | {log.get('platform','?')} | {log.get('time','')[:10]}")
        bot.send_message(cid, "\n".join(lines))

    elif data == "admin_copy_logs":
        bot.answer_callback_query(call.id)
        logs = get_logs()
        text = json.dumps(logs[-20:], ensure_ascii=False, indent=2)
        bot.send_message(cid, f"📋 *Last 20 Logs:*\n```\n{text[:3000]}\n```")

    elif data == "admin_edit_welcome":
        bot.answer_callback_query(call.id)
        set_state(uid, "admin_edit_welcome")
        s = get_settings()
        bot.send_message(cid, f"Current welcome:\n\n{s.get('welcome_message','')}\n\n✏️ Send new welcome message:")

    elif data == "admin_edit_help":
        bot.answer_callback_query(call.id)
        set_state(uid, "admin_edit_help")
        bot.send_message(cid, "✏️ Send new help message:")

    elif data == "admin_edit_about":
        bot.answer_callback_query(call.id)
        set_state(uid, "admin_edit_about")
        bot.send_message(cid, "✏️ Send new about message:")

    elif data == "admin_ban":
        bot.answer_callback_query(call.id)
        set_state(uid, "admin_ban")
        bot.send_message(cid, "🚫 Send user ID to ban:")

    elif data == "admin_unban":
        bot.answer_callback_query(call.id)
        set_state(uid, "admin_unban")
        bot.send_message(cid, "✅ Send user ID to unban:")

    elif data == "admin_maintenance":
        s = get_settings()
        s["maintenance_mode"] = not s.get("maintenance_mode", False)
        save_settings(s)
        state_str = "ON 🔧" if s["maintenance_mode"] else "OFF ✅"
        bot.answer_callback_query(call.id, f"Maintenance: {state_str}")
        bot.send_message(cid, f"🔧 Maintenance mode: {state_str}")

    elif data == "admin_promo":
        bot.answer_callback_query(call.id)
        set_state(uid, "admin_promo")
        bot.send_message(cid, "📢 Send the promotion message for the channel:")


# ─────────────────────────────────────────────
#  TEXT / URL MESSAGE HANDLER
# ─────────────────────────────────────────────
@bot.message_handler(content_types=["text"])
def handle_text(msg: types.Message):
    uid  = msg.from_user.id
    cid  = msg.chat.id
    text = msg.text.strip()

    if is_banned(uid):
        bot.reply_to(msg, "🚫 You are banned from using this bot.")
        return

    register_user(msg.from_user)

    settings = get_settings()
    if settings.get("maintenance_mode") and not is_admin(uid):
        bot.reply_to(msg, "🔧 Bot is under maintenance. Please try again later.")
        return

    state = get_state(uid)
    current_state = state.get("state", "idle")

    # ── Admin state machine ──
    if current_state == "admin_broadcast" and is_admin(uid):
        users = get_users()
        sent, failed = 0, 0
        for uid_str in users:
            try:
                bot.send_message(int(uid_str), f"📣 *Broadcast*\n\n{text}")
                sent += 1
                time.sleep(0.05)
            except Exception:
                failed += 1
        clear_state(uid)
        bot.reply_to(msg, f"✅ Sent: {sent} | ❌ Failed: {failed}")
        return

    if current_state == "admin_edit_welcome" and is_admin(uid):
        s = get_settings()
        s["welcome_message"] = text
        save_settings(s)
        clear_state(uid)
        bot.reply_to(msg, "✅ Welcome message updated!")
        return

    if current_state == "admin_edit_help" and is_admin(uid):
        s = get_settings()
        s["help_message"] = text
        save_settings(s)
        clear_state(uid)
        bot.reply_to(msg, "✅ Help message updated!")
        return

    if current_state == "admin_edit_about" and is_admin(uid):
        s = get_settings()
        s["about_message"] = text
        save_settings(s)
        clear_state(uid)
        bot.reply_to(msg, "✅ About message updated!")
        return

    if current_state == "admin_ban" and is_admin(uid):
        if text.isdigit():
            banned = get_banned()
            if int(text) not in banned:
                banned.append(int(text))
                save_banned(banned)
            clear_state(uid)
            bot.reply_to(msg, f"🚫 User `{text}` banned.")
        else:
            bot.reply_to(msg, "❌ Please send a valid user ID.")
        return

    if current_state == "admin_unban" and is_admin(uid):
        if text.isdigit():
            banned = get_banned()
            uid_int = int(text)
            if uid_int in banned:
                banned.remove(uid_int)
                save_banned(banned)
            clear_state(uid)
            bot.reply_to(msg, f"✅ User `{text}` unbanned.")
        return

    if current_state == "admin_promo" and is_admin(uid):
        if CHANNEL_ID:
            try:
                bot.send_message(CHANNEL_ID, text)
                bot.reply_to(msg, "✅ Promotion sent to channel!")
            except Exception as e:
                bot.reply_to(msg, f"❌ Failed: {e}")
        else:
            bot.reply_to(msg, "❌ CHENEL_ID not set.")
        clear_state(uid)
        return

    # ── Menu buttons ──
    if text == "📥 Download":
        bot.send_message(cid, "📥 *Choose Platform*", reply_markup=download_menu_ik())
        return

    if text == "🔗 Paste URL":
        set_state(uid, "waiting_url_auto")
        bot.send_message(cid, "🔗 *Paste your URL here* and I'll auto-detect the platform!")
        return

    if text == "📋 Copy Tools":
        bot.send_message(cid, "📋 *Copy Tools*\nDownload something first, then use these to copy info:",
                         reply_markup=copy_tools_ik())
        return

    if text == "🧠 AI Tools":
        bot.send_message(cid, "🧠 *AI Tools*\nPaste a URL to use AI features:", reply_markup=ai_tools_ik())
        return

    if text == "👤 My Account":
        cmd_account(msg)
        return

    if text == "⚙️ Settings":
        s = get_settings()
        quality = s.get("default_quality", "best")
        text_out = (
            "⚙️ *Settings*\n\n"
            f"🎞 Default Quality: `{quality}`\n"
            f"🔧 Maintenance: `{'ON' if s.get('maintenance_mode') else 'OFF'}`\n\n"
            "Admin commands:\n"
            "`/setquality best|1080|720|480|4k`\n"
            "`/setwelcome <msg>`\n"
            "`/sethelp <msg>`\n"
            "`/setabout <msg>`\n"
            "`/maintenance`"
        )
        bot.send_message(cid, text_out)
        return

    if text == "🆘 Help":
        cmd_help(msg)
        return

    # ── AI Tool states ──
    if current_state.startswith("ai_"):
        ai_action = current_state.replace("ai_", "")
        urls = extract_urls(text)
        url = urls[0] if urls else (text if is_valid_url(text) else None)
        if not url:
            bot.reply_to(msg, "⚠️ Please send a valid URL.")
            return
        _handle_ai_action(cid, uid, url, ai_action)
        clear_state(uid)
        return

    # ── AI callback set state ──

    # ── URL auto-detection ──
    urls = extract_urls(text)
    if not urls and is_valid_url(text):
        urls = [text]

    if urls:
        url      = urls[0]
        platform = detect_platform(url)

        # store URL in state
        existing = get_state(uid)
        existing_data = existing.get("data", {})
        existing_data["url"] = url
        existing_data["platform"] = platform
        set_state(uid, f"has_url_{platform}", existing_data)

        PLATFORM_ICONS = {
            "tiktok": "🎵", "instagram": "📸", "facebook": "📘",
            "youtube": "▶️", "pinterest": "📌", "twitter": "🐦",
            "reddit": "🤖", "other": "🌐",
        }
        icon = PLATFORM_ICONS.get(platform, "🌐")

        # if user was waiting for a URL for a specific platform action, auto-download
        if current_state.startswith("waiting_url_"):
            plat = current_state.replace("waiting_url_", "")
            if plat == "auto":
                plat = platform
            s = get_settings()
            quality = s.get("default_quality", "best")
            bot.send_message(
                cid,
                f"🔗 *URL Detected!*\n\n{icon} Platform: *{platform.title()}*\n\n"
                "Choose an action:",
                reply_markup=platform_actions_ik(platform)
            )
        else:
            # Auto-detect response
            ik = InlineKeyboardMarkup(row_width=3)
            ik.add(
                InlineKeyboardButton("⬇️ Download Now",    callback_data=f"act_video_{platform}"),
                InlineKeyboardButton("📋 Copy URL",        callback_data="copy_url"),
                InlineKeyboardButton("🎞 Quality",         callback_data=f"quality_{platform}"),
                InlineKeyboardButton("🎵 Audio",           callback_data=f"act_audio_{platform}"),
                InlineKeyboardButton("🖼 Thumbnail",       callback_data=f"act_thumbnail_{platform}"),
                InlineKeyboardButton("📝 Caption/Info",    callback_data=f"act_caption_{platform}"),
                InlineKeyboardButton("🔁 More Options",    callback_data=f"plat_{platform}"),
                InlineKeyboardButton("🧠 AI Analyze",      callback_data="ai_analyze"),
            )
            bot.send_message(
                cid,
                f"🔗 *URL Detected!*\n\n{icon} Platform: *{platform.title()}*\n`{url[:100]}`\n\n"
                "What would you like to do?",
                reply_markup=ik
            )
        return

    # ── Fallback ──
    bot.send_message(
        cid,
        "👋 Send me a URL to download or use the menu below!",
        reply_markup=main_menu_kb()
    )


# ── AI callback: set state then ask for URL
@bot.callback_query_handler(func=lambda c: c.data.startswith("ai_"))
def handle_ai_callback(call: types.CallbackQuery):
    uid  = call.from_user.id
    cid  = call.message.chat.id
    action = call.data.replace("ai_", "")
    state = get_state(uid)
    url = state.get("data", {}).get("url")
    if url:
        bot.answer_callback_query(call.id, "🧠 Processing...")
        _handle_ai_action(cid, uid, url, action)
    else:
        set_state(uid, f"ai_{action}")
        bot.answer_callback_query(call.id)
        bot.send_message(cid, "🔗 Send a URL for AI analysis:")


def _handle_ai_action(cid: int, uid: int, url: str, action: str):
    msg = bot.send_message(cid, "🧠 AI processing...")
    info = fetch_info(url)
    if not info:
        bot.edit_message_text("❌ Could not fetch media info.", cid, msg.message_id)
        return

    title   = info.get("title", "")
    desc    = info.get("description", "") or ""
    tags    = info.get("tags", []) or []
    uploader = info.get("uploader", "")

    if action == "summary":
        summary = (
            f"📝 *Video Summary*\n\n"
            f"🎬 *Title:* {title}\n"
            f"👤 *Creator:* {uploader}\n"
            f"📖 *Description:* {desc[:500] or 'N/A'}\n"
            f"🏷 *Tags:* {', '.join(tags[:10]) or 'N/A'}"
        )
        bot.edit_message_text(summary, cid, msg.message_id)

    elif action == "caption":
        caption = (
            f"✍️ *Generated Caption*\n\n"
            f"🔥 {title}\n\n"
            f"{desc[:300] if desc else 'Check this out! 🎯'}\n\n"
            f"{'  '.join('#'+t for t in tags[:5])}"
        )
        bot.edit_message_text(caption, cid, msg.message_id)

    elif action == "hashtag":
        ht = " ".join(f"#{t.replace(' ','')}" for t in tags[:20]) if tags else "#trending #viral #content"
        bot.edit_message_text(f"#️⃣ *Hashtags*\n\n{ht}", cid, msg.message_id)

    elif action == "analyze":
        duration = info.get("duration", 0)
        view_cnt = info.get("view_count", 0)
        like_cnt = info.get("like_count", 0)
        analysis = (
            f"🔍 *Content Analysis*\n\n"
            f"🎬 Title: {title[:80]}\n"
            f"👤 Creator: {uploader}\n"
            f"⏱ Duration: {duration}s\n"
            f"👁 Views: {view_cnt:,}\n"
            f"❤️ Likes: {like_cnt:,}\n"
            f"🏷 Tags: {len(tags)}\n"
            f"📊 Engagement: {'High 🔥' if like_cnt and view_cnt and like_cnt/view_cnt > 0.05 else 'Normal 📊'}"
        )
        bot.edit_message_text(analysis, cid, msg.message_id)

    elif action == "title":
        bot.edit_message_text(
            f"🏷 *Title Generator*\n\n📌 Original: {title}\n\n"
            f"💡 Variation 1: {title} | Trending Now\n"
            f"💡 Variation 2: 🔥 {title}\n"
            f"💡 Variation 3: {title} — Must Watch!",
            cid, msg.message_id
        )

    elif action in ("ocr", "imgdesc"):
        thumb_url = info.get("thumbnail", "")
        bot.edit_message_text(
            f"🖼 *Thumbnail Analysis*\n\n"
            f"📷 Thumbnail URL: `{thumb_url[:200]}`\n\n"
            f"ℹ️ Full OCR requires a vision model integration.",
            cid, msg.message_id
        )
    else:
        bot.edit_message_text(f"✅ Info fetched!\n\n{build_info_text(info, 'auto')}", cid, msg.message_id)


# ─────────────────────────────────────────────
#  FLASK HEALTH CHECK + WEBHOOK
# ─────────────────────────────────────────────
app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "bot": "Ultimate Downloader Bot", "time": datetime.now().isoformat()})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if flask_request.headers.get("content-type") == "application/json":
        json_str = flask_request.get_data().decode("UTF-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "", 200
    return "Bad request", 400


# ─────────────────────────────────────────────
#  MAIN ENTRY POINT
# ─────────────────────────────────────────────
def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def run_bot():
    logger.info("🤖 Bot starting in polling mode...")
    bot.remove_webhook()
    bot.infinity_polling(timeout=30, long_polling_timeout=20, logger_level=logging.WARNING)


if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is not set!")
        exit(1)

    logger.info(f"🚀 Starting Ultimate Downloader Bot")
    logger.info(f"👑 Admin IDs: {ADMIN_IDS}")
    logger.info(f"📢 Channel: {CHANNEL_ID}")
    logger.info(f"🌐 Flask port: {PORT}")

    # Run Flask in background thread (for render.com health checks)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"✅ Flask health server started on port {PORT}")

    # Run bot in main thread
    run_bot()
