"""
Telegram Support Bot - Natural Chat + Follow-Up System
=======================================================
Fühlt sich an wie normales Telegram-Chatten.
100% native Sprachnachrichten, Inbox-System, Ungelesen-Tracking.
+ Ultra Follow-Up System mit Reminders
"""

import asyncio
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Tuple
import html

from telegram import Update, Bot, Message
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode, ChatAction

# ============================================================
# KONFIGURATION (Environment Variables oder Defaults)
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8544263228:AAGh08e5WK6N7NEVUNfQyEhembOQSwCVYVY")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "-1003740182436"))
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "2089427192,6696982829").split(",")]

ARCHIVE_AFTER_DAYS = 14
DIGEST_INTERVAL_MINUTES = 30
TYPING_INDICATOR = True

# Follow-Up Einstellungen
FOLLOWUP_AFTER_HOURS = 24   # Nach 24h ohne Antwort → Follow-up fällig
FOLLOWUP_MORNING_HOUR = 9   # Täglicher Report um 9:00

WELCOME_MESSAGE = """Hey! 👋

Schreib mir einfach deine Frage – ich melde mich so schnell wie möglich.

Sprachnachrichten, Bilder, alles kein Problem."""

STATUS = {"unread": "🔴", "read": "⚪", "answered": "🟢", "closed": "⚫", "followup": "💛"}
PRIORITY = {"normal": "", "vip": "⭐", "urgent": "🚨"}

TEMPLATES = {
    "hi": "Hey! 👋 Wie kann ich dir helfen?",
    "danke": "Gerne! Bei Fragen melde dich einfach 😊",
    "moment": "Einen Moment, ich schau mir das an! 🔍",
    "screenshot": "Kannst du mir einen Screenshot schicken? 📸",
    "erledigt": "Super, freut mich! ✅ Bei Fragen melde dich.",
}

# ============================================================
# DATABASE
# ============================================================

DB_PATH = Path(__file__).parent / "support.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            topic_id INTEGER,
            status TEXT DEFAULT 'unread',
            priority TEXT DEFAULT 'normal',
            unread_count INTEGER DEFAULT 0,
            last_message_preview TEXT,
            last_message_type TEXT,
            last_message_at TIMESTAMP,
            last_reply_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_archived INTEGER DEFAULT 0,
            followup_enabled INTEGER DEFAULT 1,
            followup_stage INTEGER DEFAULT 0,
            followup_skipped_until TIMESTAMP,
            followup_done INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            direction TEXT,
            msg_type TEXT,
            content TEXT,
            file_id TEXT,
            duration INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS voice_templates (
            name TEXT PRIMARY KEY,
            file_id TEXT,
            duration INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Migration: Add followup columns if not exist
    try:
        c.execute("ALTER TABLE chats ADD COLUMN followup_enabled INTEGER DEFAULT 1")
    except: pass
    try:
        c.execute("ALTER TABLE chats ADD COLUMN followup_stage INTEGER DEFAULT 0")
    except: pass
    try:
        c.execute("ALTER TABLE chats ADD COLUMN followup_skipped_until TIMESTAMP")
    except: pass
    try:
        c.execute("ALTER TABLE chats ADD COLUMN followup_done INTEGER DEFAULT 0")
    except: pass
    
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)

# ============================================================
# CHAT MANAGER
# ============================================================

class Chat:
    @staticmethod
    def get(user_id: int) -> Optional[dict]:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM chats WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if row:
            cols = [d[0] for d in c.description]
            conn.close()
            return dict(zip(cols, row))
        conn.close()
        return None

    @staticmethod
    def get_by_topic(topic_id: int) -> Optional[dict]:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM chats WHERE topic_id = ? AND is_archived = 0", (topic_id,))
        row = c.fetchone()
        if row:
            cols = [d[0] for d in c.description]
            conn.close()
            return dict(zip(cols, row))
        conn.close()
        return None

    @staticmethod
    def create(user_id: int, username: str, first_name: str, last_name: str, topic_id: int):
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            INSERT INTO chats (user_id, username, first_name, last_name, topic_id, last_message_at, status, unread_count)
            VALUES (?, ?, ?, ?, ?, ?, 'unread', 1)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name,
                topic_id=excluded.topic_id, is_archived=0, status='unread', unread_count=1
        """, (user_id, username, first_name, last_name, topic_id, datetime.now()))
        conn.commit()
        conn.close()

    @staticmethod
    def new_message(user_id: int, preview: str, msg_type: str):
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            UPDATE chats SET status='unread', unread_count=unread_count+1,
            last_message_preview=?, last_message_type=?, last_message_at=?,
            followup_stage=0, followup_done=0, followup_skipped_until=NULL
            WHERE user_id=?
        """, (preview[:100], msg_type, datetime.now(), user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def mark_read(user_id: int):
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE chats SET status=CASE WHEN status='unread' THEN 'read' ELSE status END, unread_count=0 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def mark_unread(user_id: int):
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE chats SET status='unread', unread_count=CASE WHEN unread_count=0 THEN 1 ELSE unread_count END WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def mark_answered(user_id: int):
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE chats SET status='answered', unread_count=0, last_reply_at=? WHERE user_id=?", (datetime.now(), user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def set_priority(user_id: int, priority: str):
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE chats SET priority=? WHERE user_id=?", (priority, user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def archive(user_id: int):
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE chats SET is_archived=1, status='closed' WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def get_unread() -> List[dict]:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM chats WHERE is_archived=0 AND status='unread'
            ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'vip' THEN 2 ELSE 3 END, last_message_at DESC
        """)
        rows = c.fetchall()
        cols = [d[0] for d in c.description]
        conn.close()
        return [dict(zip(cols, r)) for r in rows]

    @staticmethod
    def get_all_active() -> List[dict]:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM chats WHERE is_archived=0
            ORDER BY CASE status WHEN 'unread' THEN 1 WHEN 'read' THEN 2 ELSE 3 END, last_message_at DESC
        """)
        rows = c.fetchall()
        cols = [d[0] for d in c.description]
        conn.close()
        return [dict(zip(cols, r)) for r in rows]

    # ==================== FOLLOW-UP METHODS ====================
    
    @staticmethod
    def reset_followup(user_id: int):
        """Reset follow-up when customer replies"""
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            UPDATE chats SET followup_stage=0, followup_done=0, followup_skipped_until=NULL 
            WHERE user_id=?
        """, (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def mark_followup_done(user_id: int):
        """Mark follow-up as done (no more reminders)"""
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE chats SET followup_done=1 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def skip_followup(user_id: int, days: int = 3):
        """Skip follow-up for X days"""
        conn = get_db()
        c = conn.cursor()
        skip_until = datetime.now() + timedelta(days=days)
        c.execute("UPDATE chats SET followup_skipped_until=? WHERE user_id=?", (skip_until, user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def advance_followup_stage(user_id: int):
        """Move to next follow-up stage"""
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE chats SET followup_stage=followup_stage+1 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def get_followups_due() -> List[dict]:
        """Get all chats needing follow-up (max 1 per customer)"""
        conn = get_db()
        c = conn.cursor()
        now = datetime.now()
        cutoff = now - timedelta(hours=FOLLOWUP_AFTER_HOURS)
        
        # Get answered chats where:
        # - Not archived
        # - Status is 'answered' (we replied, waiting for customer)
        # - Follow-up not done yet
        # - Last reply older than 24h
        # - Not skipped
        c.execute("""
            SELECT * FROM chats 
            WHERE is_archived=0 
            AND status='answered' 
            AND followup_done=0
            AND last_reply_at < ?
            AND (followup_skipped_until IS NULL OR followup_skipped_until < ?)
            ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'vip' THEN 2 ELSE 3 END, last_reply_at ASC
        """, (cutoff, now))
        rows = c.fetchall()
        cols = [d[0] for d in c.description]
        conn.close()
        
        return [dict(zip(cols, r)) for r in rows]

# ============================================================
# HELPERS
# ============================================================

def get_name(chat: dict) -> str:
    parts = []
    if chat.get('first_name'): parts.append(chat['first_name'])
    if chat.get('last_name'): parts.append(chat['last_name'])
    if parts: return " ".join(parts)
    if chat.get('username'): return f"@{chat['username']}"
    return f"User {chat['user_id']}"

def get_topic_name(chat: dict) -> str:
    name = get_name(chat)
    s = STATUS.get(chat['status'], "")
    p = PRIORITY.get(chat['priority'], "")
    parts = [x for x in [p, s, name] if x]
    if chat.get('unread_count', 0) > 0:
        parts.append(f"({chat['unread_count']})")
    return " ".join(parts)[:128]

def time_ago(dt) -> str:
    if not dt: return ""
    delta = datetime.now() - dt
    if delta.days > 0: return f"vor {delta.days}d"
    if delta.seconds >= 3600: return f"vor {delta.seconds // 3600}h"
    if delta.seconds >= 60: return f"vor {delta.seconds // 60}min"
    return "gerade"

def msg_icon(t: str) -> str:
    return {"voice": "🎤", "video_note": "⏺", "photo": "📷", "video": "🎬", "document": "📎", "sticker": "😀"}.get(t, "")

def extract_info(msg: Message) -> Tuple[str, str, str, int]:
    if msg.text: return ("text", msg.text[:100], "", 0)
    if msg.voice: return ("voice", f"Sprachnachricht ({msg.voice.duration}s)", msg.voice.file_id, msg.voice.duration)
    if msg.video_note: return ("video_note", "Videonachricht", msg.video_note.file_id, msg.video_note.duration)
    if msg.photo: return ("photo", msg.caption or "Foto", msg.photo[-1].file_id, 0)
    if msg.video: return ("video", msg.caption or "Video", msg.video.file_id, msg.video.duration or 0)
    if msg.document: return ("document", msg.document.file_name or "Dokument", msg.document.file_id, 0)
    if msg.audio: return ("audio", msg.audio.title or "Audio", msg.audio.file_id, msg.audio.duration or 0)
    if msg.sticker: return ("sticker", msg.sticker.emoji or "Sticker", msg.sticker.file_id, 0)
    if msg.animation: return ("animation", "GIF", msg.animation.file_id, 0)
    if msg.location: return ("location", "Standort", "", 0)
    if msg.contact: return ("contact", msg.contact.first_name, "", 0)
    return ("unknown", "", "", 0)

def log_msg(user_id: int, direction: str, msg_type: str, content: str = "", file_id: str = "", duration: int = 0):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO messages (user_id, direction, msg_type, content, file_id, duration) VALUES (?,?,?,?,?,?)",
              (user_id, direction, msg_type, content, file_id, duration))
    conn.commit()
    conn.close()

# ============================================================
# TOPIC MANAGEMENT
# ============================================================

# Track last topic name to avoid unnecessary renames
TOPIC_NAME_CACHE = {}

async def update_topic(bot: Bot, chat: dict):
    """Update topic name with status - only if changed"""
    try:
        name = get_name(chat)
        s = STATUS.get(chat['status'], "")
        p = PRIORITY.get(chat['priority'], "")
        parts = [x for x in [p, s, name] if x]
        topic_name = " ".join(parts)[:128]
        
        # Only rename if actually changed
        cache_key = chat['topic_id']
        cached = TOPIC_NAME_CACHE.get(cache_key)
        
        print(f"[DEBUG] update_topic: status={chat['status']}, cached={cached}, new={topic_name}")
        
        if cached == topic_name:
            print(f"[DEBUG] Skipping - name unchanged")
            return
        
        await bot.edit_forum_topic(chat_id=SUPPORT_GROUP_ID, message_thread_id=chat['topic_id'], name=topic_name)
        TOPIC_NAME_CACHE[cache_key] = topic_name
        print(f"[DEBUG] Topic renamed to: {topic_name}")
    except Exception as e:
        print(f"[DEBUG] update_topic error: {e}")
        pass

async def repair_topic_if_needed(bot: Bot, user_id: int, user) -> dict:
    """Check if topic exists, recreate if not"""
    chat = Chat.get(user_id)
    if not chat:
        return None
    
    try:
        # Try to send typing action to check if we can access the topic
        # This doesn't change anything visible
        await bot.get_chat(chat_id=SUPPORT_GROUP_ID)
        # If topic_id is valid, this should work
        return chat
    except:
        pass
    
    # If we get here, try creating new topic
    try:
        name = get_name({'first_name': user.first_name, 'last_name': user.last_name, 'username': user.username})
        topic_name = f"🔴 {name}"[:128]
        topic = await bot.create_forum_topic(chat_id=SUPPORT_GROUP_ID, name=topic_name)
        
        # Update database with new topic_id
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE chats SET topic_id=? WHERE user_id=?", (topic.message_thread_id, user_id))
        conn.commit()
        conn.close()
        
        TOPIC_NAME_CACHE[topic.message_thread_id] = topic_name
        return Chat.get(user_id)
    except:
        return chat  # Return existing chat, let it fail naturally

async def delete_service_messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete 'topic renamed' service messages"""
    msg = update.message
    if not msg: return
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    # Check if it's a service message about topic rename
    if msg.forum_topic_edited:
        try:
            await msg.delete()
        except:
            pass

async def create_topic(bot: Bot, user) -> int:
    name = get_name({'first_name': user.first_name, 'last_name': user.last_name, 'username': user.username})
    topic_name = f"🔴 {name}"[:128]
    topic = await bot.create_forum_topic(chat_id=SUPPORT_GROUP_ID, name=topic_name)
    Chat.create(user.id, user.username or "", user.first_name or "", user.last_name or "", topic.message_thread_id)
    TOPIC_NAME_CACHE[topic.message_thread_id] = topic_name
    return topic.message_thread_id

# ============================================================
# FORWARDING - 100% NATIVE
# ============================================================

async def to_topic(bot: Bot, msg: Message, topic_id: int, user_id: int) -> bool:
    """User → Topic (100% nativ, keine Formatierung)"""
    t, preview, fid, dur = extract_info(msg)
    
    try:
        if t == "text": await bot.send_message(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, text=msg.text)
        elif t == "voice": await bot.send_voice(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, voice=fid, duration=dur)
        elif t == "video_note": await bot.send_video_note(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, video_note=fid, duration=dur)
        elif t == "photo": await bot.send_photo(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, photo=fid, caption=msg.caption)
        elif t == "video": await bot.send_video(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, video=fid, caption=msg.caption)
        elif t == "document": await bot.send_document(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, document=fid, caption=msg.caption)
        elif t == "audio": await bot.send_audio(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, audio=fid, caption=msg.caption)
        elif t == "sticker": await bot.send_sticker(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, sticker=fid)
        elif t == "animation": await bot.send_animation(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, animation=fid, caption=msg.caption)
        elif t == "location": await bot.send_location(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, latitude=msg.location.latitude, longitude=msg.location.longitude)
        elif t == "contact": await bot.send_contact(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, phone_number=msg.contact.phone_number, first_name=msg.contact.first_name, last_name=msg.contact.last_name or "")
        
        log_msg(user_id, "in", t, preview, fid, dur)
        Chat.new_message(user_id, preview, t)
        return True
    except:
        return False

async def to_user(bot: Bot, msg: Message, user_id: int, topic_id: int) -> bool:
    """Topic → User (100% nativ)"""
    t, preview, fid, dur = extract_info(msg)
    
    try:
        if t == "text": await bot.send_message(chat_id=user_id, text=msg.text)
        elif t == "voice": await bot.send_voice(chat_id=user_id, voice=fid, duration=dur)
        elif t == "video_note": await bot.send_video_note(chat_id=user_id, video_note=fid, duration=dur)
        elif t == "photo": await bot.send_photo(chat_id=user_id, photo=fid, caption=msg.caption)
        elif t == "video": await bot.send_video(chat_id=user_id, video=fid, caption=msg.caption)
        elif t == "document": await bot.send_document(chat_id=user_id, document=fid, caption=msg.caption)
        elif t == "audio": await bot.send_audio(chat_id=user_id, audio=fid, caption=msg.caption)
        elif t == "sticker": await bot.send_sticker(chat_id=user_id, sticker=fid)
        elif t == "animation": await bot.send_animation(chat_id=user_id, animation=fid, caption=msg.caption)
        elif t == "location": await bot.send_location(chat_id=user_id, latitude=msg.location.latitude, longitude=msg.location.longitude)
        elif t == "contact": await bot.send_contact(chat_id=user_id, phone_number=msg.contact.phone_number, first_name=msg.contact.first_name, last_name=msg.contact.last_name or "")
        
        log_msg(user_id, "out", t, preview, fid, dur)
        Chat.mark_answered(user_id)
        
        return True
    except Exception as e:
        await msg.reply_text(f"⚠️ {e}")
        return False

# ============================================================
# HANDLERS
# ============================================================

async def handle_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type != "private": return
    user, msg = update.effective_user, update.message
    if not user or not msg: return
    
    # Check if this is a voice message for /save
    if await handle_voice_save(update, ctx):
        return
    
    chat = Chat.get(user.id)
    if not chat or chat['is_archived']:
        topic_id = await create_topic(ctx.bot, user)
        if WELCOME_MESSAGE: await msg.reply_text(WELCOME_MESSAGE)
        chat = Chat.get(user.id)
    
    # Try to send to topic, create new if fails
    success = await to_topic(ctx.bot, msg, chat['topic_id'], user.id)
    if not success:
        # Topic doesn't exist anymore - create new one
        topic_id = await create_topic(ctx.bot, user)
        chat = Chat.get(user.id)
        await to_topic(ctx.bot, msg, chat['topic_id'], user.id)
    
    chat = Chat.get(user.id)
    await update_topic(ctx.bot, chat)

async def handle_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    # Ignore bot's own messages
    if msg.from_user and msg.from_user.is_bot:
        return
    
    topic_id = msg.message_thread_id
    if not topic_id: return
    
    # Nur Bot-eigene Befehle ignorieren - alle anderen /commands werden weitergeleitet
    BOT_COMMANDS = ['inbox', 'all', 'unread', 'read', 'info', 'vip', 'urgent', 'close', 'note', 't', 'v', 'save', 'del', 'search', 'help', 'hilfe', 'followup', 'done', 'skip', 'start', 'bc', 'broadcast', 'confirm', 'cancel']
    if msg.text:
        first_word = msg.text.split()[0].lower() if msg.text.split() else ""
        if first_word.startswith('/') and first_word[1:].split('@')[0] in BOT_COMMANDS:
            return
    
    chat = Chat.get_by_topic(topic_id)
    if not chat: return
    
    if TYPING_INDICATOR:
        try:
            await ctx.bot.send_chat_action(chat_id=chat['user_id'], action=ChatAction.TYPING)
            await asyncio.sleep(0.3)
        except: pass
    
    if await to_user(ctx.bot, msg, chat['user_id'], topic_id):
        chat = Chat.get(chat['user_id'])
        await update_topic(ctx.bot, chat)

# ============================================================
# COMMANDS
# ============================================================

async def cmd_inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    unread = Chat.get_unread()
    
    lines = ["━━━━━━━━━━━━━━━━━━━━", "📥 <b>INBOX</b>", "━━━━━━━━━━━━━━━━━━━━\n"]
    
    if unread:
        lines.append(f"🔴 <b>UNGELESEN ({len(unread)})</b>\n")
        for i, c in enumerate(unread, 1):
            name = get_name(c)
            p = PRIORITY.get(c['priority'], "")
            icon = msg_icon(c.get('last_message_type', ''))
            preview = c.get('last_message_preview', '')
            if icon: preview = f"{icon} {preview}"
            cnt = f"({c['unread_count']})" if c['unread_count'] > 1 else ""
            
            lines.append(f"<b>{i}. {p}{html.escape(name)}</b> {cnt}")
            lines.append(f"   {html.escape(preview[:40])}")
            lines.append(f"   <i>{time_ago(c['last_message_at'])}</i>\n")
    else:
        lines.append("✅ Keine ungelesenen\n")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("/unread • /read • /all")
    
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    chats = Chat.get_all_active()
    if not chats:
        await update.message.reply_text("Keine aktiven Chats")
        return
    
    lines = ["📋 <b>ALLE CHATS</b>\n"]
    for c in chats[:25]:
        s = STATUS.get(c['status'], "")
        p = PRIORITY.get(c['priority'], "")
        lines.append(f"{p}{s} {html.escape(get_name(c))} – <i>{time_ago(c['last_message_at'])}</i>")
    
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def cmd_unread(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    topic_id = update.message.message_thread_id
    if topic_id:
        chat = Chat.get_by_topic(topic_id)
        if chat:
            Chat.mark_unread(chat['user_id'])
            await update_topic(ctx.bot, Chat.get(chat['user_id']))
            await update.message.reply_text("🔴 Ungelesen")
            return
    
    if ctx.args:
        search = " ".join(ctx.args).lower()
        for c in Chat.get_all_active():
            if search in get_name(c).lower():
                Chat.mark_unread(c['user_id'])
                await update_topic(ctx.bot, Chat.get(c['user_id']))
                await update.message.reply_text(f"🔴 {get_name(c)} → ungelesen")
                return
        await update.message.reply_text("Nicht gefunden")
    else:
        await update.message.reply_text("Im Topic oder: /unread <name>")

async def cmd_read(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    topic_id = update.message.message_thread_id
    if topic_id:
        chat = Chat.get_by_topic(topic_id)
        if chat:
            Chat.mark_read(chat['user_id'])
            await update_topic(ctx.bot, Chat.get(chat['user_id']))
            await update.message.reply_text("⚪ Gelesen")
            return
    
    if ctx.args:
        search = " ".join(ctx.args).lower()
        for c in Chat.get_all_active():
            if search in get_name(c).lower():
                Chat.mark_read(c['user_id'])
                await update_topic(ctx.bot, Chat.get(c['user_id']))
                await update.message.reply_text(f"⚪ {get_name(c)} → gelesen")
                return
        await update.message.reply_text("Nicht gefunden")
    else:
        await update.message.reply_text("Im Topic oder: /read <name>")

async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    if not topic_id: return await update.message.reply_text("Im Topic nutzen")
    
    chat = Chat.get_by_topic(topic_id)
    if not chat: return
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(CASE WHEN direction='in' THEN 1 ELSE 0 END), SUM(CASE WHEN direction='out' THEN 1 ELSE 0 END) FROM messages WHERE user_id=?", (chat['user_id'],))
    stats = c.fetchone()
    conn.close()
    
    await update.message.reply_text(f"""<b>{html.escape(get_name(chat))}</b>

🆔 <code>{chat['user_id']}</code>
📧 @{html.escape(chat['username'] or '—')}
💬 {stats[0] or 0} ({stats[1] or 0} ↙️ {stats[2] or 0} ↗️)
📅 {chat['created_at'].strftime('%d.%m.%Y') if chat['created_at'] else '—'}""", parse_mode=ParseMode.HTML)

async def cmd_vip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    if not topic_id: return
    chat = Chat.get_by_topic(topic_id)
    if not chat: return
    
    new = "normal" if chat['priority'] == "vip" else "vip"
    Chat.set_priority(chat['user_id'], new)
    await update_topic(ctx.bot, Chat.get(chat['user_id']))
    await update.message.reply_text("⭐ VIP" if new == "vip" else "VIP aus")

async def cmd_urgent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    if not topic_id: return
    chat = Chat.get_by_topic(topic_id)
    if not chat: return
    
    new = "normal" if chat['priority'] == "urgent" else "urgent"
    Chat.set_priority(chat['user_id'], new)
    await update_topic(ctx.bot, Chat.get(chat['user_id']))
    await update.message.reply_text("🚨 Urgent" if new == "urgent" else "Urgent aus")

async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    if not topic_id: return
    chat = Chat.get_by_topic(topic_id)
    if not chat: return
    
    Chat.archive(chat['user_id'])
    try: await ctx.bot.close_forum_topic(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id)
    except: pass
    await update.message.reply_text("⚫ Archiviert")

async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    topic_id = update.message.message_thread_id
    if not topic_id: return
    chat = Chat.get_by_topic(topic_id)
    if not chat: return
    
    note = " ".join(ctx.args) if ctx.args else ""
    conn = get_db()
    c = conn.cursor()
    
    if note:
        c.execute("INSERT INTO notes (user_id, note) VALUES (?,?)", (chat['user_id'], note))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"📝 {note}")
    else:
        c.execute("SELECT note, created_at FROM notes WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (chat['user_id'],))
        notes = c.fetchall()
        conn.close()
        if notes:
            lines = ["📝 <b>Notizen</b>\n"]
            for n, d in notes:
                lines.append(f"• {html.escape(n)} <i>({d.strftime('%d.%m.') if d else ''})</i>")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("/note <text>")

async def cmd_t(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    if not ctx.args:
        lines = ["📋 <b>Templates</b>\n"]
        for k, v in TEMPLATES.items():
            lines.append(f"/t {k} → {html.escape(v[:30])}...")
        return await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    
    topic_id = update.message.message_thread_id
    if not topic_id: return await update.message.reply_text("Im Topic")
    chat = Chat.get_by_topic(topic_id)
    if not chat: return
    
    tmpl = TEMPLATES.get(ctx.args[0].lower())
    if not tmpl: return await update.message.reply_text("Nicht gefunden")
    
    try:
        await ctx.bot.send_message(chat_id=chat['user_id'], text=tmpl)
        log_msg(chat['user_id'], "out", "text", tmpl)
        Chat.mark_answered(chat['user_id'])
        await update_topic(ctx.bot, Chat.get(chat['user_id']))
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")

# Speicher für pending /save Befehle
PENDING_SAVE = {}

async def cmd_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Save next voice message as template: /save name"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return
    
    if not ctx.args:
        # Liste alle gespeicherten Voice-Templates
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT name, duration FROM voice_templates ORDER BY name")
        templates = c.fetchall()
        conn.close()
        
        if templates:
            lines = ["🎤 <b>Gespeicherte Sprachnachrichten</b>\n"]
            for name, dur in templates:
                lines.append(f"• /v {name} ({dur}s)")
            lines.append("\n<i>/save name → speichert nächste Sprachnachricht</i>")
            lines.append("<i>/del name → löscht Sprachnachricht</i>")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("Noch keine Sprachnachrichten gespeichert.\n\n/save name → dann Sprachnachricht senden")
        return
    
    name = ctx.args[0].lower()
    PENDING_SAVE[user_id] = name
    await update.message.reply_text(f"🎤 Sende jetzt die Sprachnachricht für <b>{name}</b>", parse_mode=ParseMode.HTML)

async def handle_voice_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle voice message after /save command"""
    user_id = update.effective_user.id
    msg = update.message
    
    if user_id not in PENDING_SAVE:
        return False
    
    if not msg.voice and not msg.audio:
        return False
    
    name = PENDING_SAVE.pop(user_id)
    voice = msg.voice or msg.audio
    file_id = voice.file_id
    duration = voice.duration or 0
    
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO voice_templates (name, file_id, duration) VALUES (?, ?, ?)", 
              (name, file_id, duration))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ Sprachnachricht <b>{name}</b> gespeichert!\n\nNutze /v {name} im Topic", parse_mode=ParseMode.HTML)
    return True

async def cmd_v(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send saved voice template: /v name"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    if not ctx.args:
        # Liste alle Voice-Templates
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT name, duration FROM voice_templates ORDER BY name")
        templates = c.fetchall()
        conn.close()
        
        if templates:
            lines = ["🎤 <b>Sprachnachrichten</b>\n"]
            for name, dur in templates:
                lines.append(f"• /v {name} ({dur}s)")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("Keine Sprachnachrichten.\n/save name → speichern")
        return
    
    topic_id = update.message.message_thread_id
    if not topic_id: return await update.message.reply_text("Im Topic nutzen")
    
    chat = Chat.get_by_topic(topic_id)
    if not chat: return
    
    name = ctx.args[0].lower()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT file_id, duration FROM voice_templates WHERE name=?", (name,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return await update.message.reply_text(f"❌ '{name}' nicht gefunden\n/v für Liste")
    
    file_id, duration = row
    
    try:
        await ctx.bot.send_voice(chat_id=chat['user_id'], voice=file_id)
        log_msg(chat['user_id'], "out", "voice", f"[Voice: {name}]", file_id, duration)
        Chat.mark_answered(chat['user_id'])
        await update.message.reply_text(f"🎤 ✓", parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")

async def cmd_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete voice template: /del name"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return
    
    if not ctx.args:
        return await update.message.reply_text("/del name → löscht Sprachnachricht")
    
    name = ctx.args[0].lower()
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM voice_templates WHERE name=?", (name,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    if deleted:
        await update.message.reply_text(f"🗑 <b>{name}</b> gelöscht", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"❌ '{name}' nicht gefunden")

async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    q = " ".join(ctx.args) if ctx.args else ""
    if not q: return await update.message.reply_text("/search <text>")
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT m.content, m.direction, c.first_name FROM messages m JOIN chats c ON m.user_id=c.user_id WHERE m.content LIKE ? ORDER BY m.created_at DESC LIMIT 10", (f"%{q}%",))
    results = c.fetchall()
    conn.close()
    
    if not results: return await update.message.reply_text("Nichts gefunden")
    
    lines = [f"🔍 <b>'{html.escape(q)}'</b>\n"]
    for content, direction, name in results:
        arrow = "↗️" if direction == "out" else "↙️"
        lines.append(f"{arrow} <b>{html.escape(name or '?')}</b>: {html.escape(content[:40])}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# ============================================================
# FOLLOW-UP COMMANDS
# ============================================================

async def cmd_followup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show all pending follow-ups"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    followups = Chat.get_followups_due()
    
    if not followups:
        await update.message.reply_text("✅ Keine Follow-ups fällig!")
        return
    
    lines = ["━━━━━━━━━━━━━━━━━━━━", f"📋 <b>FOLLOW-UPS ({len(followups)})</b>", "━━━━━━━━━━━━━━━━━━━━\n"]
    
    for c in followups[:15]:
        name = get_name(c)
        time = time_ago(c['last_reply_at'])
        p = PRIORITY.get(c['priority'], "")
        lines.append(f"{p}💛 <b>{html.escape(name)}</b>")
        lines.append(f"   Letzte Antwort: {time}\n")
    
    if len(followups) > 15:
        lines.append(f"<i>... +{len(followups)-15} weitere</i>\n")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>/done – Erledigt (nie wieder Reminder)</i>")
    lines.append("<i>/skip – Überspring für 3 Tage</i>")
    
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Mark follow-up as done"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    topic_id = update.message.message_thread_id
    if topic_id:
        chat = Chat.get_by_topic(topic_id)
        if chat:
            Chat.mark_followup_done(chat['user_id'])
            await update.message.reply_text("✅ Follow-up erledigt – keine weiteren Reminder")
            return
    
    if ctx.args:
        search = " ".join(ctx.args).lower()
        for c in Chat.get_all_active():
            if search in get_name(c).lower():
                Chat.mark_followup_done(c['user_id'])
                await update.message.reply_text(f"✅ {get_name(c)} – Follow-up erledigt")
                return
        await update.message.reply_text("Nicht gefunden")
    else:
        await update.message.reply_text("Im Topic oder: /done <name>")

async def cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Skip follow-up for X days"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    
    days = 3  # Default
    if ctx.args and ctx.args[-1].isdigit():
        days = int(ctx.args[-1])
    
    topic_id = update.message.message_thread_id
    if topic_id:
        chat = Chat.get_by_topic(topic_id)
        if chat:
            Chat.skip_followup(chat['user_id'], days)
            await update.message.reply_text(f"⏭️ Follow-up übersprungen für {days} Tage")
            return
    
    if ctx.args:
        search = ctx.args[0].lower()
        for c in Chat.get_all_active():
            if search in get_name(c).lower():
                Chat.skip_followup(c['user_id'], days)
                await update.message.reply_text(f"⏭️ {get_name(c)} – Follow-up übersprungen für {days} Tage")
                return
        await update.message.reply_text("Nicht gefunden")
    else:
        await update.message.reply_text("Im Topic oder: /skip <name> [tage]")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""<b>📖 Befehle</b>

<b>Inbox</b>
/inbox – Ungelesene
/all – Alle Chats
/search – Suchen

<b>Follow-Up</b>
/followup – Alle anstehenden
/done – Erledigt (kein Reminder mehr)
/skip – Überspring für 3 Tage

<b>Broadcast</b>
/bc followup [text] – An alle Follow-ups
/bc all [text] – An alle aktiven
/bc vip [text] – An alle VIPs

<b>Im Topic</b>
/unread – Als ungelesen
/read – Als gelesen
/info – User-Info
/note – Notizen
/vip /urgent – Priorität
/close – Archivieren
/t – Templates""", parse_mode=ParseMode.HTML)

# Pending broadcasts waiting for confirmation
PENDING_BROADCAST = {}

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to multiple users"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return
    
    if not ctx.args:
        await update.message.reply_text("""<b>📢 Broadcast</b>

/bc followup [nachricht] – An alle mit fälligem Follow-up
/bc all [nachricht] – An alle aktiven Chats
/bc vip [nachricht] – An alle VIPs

<i>Beispiel:</i>
<code>/bc followup Hey, alles klar bei dir? 😊</code>""", parse_mode=ParseMode.HTML)
        return
    
    target = ctx.args[0].lower()
    message = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""
    
    # Get recipients based on target
    recipients = []
    
    if target == "followup":
        followups = Chat.get_followups_due()
        for stage in ['overdue', 'urgent', 'due']:
            recipients.extend(followups.get(stage, []))
        target_name = "Follow-ups"
    elif target == "all":
        recipients = Chat.get_all_active()
        target_name = "Alle aktiven"
    elif target == "vip":
        recipients = [c for c in Chat.get_all_active() if c['priority'] == 'vip']
        target_name = "VIPs"
    else:
        await update.message.reply_text("❌ Unbekanntes Ziel. Nutze: followup, all, vip")
        return
    
    if not recipients:
        await update.message.reply_text(f"❌ Keine Empfänger in '{target_name}'")
        return
    
    if not message:
        await update.message.reply_text(f"❌ Keine Nachricht angegeben\n\n/bc {target} [deine nachricht]")
        return
    
    # Store pending broadcast
    PENDING_BROADCAST[user_id] = {
        'recipients': recipients,
        'message': message,
        'target_name': target_name
    }
    
    # Show preview
    names = [get_name(r) for r in recipients[:10]]
    more = f"\n... und {len(recipients) - 10} weitere" if len(recipients) > 10 else ""
    
    preview = f"""<b>📢 Broadcast Vorschau</b>

<b>Ziel:</b> {target_name}
<b>Empfänger:</b> {len(recipients)}

{chr(10).join(f'• {n}' for n in names)}{more}

<b>Nachricht:</b>
{html.escape(message)}

━━━━━━━━━━━━━━━━━━━━
/confirm – Jetzt senden
/cancel – Abbrechen"""
    
    await update.message.reply_text(preview, parse_mode=ParseMode.HTML)

async def cmd_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Confirm and send broadcast"""
    if update.effective_chat.id != SUPPORT_GROUP_ID: return
    user_id = update.effective_user.id
    
    if user_id not in PENDING_BROADCAST:
        await update.message.reply_text("❌ Kein Broadcast ausstehend")
        return
    
    broadcast = PENDING_BROADCAST.pop(user_id)
    recipients = broadcast['recipients']
    message = broadcast['message']
    
    sent = 0
    failed = 0
    
    status_msg = await update.message.reply_text(f"📤 Sende... 0/{len(recipients)}")
    
    for i, recipient in enumerate(recipients):
        try:
            await ctx.bot.send_message(chat_id=recipient['user_id'], text=message)
            Chat.mark_answered(recipient['user_id'])
            log_msg(recipient['user_id'], "out", "text", f"[Broadcast] {message[:50]}")
            sent += 1
        except Exception as e:
            failed += 1
        
        # Update status every 5 messages
        if (i + 1) % 5 == 0:
            try:
                await status_msg.edit_text(f"📤 Sende... {i + 1}/{len(recipients)}")
            except:
                pass
    
    await status_msg.edit_text(f"""✅ <b>Broadcast gesendet!</b>

📤 Gesendet: {sent}
❌ Fehlgeschlagen: {failed}""", parse_mode=ParseMode.HTML)

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cancel pending broadcast"""
    user_id = update.effective_user.id
    
    if user_id in PENDING_BROADCAST:
        del PENDING_BROADCAST[user_id]
        await update.message.reply_text("❌ Broadcast abgebrochen")
    else:
        await update.message.reply_text("Nichts zum Abbrechen")

# ============================================================
# JOBS
# ============================================================

async def job_digest(ctx: ContextTypes.DEFAULT_TYPE):
    unread = Chat.get_unread()
    old = [c for c in unread if c['last_message_at'] and (datetime.now() - c['last_message_at']).seconds > 1800]
    if not old: return
    
    lines = [f"📬 <b>{len(old)} warten!</b>\n"]
    for c in old[:5]:
        lines.append(f"• {html.escape(get_name(c))} – {time_ago(c['last_message_at'])}")
    lines.append("\n/inbox")
    
    await ctx.bot.send_message(chat_id=SUPPORT_GROUP_ID, text="\n".join(lines), parse_mode=ParseMode.HTML)

async def job_followup_morning(ctx: ContextTypes.DEFAULT_TYPE):
    """Daily morning follow-up report"""
    followups = Chat.get_followups_due()
    
    if not followups:
        return  # No follow-ups needed
    
    lines = ["━━━━━━━━━━━━━━━━━━━━", "☀️ <b>GUTEN MORGEN!</b>", "━━━━━━━━━━━━━━━━━━━━\n"]
    lines.append(f"📋 <b>{len(followups)} Follow-ups fällig</b>\n")
    
    for c in followups[:10]:
        name = get_name(c)
        time = time_ago(c['last_reply_at'])
        p = PRIORITY.get(c['priority'], "")
        lines.append(f"{p}💛 {html.escape(name)} – {time}")
    
    if len(followups) > 10:
        lines.append(f"\n... +{len(followups)-10} weitere")
    
    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("/followup für Details")
    
    await ctx.bot.send_message(chat_id=SUPPORT_GROUP_ID, text="\n".join(lines), parse_mode=ParseMode.HTML)

async def job_archive(ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    cutoff = datetime.now() - timedelta(days=ARCHIVE_AFTER_DAYS)
    c.execute("SELECT user_id, topic_id FROM chats WHERE is_archived=0 AND last_message_at<?", (cutoff,))
    
    for user_id, topic_id in c.fetchall():
        try: await ctx.bot.close_forum_topic(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id)
        except: pass
        c.execute("UPDATE chats SET is_archived=1, status='closed' WHERE user_id=?", (user_id,))
    
    conn.commit()
    conn.close()

# ============================================================
# MAIN
# ============================================================

def main():
    logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Delete "topic renamed" service messages
    app.add_handler(MessageHandler(filters.Chat(SUPPORT_GROUP_ID) & filters.StatusUpdate.FORUM_TOPIC_EDITED, delete_service_messages), group=0)
    
    # Private messages - also catch voice for /save
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.VOICE, handle_user))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_user))
    # Allow ALL messages in support group (including /commands for Quick Replies)
    app.add_handler(MessageHandler(filters.Chat(SUPPORT_GROUP_ID), handle_admin), group=1)
    
    for cmd, fn in [("inbox", cmd_inbox), ("all", cmd_all), ("unread", cmd_unread), ("read", cmd_read),
                    ("info", cmd_info), ("vip", cmd_vip), ("urgent", cmd_urgent), ("close", cmd_close),
                    ("note", cmd_note), ("t", cmd_t), ("v", cmd_v), ("save", cmd_save), ("del", cmd_del),
                    ("search", cmd_search), ("help", cmd_help), ("hilfe", cmd_help),
                    ("followup", cmd_followup), ("done", cmd_done), ("skip", cmd_skip),
                    ("bc", cmd_broadcast), ("broadcast", cmd_broadcast), ("confirm", cmd_confirm), ("cancel", cmd_cancel)]:
        app.add_handler(CommandHandler(cmd, fn))
    
    app.job_queue.run_repeating(job_digest, interval=DIGEST_INTERVAL_MINUTES * 60, first=300)
    app.job_queue.run_repeating(job_archive, interval=3600, first=60)
    
    # Morning follow-up report at 9:00
    from datetime import time as dt_time
    app.job_queue.run_daily(job_followup_morning, time=dt_time(hour=FOLLOWUP_MORNING_HOUR, minute=0))
    
    print("🚀 Support Bot + Follow-Up System gestartet")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
