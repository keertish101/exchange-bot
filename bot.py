import os
import re
import ast
import random
import asyncio
import operator as op
import sqlite3
import time
import io
import csv
import shutil
import logging
from pathlib import Path
from html import escape as html_escape
from collections import defaultdict
from datetime import timedelta
from urllib.parse import quote_plus

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import qrcode

load_dotenv()
log = logging.getLogger("exchange")

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
PREFIX = os.getenv("PREFIX", ".") or "."
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
BRAND = os.getenv("BOT_NAME", os.getenv("BOT_BRAND", "DOJI EXCHANGE")).strip() or "DOJI EXCHANGE"
WATERMARK = os.getenv("BOT_WATERMARK", f"{BRAND} • Professional Exchange System").strip()

# Optional application-owned custom emojis. Leave an ID blank to use no emoji.
def custom_emoji(env_name, fallback=None):
    raw = os.getenv(env_name, "").strip()
    if raw.isdigit():
        return discord.PartialEmoji(name=env_name.lower(), id=int(raw))
    return fallback

EMOJI_CLAIM = custom_emoji("EMOJI_CLAIM")
EMOJI_DONE = custom_emoji("EMOJI_DONE")
EMOJI_CLOSED = custom_emoji("EMOJI_CLOSED")
EMOJI_C2C = custom_emoji("EMOJI_C2C")
EMOJI_I2C_C2I = custom_emoji("EMOJI_I2C_C2I")
EMOJI_APPLY = custom_emoji("EMOJI_APPLY")
EMOJI_COMPLETED = custom_emoji("EMOJI_COMPLETED")
EMOJI_VERIFIED = custom_emoji("EMOJI_VERIFIED")
EMOJI_INFORMATION = custom_emoji("EMOJI_INFORMATION")
EMOJI_SETTINGS = custom_emoji("EMOJI_SETTINGS")
EMOJI_MONEY = custom_emoji("EMOJI_MONEY")
EMOJI_CLAIMED = custom_emoji("EMOJI_CLAIMED")
EMOJI_TICKET = custom_emoji("EMOJI_TICKET")

def emoji_or(fallback, custom):
    return str(custom) if custom else fallback

PANEL_E_I2C = emoji_or("💱", EMOJI_I2C_C2I)
PANEL_E_C2I = emoji_or("🪙", EMOJI_I2C_C2I)
PANEL_E_C2C = emoji_or("🔄", EMOJI_C2C)
PANEL_E_APPLY = emoji_or("📝", EMOJI_APPLY)
PANEL_E_RATE = emoji_or("📈", EMOJI_MONEY)
PANEL_E_INFO = emoji_or("🛡️", EMOJI_INFORMATION)

# Panel-only configuration. INR emoji is intentionally optional because you said you
# will choose/upload that one yourself. A direct image/GIF URL can be supplied later.
PANEL_IMAGE_URL = os.getenv("PANEL_IMAGE_URL", "").strip()
PANEL_FOOTER_TEXT = os.getenv("PANEL_FOOTER_TEXT", "").strip()
THREE_P_IMAGE = os.getenv("THREE_P_IMAGE", "assets/3p.png").strip()
START_TIME = time.time()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None, case_insensitive=True)

# ----------------------------- SQLite -----------------------------
DB = os.getenv("DB_PATH", "exchange_bot.db")
Path(DB).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups"))
BACKUP_KEEP = max(1, int(os.getenv("BACKUP_KEEP", "7") or 7))
db = sqlite3.connect(DB, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=NORMAL")
db.execute("PRAGMA busy_timeout=5000")

db.execute("""CREATE TABLE IF NOT EXISTS config(
 guild_id INTEGER PRIMARY KEY,
 ticket_category INTEGER,
 i2c_category INTEGER,
 c2i_category INTEGER,
 c2c_category INTEGER,
 apply_category INTEGER,
 archive_category INTEGER,
 log_channel INTEGER,
 showcase_channel INTEGER,
 done_channel INTEGER,
 total_channel INTEGER,
 staff_role INTEGER,
 i2c_rate REAL DEFAULT 105,
 i2c_below_rate REAL DEFAULT 100,
 i2c_above_rate REAL DEFAULT 101,
 c2i_rate REAL DEFAULT 100,
 c2c_rate REAL DEFAULT 0,
 c2c_fee_percent REAL DEFAULT 3,
 decline_penalty REAL DEFAULT 1,
 tos_text TEXT DEFAULT 'Please read the TOS before starting an exchange.',
 panel_channel INTEGER,
 demo_enabled INTEGER DEFAULT 0,
 demo_interval_minutes INTEGER DEFAULT 15,
 demo_min_amount REAL DEFAULT 10,
 demo_max_amount REAL DEFAULT 10000,
 demo_channel INTEGER,
 staff_limit_usdt REAL DEFAULT 40,
 unclaimed_timeout_minutes INTEGER DEFAULT 120,
 panel_message INTEGER
)""")
db.execute("""CREATE TABLE IF NOT EXISTS exchange_roles(
 guild_id INTEGER NOT NULL,
 role_id INTEGER NOT NULL,
 PRIMARY KEY(guild_id, role_id)
)""")

db.execute("""CREATE TABLE IF NOT EXISTS saved_upi(
guild_id INTEGER NOT NULL,
user_id INTEGER NOT NULL,
name TEXT NOT NULL,
upi_id TEXT NOT NULL,
created_at TEXT NOT NULL,
PRIMARY KEY(guild_id, user_id, name)
)""")

db.execute("""CREATE TABLE IF NOT EXISTS tickets(
 channel_id INTEGER PRIMARY KEY,
 guild_id INTEGER NOT NULL,
 opener_id INTEGER NOT NULL,
 ticket_type TEXT NOT NULL,
 amount REAL DEFAULT 0,
 rate REAL DEFAULT 0,
 claimed_by INTEGER,
 status TEXT DEFAULT 'open',
 created_at TEXT NOT NULL,
 p3_accepted INTEGER DEFAULT 0,
 p3_declined INTEGER DEFAULT 0,
 ticket_message_id INTEGER,
 p3_message_id INTEGER,
 notes TEXT DEFAULT ''
)""")
db.execute("""CREATE TABLE IF NOT EXISTS exchanges(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 guild_id INTEGER NOT NULL,
 ticket_channel_id INTEGER NOT NULL,
 ticket_type TEXT NOT NULL,
 amount REAL DEFAULT 0,
 rate REAL DEFAULT 0,
 status TEXT NOT NULL,
 created_at TEXT NOT NULL,
 opener_id INTEGER,
 claimed_by INTEGER,
 inr_value REAL DEFAULT 0
)""")
db.execute("""CREATE TABLE IF NOT EXISTS warnings(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL,
 moderator_id INTEGER NOT NULL,
 reason TEXT,
 created_at TEXT NOT NULL
)""")
db.execute("""CREATE TABLE IF NOT EXISTS blacklists(
 guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL,
 moderator_id INTEGER NOT NULL,
 reason TEXT DEFAULT '',
 created_at TEXT NOT NULL,
 PRIMARY KEY(guild_id,user_id)
)""")
db.execute("""CREATE TABLE IF NOT EXISTS feedback(
id INTEGER PRIMARY KEY AUTOINCREMENT,
guild_id INTEGER NOT NULL,
ticket_channel_id INTEGER NOT NULL UNIQUE,
client_id INTEGER NOT NULL,
exchanger_id INTEGER NOT NULL,
rating INTEGER NOT NULL,
comment TEXT DEFAULT '',
created_at TEXT NOT NULL
)""")

db.execute("""CREATE TABLE IF NOT EXISTS user_stats(
 guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL,
 completed_count INTEGER DEFAULT 0,
 completed_volume REAL DEFAULT 0,
 PRIMARY KEY(guild_id,user_id)
)""")
db.execute("""CREATE TABLE IF NOT EXISTS counters(
 guild_id INTEGER PRIMARY KEY,
 last_total REAL DEFAULT 0,
 last_count INTEGER DEFAULT 0
)""")

# Migrations from older builds.
MIGRATIONS = {
    "i2c_category": "INTEGER", "c2i_category": "INTEGER", "c2c_category": "INTEGER",
    "apply_category": "INTEGER", "archive_category": "INTEGER", "done_channel": "INTEGER",
    "total_channel": "INTEGER", "i2c_below_rate": "REAL DEFAULT 100", "i2c_above_rate": "REAL DEFAULT 101", "c2c_rate": "REAL DEFAULT 0", "c2c_fee_percent": "REAL DEFAULT 3", "panel_channel": "INTEGER",
    "panel_message": "INTEGER",
    "demo_enabled": "INTEGER DEFAULT 0", "demo_interval_minutes": "INTEGER DEFAULT 15",
    "demo_min_amount": "REAL DEFAULT 10", "demo_max_amount": "REAL DEFAULT 10000", "demo_channel": "INTEGER",
    "staff_limit_usdt": "REAL DEFAULT 40", "unclaimed_timeout_minutes": "INTEGER DEFAULT 120",
    "leaderboard_channel": "INTEGER", "leaderboard_message": "INTEGER", "first_prize": "TEXT DEFAULT '$40'", "second_prize": "TEXT DEFAULT '$25'",
    "third_prize": "TEXT DEFAULT '$15'", "fourth_prize": "TEXT DEFAULT '$10'", "fifth_prize": "TEXT DEFAULT '$10'",
    "sixth_prize": "TEXT DEFAULT '$10'", "seventh_prize": "TEXT DEFAULT '$10'", "eighth_prize": "TEXT DEFAULT '$10'",
    "ninth_prize": "TEXT DEFAULT '$10'", "tenth_prize": "TEXT DEFAULT '$10'", "security_enabled": "INTEGER DEFAULT 1",
    "min_account_age_days": "INTEGER DEFAULT 7", "min_server_age_minutes": "INTEGER DEFAULT 10",
    "require_security_check": "INTEGER DEFAULT 0", "require_3p": "INTEGER DEFAULT 0",
    "public_showcase_enabled": "INTEGER DEFAULT 1", "ticket_cooldown_minutes": "INTEGER DEFAULT 2"
}
for col, definition in MIGRATIONS.items():
    try:
        db.execute(f"ALTER TABLE config ADD COLUMN {col} {definition}")
    except sqlite3.OperationalError:
        pass
for col, definition in {"notes":"TEXT DEFAULT ''", "security_score":"INTEGER DEFAULT 0", "security_status":"TEXT DEFAULT 'not_checked'", "security_checked_at":"TEXT"}.items():
    try: db.execute(f"ALTER TABLE tickets ADD COLUMN {col} {definition}")
    except sqlite3.OperationalError: pass
for col, definition in {"input_amount":"REAL DEFAULT 0","input_currency":"TEXT DEFAULT 'USDT'","inr_value":"REAL DEFAULT 0"}.items():
    try: db.execute(f"ALTER TABLE tickets ADD COLUMN {col} {definition}")
    except sqlite3.OperationalError: pass
for col, definition in {"input_amount":"REAL DEFAULT 0","input_currency":"TEXT DEFAULT 'USDT'"}.items():
    try: db.execute(f"ALTER TABLE exchanges ADD COLUMN {col} {definition}")
    except sqlite3.OperationalError: pass
for col, definition in {
    "opener_id": "INTEGER", "claimed_by": "INTEGER", "inr_value": "REAL DEFAULT 0"
}.items():
    try:
        db.execute(f"ALTER TABLE exchanges ADD COLUMN {col} {definition}")
    except sqlite3.OperationalError:
        pass
for stmt in (
    "CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(guild_id,opener_id,status)",
    "CREATE INDEX IF NOT EXISTS idx_exchanges_guild ON exchanges(guild_id,status,id)",
    "CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings(guild_id,user_id,id)",
):
    db.execute(stmt)
db.commit()


def getcfg(gid):
    row = db.execute("SELECT * FROM config WHERE guild_id=?", (gid,)).fetchone()
    if row is None:
        db.execute("INSERT OR IGNORE INTO config(guild_id) VALUES(?)", (gid,))
        db.commit()
        row = db.execute("SELECT * FROM config WHERE guild_id=?", (gid,)).fetchone()
    return dict(row)


def setcfg(gid, field, value):
    if field not in {
        "ticket_category", "i2c_category", "c2i_category", "c2c_category", "apply_category",
        "archive_category", "log_channel", "showcase_channel", "done_channel", "total_channel",
        "staff_role", "i2c_rate", "i2c_below_rate", "i2c_above_rate", "c2i_rate", "c2i_below_rate", "c2i_above_rate", "c2c_rate", "c2c_fee_percent", "decline_penalty", "tos_text",
        "panel_channel", "panel_message", "demo_enabled", "demo_interval_minutes", "demo_min_amount", "demo_max_amount", "demo_channel", "staff_limit_usdt", "unclaimed_timeout_minutes",
        "leaderboard_channel", "leaderboard_message", "first_prize", "second_prize", "third_prize", "fourth_prize", "fifth_prize", "sixth_prize", "seventh_prize", "eighth_prize", "ninth_prize", "tenth_prize",
        "security_enabled", "min_account_age_days", "min_server_age_minutes", "require_security_check", "require_3p", "public_showcase_enabled", "ticket_cooldown_minutes"
    }:
        raise ValueError("invalid config field")
    getcfg(gid)
    db.execute(f"UPDATE config SET {field}=? WHERE guild_id=?", (value, gid))
    db.commit()


def now_iso():
    return discord.utils.utcnow().isoformat()


def fmt_rate(x):
    return f"₹{float(x or 0):,.2f}"


def embed(title, desc="", color=None):
    e = discord.Embed(title=title, description=desc, color=color or discord.Color.blurple(), timestamp=discord.utils.utcnow())
    e.set_footer(text=WATERMARK)
    return e


def owner_or_admin(member):
    return bool(member and (member.id == OWNER_ID or member.guild_permissions.administrator))


def exchange_staff(member):
    if not member or not getattr(member, "guild", None):
        return False
    if owner_or_admin(member):
        return True
    ids = {int(r["role_id"]) for r in db.execute(
        "SELECT role_id FROM exchange_roles WHERE guild_id=?", (member.guild.id,)
    ).fetchall()}
    # Backward compatibility: the old single staff role is treated as an exchange role.
    c = getcfg(member.guild.id)
    if c.get("staff_role"):
        ids.add(int(c["staff_role"]))
    return any(r.id in ids for r in member.roles)


def staff(member):
    return exchange_staff(member)


def ticket_manager(member, ticket_type):
    """Permission boundary: exchange roles manage only exchange tickets.
    Support/report/apply are restricted to the owner or Discord Administrators.
    """
    if owner_or_admin(member):
        return True
    return ticket_type in {"I2C", "C2I", "C2C"} and exchange_staff(member)


def configured_exchange_roles(guild):
    ids = {int(r["role_id"]) for r in db.execute(
        "SELECT role_id FROM exchange_roles WHERE guild_id=?", (guild.id,)
    ).fetchall()}
    c = getcfg(guild.id)
    if c.get("staff_role"):
        ids.add(int(c["staff_role"]))
    return [guild.get_role(rid) for rid in ids if guild.get_role(rid)]


def can_manage_current_ticket(ctx):
    row = db.execute("SELECT ticket_type FROM tickets WHERE channel_id=?", (ctx.channel.id,)).fetchone()
    return row is not None and ticket_manager(ctx.author, row["ticket_type"]), row


def bot_can_manage(guild):
    me = guild.me
    return bool(me and me.guild_permissions.manage_channels)


STAFF_COMMANDS = {"claim","x","done","close","reopen","add","remove","rename","ticketlock","ticketunlock","ticketinfo","trans","upi","3p","exprofile","blacklist","blacklistcheck"}
PUBLIC_COMMANDS = {"help","help2","ping","uptime","rate","rates","tos","terms","rules","calc","leaderboard","lb","prizes","securitycheck","p","profile"}

@bot.check
async def _global_access(ctx):
    # Admin/owner commands stay protected, while genuine staff commands work
    # for the configured exchanger role too. This fixes the common situation
    # where a staff member can click Claim but `.claim` was rejected.
    if not ctx.guild:
        return False
    return owner_or_admin(ctx.author) or (ctx.command and (ctx.command.name in STAFF_COMMANDS and staff(ctx.author) or ctx.command.name in PUBLIC_COMMANDS))


async def send_log(guild, title, desc, color=discord.Color.blurple()):
    cid = getcfg(guild.id).get("log_channel")
    if not cid:
        return
    ch = guild.get_channel(cid)
    if ch:
        try:
            await ch.send(embed=embed(title, desc, color))
        except discord.HTTPException:
            pass


async def send_done_record(guild, flow, amount, rate, transaction_id=None, completed_at=None, claimed_by=None, opener_id=None, inr_value=0, input_amount=None, input_currency="USDT"):
    cfg=getcfg(guild.id); cid=cfg.get("showcase_channel") if cfg.get("public_showcase_enabled", 1) else None
    if not cid: return
    ch=guild.get_channel(cid)
    if not ch: return
    when=completed_at or discord.utils.utcnow()
    direction={"I2C":"INR → USDT","C2I":"USDT → INR","C2C":"Crypto → Crypto","APPLY":"Custom Exchange"}.get(flow,flow)
    staff_member=guild.get_member(claimed_by) if claimed_by else None
    staff_name=staff_member.display_name if staff_member else "Unassigned"
    inr_value=float(inr_value or 0)
    input_amount=float(input_amount if input_amount is not None else amount)
    amount_line=(f"`₹{input_amount:,.2f} INR → {amount:,.4f} USDT`" if flow=="I2C" else
                 f"`{amount:,.4f} USDT → ₹{inr_value:,.2f}`" if flow=="C2I" else
                 f"`{amount:,.4f} USDT`" if flow=="C2C" else f"`{input_amount:,.2f} {input_currency}`")
    e=embed("✨ DEAL COMPLETED",
        "A verified exchange has been completed successfully.\n"
        "Thank you for choosing our exchange service.\n\n"
        f"🎫 **Transaction ID**\n`#{transaction_id}`\n\n"
        f"💱 **Exchanger**\n`{staff_name}`\n\n"
        f"🦋 **Client**\n🔒 `Private`\n\n"
        f"» **Amount**\n{amount_line}\n\n"
        f"💜 **Category**\n`{flow}`\n\n"
        f"💹 **Rate**\n`{fmt_rate(rate) if rate else 'Manual quote'}`\n\n"
        f"🕒 **Completed**\n{discord.utils.format_dt(when,'F')}\n\n"
        "🛡️ Client details are hidden from the public showcase.\n"
        "✨ Fast • Verified • Trusted Exchange",discord.Color.green())
    if bot.user:
        e.set_author(name=f"{BRAND} • Verified Deal Log", icon_url=bot.user.display_avatar.url)
    try: await ch.send(embed=e)
    except discord.HTTPException: pass


async def send_internal_done_record(guild, flow, amount, rate, transaction_id, opener_id, claimed_by):
    """Optional admin/staff completion record. It is not used in public showcase."""
    cfg = getcfg(guild.id)
    # Completion/audit records must stay private. Prefer the dedicated done channel;
    # if it is missing or incorrectly points to the public showcase, use the private log channel.
    cid = cfg.get("done_channel")
    if not cid or cid == cfg.get("showcase_channel"):
        cid = cfg.get("log_channel")
    if not cid:
        return
    ch = guild.get_channel(cid)
    if not ch:
        return
    e = embed(
        "🧾 PRIVATE COMPLETION LOG",
        f"**Transaction:** `#{transaction_id}`\n"
        f"**Exchange Type:** `{flow}`\n"
        f"**Amount:** `{float(amount or 0):,.2f} USDT`\n"
        f"**Rate:** `{fmt_rate(rate) if rate else 'Manual quote'}`\n"
        f"**Client ID:** `{opener_id}`\n"
        f"**Exchanger ID:** `{claimed_by or 'Unclaimed'}`\n"
        f"**Completed:** {discord.utils.format_dt(discord.utils.utcnow(), 'F')}",
        discord.Color.blurple()
    )
    try:
        await ch.send(embed=e)
    except discord.HTTPException:
        pass


def rounded_demo_amount(minimum=10, maximum=10000):
    minimum = max(1, int(minimum))
    maximum = max(minimum, int(maximum))
    # Round figures only: multiples of 10, including values such as 80/90/100.
    lo = ((minimum + 9) // 10) * 10
    hi = (maximum // 10) * 10
    if lo > hi:
        return float(minimum)
    return float(random.randint(lo // 10, hi // 10) * 10)


async def post_demo_transaction(guild):
    """Post a clearly marked sample transaction; never writes it to real exchange totals."""
    cfg = getcfg(guild.id)
    cid = cfg.get("demo_channel") or cfg.get("showcase_channel")
    if not cfg.get("demo_enabled") or not cid:
        return
    ch = guild.get_channel(cid)
    if not ch:
        return
    amount_inr = rounded_demo_amount(cfg.get("demo_min_amount") or 10, cfg.get("demo_max_amount") or 10000)
    flow = random.choice(("I2C", "C2I"))
    rate = float(cfg.get("i2c_rate") if flow == "I2C" else cfg.get("c2i_rate") or 0)
    usdt = amount_inr / rate if rate else 0
    direction = "INR → USDT" if flow == "I2C" else "USDT → INR"
    e = embed(
        "🧪 SAMPLE / DEMO TRANSACTION",
        f"**Type:** `{flow}` • `{direction}`\n"
        f"**Sample INR Amount:** `{amount_inr:,.0f} INR`\n"
        f"**Reference Rate:** `{fmt_rate(rate)}`\n"
        f"**Estimated USDT:** `{usdt:,.2f} USDT`\n"
        f"**Generated:** {discord.utils.format_dt(discord.utils.utcnow(), 'F')}\n\n"
        "⚠️ **DEMO ONLY — this is not a real transaction, is not counted in totals, "
        "and is not stored in the completed-transactions database.**",
        discord.Color.orange()
    )
    try:
        await ch.send(embed=e)
    except discord.HTTPException:
        pass


@tasks.loop(seconds=60)
async def demo_showcase_loop():
    now = time.time()
    for guild in list(bot.guilds):
        cfg = getcfg(guild.id)
        if not cfg.get("demo_enabled"):
            continue
        interval = max(15, min(int(cfg.get("demo_interval_minutes") or 15), 1440))
        last = getattr(demo_showcase_loop, "_last", {}).get(guild.id, 0)
        if now - last >= interval * 60:
            await post_demo_transaction(guild)
            if not hasattr(demo_showcase_loop, "_last"):
                demo_showcase_loop._last = {}
            demo_showcase_loop._last[guild.id] = now


@demo_showcase_loop.before_loop
async def before_demo_showcase_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def unclaimed_cleanup_loop():
    now=time.time()
    for guild in list(bot.guilds):
        cfg=getcfg(guild.id); timeout=max(15,int(cfg.get("unclaimed_timeout_minutes") or 120))
        rows=db.execute("SELECT channel_id,ticket_type,created_at FROM tickets WHERE guild_id=? AND status='open' AND claimed_by IS NULL",(guild.id,)).fetchall()
        for row in rows:
            try: age=(discord.utils.utcnow()-discord.utils.parse_time(row["created_at"])).total_seconds()/60
            except Exception: continue
            if age < timeout: continue
            ch=guild.get_channel(row["channel_id"])
            db.execute("UPDATE tickets SET status='closed' WHERE channel_id=?",(row["channel_id"],)); db.commit()
            if ch:
                try:
                    await ch.send(embed=embed("⏳ UNCLAIMED TICKET EXPIRED",f"No exchanger claimed this `{row['ticket_type']}` ticket within `{timeout}` minutes.\n\nThe ticket was **closed, not deleted**.",discord.Color.orange()))
                    await ch.edit(name=f"expired-{ch.name}"[:95])
                    await ch.set_permissions(guild.default_role,view_channel=False)
                except discord.HTTPException: pass

@unclaimed_cleanup_loop.before_loop
async def before_unclaimed_cleanup_loop():
    await bot.wait_until_ready()


async def update_total_counter(guild):
    cfg = getcfg(guild.id)
    cid = cfg.get("total_channel")
    # Count every completed exchange in INR equivalent.
    # I2C uses the original INR input; C2I uses the recorded INR payout;
    # C2C falls back to crypto amount multiplied by its recorded rate.
    total, count = db.execute(
        """SELECT COALESCE(SUM(
                 CASE
                   WHEN ticket_type='I2C' THEN COALESCE(input_amount,0)
                   WHEN ticket_type='C2I' THEN COALESCE(inr_value,0)
                   WHEN ticket_type='C2C' AND COALESCE(rate,0) > 0
                     THEN COALESCE(amount,0) * rate
                   ELSE COALESCE(inr_value,0)
                 END
               ),0), COUNT(*)
            FROM exchanges
            WHERE guild_id=? AND status='completed'""",
        (guild.id,)
    ).fetchone()
    total, count = float(total or 0), int(count or 0)
    db.execute("INSERT INTO counters(guild_id,last_total,last_count) VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET last_total=excluded.last_total,last_count=excluded.last_count", (guild.id,total,count))
    db.commit()
    if cid:
        ch = guild.get_channel(cid)
        if ch:
            desired = f"total-{total:,.2f}₹-inr"[:100]
            # Discord rate-limits channel PATCH requests. Do not PATCH when the
            # channel already has the correct name; this prevents repeated 429s
            # and keeps .x responsive.
            if ch.name != desired:
                try:
                    await ch.edit(name=desired, reason="Exchange total counter update")
                except discord.HTTPException as exc:
                    print(f"Total counter rename skipped: {getattr(exc, 'status', 'HTTP error')}")


# ----------------------------- Utilities -----------------------------
def safe_calc(expr):
    if len(expr) > 100:
        raise ValueError
    tree = ast.parse(expr, mode="eval")
    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
           ast.FloorDiv: op.floordiv, ast.Mod: op.mod, ast.Pow: op.pow}
    unary = {ast.UAdd: op.pos, ast.USub: op.neg}

    def ev(n):
        if isinstance(n, ast.Expression): return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
        if isinstance(n, ast.UnaryOp) and type(n.op) in unary: return unary[type(n.op)](ev(n.operand))
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            a, b = ev(n.left), ev(n.right)
            if b == 0 and isinstance(n.op, (ast.Div, ast.FloorDiv, ast.Mod)): raise ZeroDivisionError
            if isinstance(n.op, ast.Pow) and abs(b) > 10: raise ValueError
            return ops[type(n.op)](a, b)
        raise ValueError
    result = ev(tree)
    return int(result) if isinstance(result, float) and result.is_integer() else round(result, 10) if isinstance(result, float) else result


UPI_RE = re.compile(r"(?<![\w.-])([A-Za-z0-9][A-Za-z0-9._-]{1,255}@[A-Za-z0-9][A-Za-z0-9.-]{1,63})(?![\w.-])")


def extract_upi(text):
    m = UPI_RE.search(text or "")
    return m.group(1) if m else None


def make_upi_qr(upi_id, amount=None):
    data = f"upi://pay?pa={quote_plus(upi_id)}&cu=INR"
    if amount is not None:
        data += f"&am={float(amount):.2f}"
    qr = qrcode.QRCode(version=None, box_size=8, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ----------------------------- Security / anti-alt helpers -----------------------------
def security_score(member):
    """Discord does not expose IP/device identity, so this is a risk engine, not a claim of identity matching."""
    cfg = getcfg(member.guild.id)
    score = 0
    reasons = []
    now = discord.utils.utcnow()
    account_days = max(0, int((now - member.created_at).total_seconds() / 86400))
    joined_days = max(0, int((now - member.joined_at).total_seconds() / 86400)) if member.joined_at else 0
    min_account = max(0, int(cfg.get("min_account_age_days") or 0))
    min_join = max(0, int(cfg.get("min_server_age_minutes") or 0))
    if member.bot:
        score += 100; reasons.append("Account is a bot account.")
    if account_days < min_account:
        score += 50; reasons.append(f"Discord account age is only {account_days} day(s) (minimum {min_account}).")
    if member.joined_at and (now - member.joined_at).total_seconds() / 60 < min_join:
        score += 35; reasons.append(f"Member joined the server recently (minimum {min_join} minutes).")
    prior = db.execute("SELECT COUNT(*) FROM exchanges WHERE guild_id=? AND opener_id=? AND status='completed'", (member.guild.id, member.id)).fetchone()[0]
    if prior:
        score = max(0, score - 20); reasons.append(f"History: {prior} completed exchange(s) on this account.")
    status = "high_risk" if score >= 50 else ("review" if score >= 25 else "passed")
    return score, status, reasons


def security_embed(member):
    score, status, reasons = security_score(member)
    labels = {"passed":"🟢 PASSED", "review":"🟠 REVIEW", "high_risk":"🔴 HIGH RISK"}
    e = embed("🛡️ SECURITY / ANTI-ALT CHECK", f"**Member:** {member.mention}\n**Account created:** {discord.utils.format_dt(member.created_at,'R')}\n**Joined server:** {discord.utils.format_dt(member.joined_at,'R') if member.joined_at else 'Unknown'}\n\n**Risk score:** `{score}`\n**Status:** **{labels[status]}**\n\n" + ("\n".join(f"• {r}" for r in reasons[:8]) if reasons else "No risk signals detected."), discord.Color.green() if status=='passed' else discord.Color.orange() if status=='review' else discord.Color.red())
    e.set_footer(text=f"{BRAND} • Risk signals only — Discord does not provide IP/device identity to bots")
    return e, score, status

class SecurityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
    @discord.ui.button(label="Run Security Check", emoji="🛡️", style=discord.ButtonStyle.primary)
    async def run(self, interaction, button):
        row = db.execute("SELECT opener_id FROM tickets WHERE channel_id=?", (interaction.channel.id,)).fetchone()
        if not row or (interaction.user.id != row["opener_id"] and not staff(interaction.user)):
            return await interaction.response.send_message("❌ Client or staff only.", ephemeral=True)
        e, score, status = security_embed(interaction.guild.get_member(row["opener_id"]) or interaction.user)
        db.execute("UPDATE tickets SET security_score=?,security_status=?,security_checked_at=? WHERE channel_id=?", (score,status,now_iso(),interaction.channel.id)); db.commit()
        await interaction.response.send_message(embed=e, ephemeral=True)

def i2c_rate_for(amount, cfg):
    """I2C uses one normal rate for every INR amount.

    The below/above tier settings belong to C2I and must not change the
    I2C rate shown in tickets or completion/showcase logs.
    """
    try:
        return float(cfg.get("i2c_rate") or 0)
    except (TypeError, ValueError):
        return 0.0


# ----------------------------- Ticket UI -----------------------------
class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="I2C — INR → Crypto", value="I2C", emoji=EMOJI_I2C_C2I or "💱", description="Exchange INR to crypto"),
            discord.SelectOption(label="C2I — Crypto → INR", value="C2I", emoji=EMOJI_I2C_C2I or "💱", description="Exchange crypto to INR"),
            discord.SelectOption(label="C2C — Crypto → Crypto", value="C2C", emoji=EMOJI_C2C or "🔁", description="Exchange one crypto for another"),
        ]
        super().__init__(placeholder="Select an option to proceed…", options=options, custom_id="rishi:flow")

    async def callback(self, interaction):
        blocked = db.execute("SELECT reason FROM blacklists WHERE guild_id=? AND user_id=?", (interaction.guild.id, interaction.user.id)).fetchone()
        if blocked:
            reason = blocked["reason"] or "No reason provided."
            return await interaction.response.send_message(
                embed=embed("🚫 YOU ARE BLACKLISTED", f"You cannot create an exchange ticket on this server.\n\n**Reason:** {reason}\n\nPlease contact Support and open a support ticket to appeal your blacklist.", discord.Color.red()),
                ephemeral=True
            )
        await interaction.response.send_modal(ExchangeModal(self.values[0]))


class ExchangeModal(discord.ui.Modal):
    def __init__(self, flow):
        titles = {
            "I2C": "INR To Crypto Exchange",
            "C2I": "Crypto To INR Exchange",
            "C2C": "Crypto To Crypto Exchange",
            "APPLY": "Apply For Exchange",
        }
        super().__init__(title=f"💜 {titles.get(flow, 'Exchange Request')}", timeout=300)
        self.flow = flow
        if flow == "I2C":
            amount_label, amount_ph = "Amount (INR) **", "Enter amount (e.g. ₹5000)"
            asset_label, asset_ph = "Crypto Currency **", "e.g. BTC, ETH, USDT"
            method_label, method_ph = "UPI ID / Payment App **", "PhonePe / GPay / UPI ID"
        elif flow == "C2I":
            amount_label, amount_ph = "Amount (INR) **", "Enter amount (e.g. ₹5000)"
            asset_label, asset_ph = "Crypto Type **", "e.g. USDT, LTC, SOL"
            method_label, method_ph = "Crypto Wallet **", "e.g. Binance, Exodus, Trust Wallet"
        elif flow == "C2C":
            amount_label, amount_ph = "Amount (USDT) **", "Enter amount (e.g. 100)"
            asset_label, asset_ph = "Send → Receive Crypto **", "e.g. USDT → BTC"
            method_label, method_ph = "Wallet / Network **", "e.g. Binance / TRC20"
        else:
            amount_label, amount_ph = "Amount / Budget **", "Enter amount or budget"
            asset_label, asset_ph = "Exchange Details **", "What do you want to exchange?"
            method_label, method_ph = "Payment / Wallet **", "UPI / Bank / Wallet / Other"
        self.amount = discord.ui.TextInput(label=amount_label, placeholder=amount_ph, required=True, max_length=30)
        self.asset = discord.ui.TextInput(label=asset_label, placeholder=asset_ph, required=True, max_length=100)
        self.method = discord.ui.TextInput(label=method_label, placeholder=method_ph, required=True, max_length=100)
        for item in (self.amount, self.asset, self.method):
            self.add_item(item)

    async def on_submit(self, interaction):
        raw = str(self.amount.value).replace(",", "").replace("₹", "").replace("$", "").strip()
        try:
            amount_in = float(raw)
            if not 0 < amount_in <= 10_000_000:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(embed=embed("❌ INVALID AMOUNT", "Enter a valid positive amount, for example `500` or `60`.", discord.Color.red()), ephemeral=True)
        cfg = getcfg(interaction.guild.id)
        flow = self.flow
        if flow == "I2C":
            rate = float(cfg["i2c_rate"] or 0)
            usdt_amount = amount_in / rate if rate else 0
            inr_value = amount_in
            currency_in = "INR"
        elif flow == "C2I":
            rate = float(cfg["c2i_rate"] or 0)
            inr_value = amount_in
            usdt_amount = amount_in / rate if rate else 0
            currency_in = "INR"
        elif flow == "C2C":
            rate = float(cfg["c2c_rate"] or 0)
            usdt_amount = amount_in
            inr_value = 0
            currency_in = "USDT"
        else:
            rate = 0
            usdt_amount = amount_in
            inr_value = 0
            currency_in = "USDT"
        details = f"Asset: {self.asset.value}\nPayment / Wallet: {self.method.value}"
        await create_ticket(interaction, flow, usdt_amount, details, input_amount=amount_in, input_currency=currency_in, inr_value=inr_value)


class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class TicketActions(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # Use the application-owned emojis supplied in .env.
        for child in self.children:
            if getattr(child, "custom_id", None) == "rishi:claim" and EMOJI_CLAIM:
                child.emoji = EMOJI_CLAIM
            elif getattr(child, "custom_id", None) == "rishi:done" and EMOJI_DONE:
                child.emoji = EMOJI_DONE
            elif getattr(child, "custom_id", None) == "rishi:close" and EMOJI_CLOSED:
                child.emoji = EMOJI_CLOSED

    @discord.ui.button(label="Claim", emoji="💎", style=discord.ButtonStyle.primary, custom_id="rishi:claim")
    async def claim(self, interaction, button):
        row=db.execute("SELECT ticket_type,amount,status,claimed_by FROM tickets WHERE channel_id=?",(interaction.channel.id,)).fetchone()
        if not row or not ticket_manager(interaction.user, row["ticket_type"]):
            return await interaction.response.send_message("❌ You cannot manage this ticket. Exchange roles can manage only I2C/C2I/C2C tickets; Support/Report/Apply tickets are Admin/Owner only.", ephemeral=True)
        if not row or row["status"] != "open":
            return await interaction.response.send_message(embed=embed("❌ CLAIM FAILED","This ticket is no longer open.",discord.Color.red()),ephemeral=True)
        if row["claimed_by"] and row["claimed_by"] != interaction.user.id:
            return await interaction.response.send_message(embed=embed("⚠️ ALREADY CLAIMED","This ticket is already assigned to another exchanger.",discord.Color.orange()),ephemeral=True)
        cfg=getcfg(interaction.guild.id); limit=float(cfg.get("staff_limit_usdt") or 0)
        active=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM tickets WHERE guild_id=? AND claimed_by=? AND status='open'",(interaction.guild.id,interaction.user.id)).fetchone()[0] or 0)
        if limit and active+float(row["amount"] or 0)>limit and interaction.user.id != OWNER_ID and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=embed("🚫 CLAIM LIMIT REACHED",f"Your limit is **${limit:,.2f} USDT**.",discord.Color.red()),ephemeral=True)
        db.execute("UPDATE tickets SET claimed_by=? WHERE channel_id=?",(interaction.user.id,interaction.channel.id)); db.commit()
        active+=float(row["amount"] or 0)
        try:
            safe = re.sub(r"[^a-z0-9-]+", "-", interaction.user.name.lower()).strip("-") or str(interaction.user.id)
            await interaction.channel.edit(name=f"claimed-by-{safe}"[:100], reason=f"Ticket claimed by {interaction.user}")
        except discord.HTTPException:
            pass
        e=embed("💎 TICKET CLAIMED", f"**Claimed by:** {interaction.user.mention}\n**The exchanger is now responsible for this ticket.**\n\n**Claim Amount:** `${float(row['amount'] or 0):,.4f} USDT`", discord.Color.blurple())
        await interaction.response.send_message(embed=e)

    @discord.ui.button(label="Done", emoji="📝", style=discord.ButtonStyle.success, custom_id="rishi:done")
    async def done(self, interaction, button):
        row = db.execute("SELECT ticket_type,amount,rate,status,opener_id,claimed_by,input_amount,input_currency,inr_value FROM tickets WHERE channel_id=?", (interaction.channel.id,)).fetchone()
        if not row:
            return await interaction.response.send_message("❌ Ticket record not found.", ephemeral=True)
        if not ticket_manager(interaction.user, row["ticket_type"]):
            return await interaction.response.send_message("❌ You cannot manage this ticket. Exchange roles can manage only exchange tickets; Support/Report/Apply are Admin/Owner only.", ephemeral=True)
        if not row:
            return await interaction.response.send_message("❌ Ticket record not found.", ephemeral=True)
        await finish_ticket(interaction, row)

    @discord.ui.button(label="Close", emoji="🔴", style=discord.ButtonStyle.danger, custom_id="rishi:close")
    async def close(self, interaction, button):
        row = db.execute("SELECT ticket_type FROM tickets WHERE channel_id=?", (interaction.channel.id,)).fetchone()
        if not row or not ticket_manager(interaction.user, row["ticket_type"]):
            return await interaction.response.send_message("❌ You cannot manage this ticket. Exchange roles can manage only exchange tickets; Support/Report/Apply are Admin/Owner only.", ephemeral=True)
        await close_ticket(interaction)


class AgreementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Accept 3P", emoji="✅", style=discord.ButtonStyle.success, custom_id="rishi:3p_accept")
    async def agree(self, interaction, button):
        row = db.execute(
            "SELECT opener_id,p3_message_id,ticket_type,amount,rate,p3_accepted,p3_declined "
            "FROM tickets WHERE channel_id=?",
            (interaction.channel.id,)
        ).fetchone()
        if not row:
            return await interaction.response.send_message("❌ Ticket record not found.", ephemeral=True)
        if interaction.user.id != row["opener_id"]:
            return await interaction.response.send_message("❌ Only the client who opened this ticket can accept 3P.", ephemeral=True)
        if row["p3_accepted"]:
            return await interaction.response.send_message("ℹ️ 3P is already accepted.", ephemeral=True)

        db.execute("UPDATE tickets SET p3_accepted=1 WHERE channel_id=?", (interaction.channel.id,))
        db.commit()

        await interaction.response.defer(ephemeral=True)

        # Keep the ticket alive. Only replace the 3P prompt (including its image)
        # with a compact confirmation message. The original ticket/details message
        # is intentionally NOT deleted.
        p3_mid = row["p3_message_id"]
        if p3_mid:
            try:
                msg = interaction.channel.get_partial_message(p3_mid)
                await msg.edit(
                    content="🤝 **3P has been agreed successfully.**\n"
                            "The 3P agreement is confirmed and the ticket remains open.",
                    embed=None,
                    attachments=[],
                    view=None
                )
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            embed=embed("🤝 3P AGREED", "3P has been agreed successfully. The ticket was **not** deleted.", discord.Color.green()),
            ephemeral=True
        )
        await send_log(
            interaction.guild,
            "🤝 3P Accepted",
            f"Flow `{row['ticket_type']}` • Amount `{row['amount']:,.2f}` • Rate `{fmt_rate(row['rate'])}`",
            discord.Color.green()
        )


    @discord.ui.button(label="Decline 3P", emoji="❌", style=discord.ButtonStyle.danger, custom_id="rishi:3p_decline")
    async def decline(self, interaction, button):
        row = db.execute("SELECT opener_id,ticket_type,amount,rate,p3_accepted,p3_declined FROM tickets WHERE channel_id=?", (interaction.channel.id,)).fetchone()
        if not row:
            return await interaction.response.send_message("❌ Ticket record not found.", ephemeral=True)
        if interaction.user.id != row["opener_id"] and not staff(interaction.user):
            return await interaction.response.send_message("❌ Only the client or staff can decline 3P.", ephemeral=True)
        if row["p3_accepted"]:
            return await interaction.response.send_message("ℹ️ 3P was already accepted, so it can no longer be declined.", ephemeral=True)
        if row["p3_declined"]:
            return await interaction.response.send_message("ℹ️ The 3P surcharge has already been applied once.", ephemeral=True)
        cfg = getcfg(interaction.guild.id)
        new_rate = float(row["rate"]) + float(cfg["decline_penalty"] or 0)
        db.execute("UPDATE tickets SET p3_declined=p3_declined+1, rate=? WHERE channel_id=?", (new_rate, interaction.channel.id)); db.commit()
        await interaction.response.send_message(embed=embed("⚠️ 3P DECLINED", f"Non-agreement surcharge applied.\n\n**New rate:** `{fmt_rate(new_rate)} / USDT`\n**New value:** `{fmt_rate(row['amount'] * new_rate)}`", discord.Color.orange()))
        await send_log(interaction.guild, "⚠️ 3P Declined", f"Flow `{row['ticket_type']}` • Adjusted rate `{fmt_rate(new_rate)}`", discord.Color.orange())


async def is_ticket_opener(interaction):
    row = db.execute("SELECT opener_id FROM tickets WHERE channel_id=?", (interaction.channel.id,)).fetchone()
    return bool(row and row["opener_id"] == interaction.user.id)


async def send_3p(target):
    channel = target.channel
    row = db.execute("SELECT ticket_message_id FROM tickets WHERE channel_id=?", (channel.id,)).fetchone()
    if not row:
        msg = embed("❌ Not a Ticket", "3P can only be used inside an exchange ticket.", discord.Color.red())
        return await (target.response.send_message(embed=msg, ephemeral=True) if isinstance(target, discord.Interaction) else target.send(embed=msg))
    cfg = getcfg(channel.guild.id)
    e = embed(
        f"🤝 3P AGREEMENT • {BRAND}",
        "Review the exchange details before accepting.\n\n"
        "• Confirm amount, flow and payment method.\n"
        "• Never share OTPs, passwords, seed phrases or private keys.\n"
        f"• {cfg['tos_text']}\n\n"
        f"⚠️ Declining 3P applies the configured surcharge: `+₹{float(cfg['decline_penalty'] or 0):.2f}/USDT`.",
        discord.Color.blurple()
    )
    file = None
    if THREE_P_IMAGE and os.path.exists(THREE_P_IMAGE):
        filename = os.path.basename(THREE_P_IMAGE)
        file = discord.File(THREE_P_IMAGE, filename=filename)
        e.set_image(url=f"attachment://{filename}")
    view = AgreementView()
    if isinstance(target, discord.Interaction):
        await target.response.send_message(embed=e, file=file, view=view)
        msg = await target.original_response()
    else:
        msg = await target.send(embed=e, file=file, view=view)
    db.execute("UPDATE tickets SET p3_message_id=? WHERE channel_id=?", (msg.id, channel.id)); db.commit()


async def create_ticket(interaction, flow, amount, notes="", input_amount=None, input_currency=None, inr_value=0):
    guild, user = interaction.guild, interaction.user
    cfg = getcfg(guild.id)
    existing = db.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND opener_id=? AND status='open'", (guild.id, user.id)).fetchone()
    if existing and guild.get_channel(existing["channel_id"]):
        return await interaction.response.send_message(embed=embed("🎫 OPEN TICKET EXISTS", f"You already have an open ticket: <#{existing['channel_id']}>\n\nPlease finish or close it before opening another.", discord.Color.orange()), ephemeral=True)
    cooldown = max(0, int(cfg.get("ticket_cooldown_minutes") or 0))
    if cooldown:
        recent = db.execute("SELECT created_at FROM tickets WHERE guild_id=? AND opener_id=? ORDER BY created_at DESC LIMIT 1", (guild.id, user.id)).fetchone()
        if recent:
            try:
                age = (discord.utils.utcnow() - discord.utils.parse_time(recent["created_at"])).total_seconds() / 60
                if age < cooldown:
                    return await interaction.response.send_message(embed=embed("⏳ PLEASE WAIT", f"You can open another exchange ticket in **{max(1, int(cooldown-age))} min**.", discord.Color.orange()), ephemeral=True)
            except Exception: pass
    if cfg.get("security_enabled", 1) and cfg.get("require_security_check"):
        score, status, reasons = security_score(user)
        if status == "high_risk":
            return await interaction.response.send_message(embed=embed("🛡️ SECURITY CHECK REQUIRED", "Your account needs staff review before opening an exchange ticket.\n\n" + "\n".join(f"• {r}" for r in reasons[:5]), discord.Color.red()), ephemeral=True)
    cat_key = {"I2C":"i2c_category","C2I":"c2i_category","C2C":"c2c_category","APPLY":"apply_category"}.get(flow, "ticket_category")
    category = guild.get_channel(cfg.get(cat_key) or cfg.get("ticket_category") or 0)
    input_amount = float(input_amount if input_amount is not None else amount)
    rate = (i2c_rate_for(input_amount, cfg) if flow == "I2C" else {"C2I":cfg["c2i_rate"],"C2C":0,"APPLY":0}.get(flow,0))
    inr_value = float(inr_value or 0)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, read_message_history=True),
    }
    # Exchange roles get access only to exchange tickets. They do not receive any
    # server permissions from this system; the role is only granted channel access.
    if flow in {"I2C", "C2I", "C2C"}:
        exchange_roles = configured_exchange_roles(guild)
        for role in exchange_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            )
    else:
        # Apply/Support/Report tickets are owner/admin-only. The bot owner is added
        # explicitly in case the configured owner is not a Discord Administrator.
        owner = guild.get_member(OWNER_ID) if OWNER_ID else None
        if owner:
            overwrites[owner] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            )
    name = f"unclaimed-{flow.lower()}-{user.name[:12]}" if flow not in {"APPLY", "SUPPORT", "REPORT"} else f"unclaimed-{flow.lower()}-{user.name[:12]}"
    try:
        channel = await guild.create_text_channel(name, category=category, overwrites=overwrites, reason=f"{BRAND} ticket")
    except discord.Forbidden:
        return await interaction.response.send_message(embed=embed("❌ TICKET CREATION FAILED", "The bot needs **Manage Channels** and **Manage Permissions**.\n\nFix: give the bot those permissions and make sure its role is above the staff role.", discord.Color.red()), ephemeral=True)
    db.execute("INSERT INTO tickets(channel_id,guild_id,opener_id,ticket_type,amount,rate,status,created_at,notes,input_amount,input_currency,inr_value) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (channel.id,guild.id,user.id,flow,float(amount),float(rate or 0),"open",now_iso(),notes[:1000],input_amount,input_currency or "USDT",inr_value))
    db.commit()
    direction={"I2C":"INR → USDT","C2I":"USDT → INR","C2C":"CRYPTO → CRYPTO","APPLY":"CUSTOM EXCHANGE"}.get(flow,flow)
    if flow == "I2C":
        summary=(f"**Exchange Amount:** `₹{input_amount:,.2f}`\n**Crypto:** `{notes.split('Asset: ',1)[-1].splitlines()[0] if 'Asset: ' in notes else 'USDT'}`\n"
                 f"**Rate:** `{fmt_rate(rate)} / USDT`\n**Estimated Crypto:** `{amount:,.4f} USDT`\n"
                 f"**Payment:** `{notes.split('Payment / Wallet: ',1)[-1].splitlines()[0] if 'Payment / Wallet: ' in notes else 'Not specified'}`")
    elif flow == "C2I":
        summary=(f"**Exchange Amount:** `{amount:,.4f} USDT`\n**Rate:** `{fmt_rate(rate)} / USDT`\n**INR Payout:** `₹{inr_value:,.2f}`\n"
                 f"**Crypto / Wallet:** `{notes.split('Asset: ',1)[-1].splitlines()[0] if 'Asset: ' in notes else 'USDT'}`")
    elif flow == "C2C":
        summary=(f"**Exchange Amount:** `{amount:,.4f} USDT`\n**Pair:** `{notes.split('Asset: ',1)[-1].splitlines()[0] if 'Asset: ' in notes else 'Crypto → Crypto'}`\n"
                 f"**Network / Wallet:** `{notes.split('Payment / Wallet: ',1)[-1].splitlines()[0] if 'Payment / Wallet: ' in notes else 'Manual quote'}`")
    else:
        summary=f"**Requested Amount:** `{input_amount:,.2f}`\n**Request:** `{notes[:700] or 'No extra details provided.'}`"
    e=embed(f"💜 WELCOME TO {BRAND.upper()}",
            f"Hello {user.mention}!\n\nRead the TOS and wait for an authorised exchanger to claim this ticket.\n\n"
            f"**{direction}**\n{summary}\n\n"
            "🙋 **First exchanger to claim this ticket handles it.**\n"
            "⏳ **Ticket will be auto-closed after 2 hours if nobody claims it.**\n\n"
            f"**TOS:** {cfg['tos_text'][:700]}", discord.Color.blurple())
    # Reference-inspired ticket panel with rates, request details and optional visual.
    e.clear_fields()
    e.add_field(name=f"{PANEL_E_RATE} EXCHANGE RATES", value=(f"**INR → Crypto:** `{fmt_rate(cfg['i2c_rate'])} / $`\n**Crypto → INR:** `{fmt_rate(cfg['c2i_rate'])} / $`\n**Crypto → Crypto:** `{fmt_rate(cfg['c2c_rate'])} / $`"), inline=False)
    e.add_field(name=f"{PANEL_E_INFO} TICKET REQUEST", value=f"**Type:** `{flow}`\n{summary}", inline=False)
    e.add_field(name=f"{PANEL_E_INFO} NEXT STEP", value="An authorised exchanger will claim this ticket. Please do not send payment until the exchanger confirms the details.", inline=False)
    if bot.user:
        e.set_author(name=f"{BRAND} • Exchange Ticket", icon_url=bot.user.display_avatar.url)
    if PANEL_IMAGE_URL:
        e.set_thumbnail(url=PANEL_IMAGE_URL)
    e.set_footer(text=PANEL_FOOTER_TEXT or f"{BRAND} • Fixed rates • No negotiation")
    role_mentions = []
    if flow in {"I2C", "C2I", "C2C"}:
        role_mentions = [r.mention for r in configured_exchange_roles(guild)]
    mention_text = " ".join(role_mentions) if role_mentions else ""
    msg=await channel.send(
        content=mention_text or None,
        embed=e,
        view=TicketActions(),
        allowed_mentions=discord.AllowedMentions(roles=True, users=True)
    )
    db.execute("UPDATE tickets SET ticket_message_id=? WHERE channel_id=?",(msg.id,channel.id)); db.commit()
    await interaction.response.send_message(embed=embed("🎫 TICKET CREATED", f"Your private exchange ticket is ready: {channel.mention}\n\n**Type:** `{flow}` • **Amount:** `{input_amount:,.2f} {input_currency or 'USDT'}`", discord.Color.green()), ephemeral=True)
    await send_log(guild,"🎫 Ticket Opened",f"**Type:** `{flow}` • **Input:** `{input_amount:,.2f} {input_currency or 'USDT'}` • **USDT:** `{amount:,.4f}` • {channel.mention}",discord.Color.green())


async def build_ticket_transcript(channel):
    rows = []
    try:
        async for message in channel.history(limit=None, oldest_first=True):
            timestamp = discord.utils.format_dt(message.created_at, "F")
            author = html_escape(f"{message.author} ({message.author.id})")
            content = html_escape(message.content or "").replace("\n", "<br>")
            attachments = "".join(f'<div><a href="{html_escape(a.url, quote=True)}">Attachment: {html_escape(a.filename)}</a></div>' for a in message.attachments)
            embeds = "".join(f"<div>Embed: {html_escape(e.title or 'Untitled')}</div>" for e in message.embeds)
            rows.append(f"<article><b>{author}</b> <small>{html_escape(timestamp)}</small><div>{content}</div>{attachments}{embeds}</article>")
    except discord.HTTPException:
        rows.append("<p>Some messages could not be retrieved.</p>")
    body = "\n".join(rows) or "<p>No messages found.</p>"
    title = html_escape(channel.name)
    html = "<!doctype html><html><head><meta charset=\"utf-8\"><title>Transcript - " + title + "</title>" + "<style>body{font-family:Arial,sans-serif;background:#f5f5f5;color:#222;padding:24px}article{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 12px;margin:8px 0}small{color:#777}a{word-break:break-all}</style></head><body><h1>Ticket Transcript: " + title + "</h1><p>Channel ID: " + str(channel.id) + "<br>Guild: " + html_escape(channel.guild.name) + "</p>" + body + "</body></html>"
    return html.encode("utf-8")

async def save_ticket_transcript(guild, channel, transaction_id=None):
    cfg = getcfg(guild.id)
    destination = guild.get_channel(cfg.get("log_channel") or 0)
    if not destination:
        return False
    try:
        data = await build_ticket_transcript(channel)
        await destination.send(content=f"📄 **Ticket Transcript Saved**\n**Channel:** `{channel.name}`\n**Transaction:** `#{transaction_id or 'N/A'}`", file=discord.File(io.BytesIO(data), filename=f"transcript-{channel.id}-{transaction_id or 'ticket'}.html"), allowed_mentions=discord.AllowedMentions.none())
        return True
    except discord.HTTPException:
        return False

class FeedbackView(discord.ui.View):
    def __init__(self, ticket_channel_id, client_id, exchanger_id):
        super().__init__(timeout=1800)
        self.ticket_channel_id, self.client_id, self.exchanger_id = ticket_channel_id, client_id, exchanger_id

    async def submit(self, interaction, rating):
        if interaction.user.id != self.client_id:
            return await interaction.response.send_message("❌ Only the client who opened this ticket can submit feedback.", ephemeral=True)
        if db.execute("SELECT id FROM feedback WHERE ticket_channel_id=?", (self.ticket_channel_id,)).fetchone():
            return await interaction.response.send_message("ℹ️ Feedback has already been submitted for this ticket.", ephemeral=True)
        db.execute("INSERT INTO feedback(guild_id,ticket_channel_id,client_id,exchanger_id,rating,comment,created_at) VALUES(?,?,?,?,?,?,?)", (interaction.guild.id,self.ticket_channel_id,self.client_id,self.exchanger_id,rating,"",now_iso()))
        db.commit()
        await interaction.response.send_message(f"Thank you for your feedback! {'⭐' * rating}", ephemeral=True)
        await send_log(interaction.guild, "⭐ Client Feedback", f"**Ticket:** <#{self.ticket_channel_id}>\n**Client:** <@{self.client_id}>\n**Exchanger:** <@{self.exchanger_id}>\n**Rating:** `{rating}/5`", discord.Color.gold())
        for child in self.children: child.disabled = True
        try: await interaction.message.edit(view=self)
        except discord.HTTPException: pass

    @discord.ui.button(label="1 Star", style=discord.ButtonStyle.secondary, custom_id="feedback:1")
    async def one(self, interaction, button): await self.submit(interaction, 1)
    @discord.ui.button(label="2 Stars", style=discord.ButtonStyle.secondary, custom_id="feedback:2")
    async def two(self, interaction, button): await self.submit(interaction, 2)
    @discord.ui.button(label="3 Stars", style=discord.ButtonStyle.secondary, custom_id="feedback:3")
    async def three(self, interaction, button): await self.submit(interaction, 3)
    @discord.ui.button(label="4 Stars", style=discord.ButtonStyle.primary, custom_id="feedback:4")
    async def four(self, interaction, button): await self.submit(interaction, 4)
    @discord.ui.button(label="5 Stars", style=discord.ButtonStyle.success, custom_id="feedback:5")
    async def five(self, interaction, button): await self.submit(interaction, 5)

async def finish_ticket(target, row):
    flow, amount, rate, status = row["ticket_type"], float(row["amount"] or 0), float(row["rate"] or 0), row["status"]
    if status == "done":
        msg=embed("ℹ️ ALREADY COMPLETED","This ticket is already marked completed.",discord.Color.orange())
        return await (target.response.send_message(embed=msg,ephemeral=True) if isinstance(target,discord.Interaction) else target.send(embed=msg))
    if status != "open":
        msg=embed("❌ TICKET NOT OPEN","Only open tickets can be completed. Reopen it first if it was closed or expired.",discord.Color.red())
        return await (target.response.send_message(embed=msg,ephemeral=True) if isinstance(target,discord.Interaction) else target.send(embed=msg))
    if not row["claimed_by"]:
        msg=embed("🙋 CLAIM REQUIRED","Claim this ticket before completing it so the deal is credited to an exchanger.",discord.Color.orange())
        return await (target.response.send_message(embed=msg,ephemeral=True) if isinstance(target,discord.Interaction) else target.send(embed=msg))
    cfg = getcfg(target.guild.id)
    ticket_row = db.execute("SELECT p3_accepted,security_status FROM tickets WHERE channel_id=?", (target.channel.id,)).fetchone()
    if cfg.get("require_3p") and (not ticket_row or not ticket_row["p3_accepted"]):
        msg = embed("🤝 3P REQUIRED", "This server requires the 3P agreement before a deal can be completed. Use the **3P Agreement** button first.", discord.Color.orange())
        return await (target.response.send_message(embed=msg,ephemeral=True) if isinstance(target,discord.Interaction) else target.send(embed=msg))
    if cfg.get("require_security_check") and (not ticket_row or ticket_row["security_status"] != "passed"):
        msg = embed("🛡️ SECURITY CHECK REQUIRED", "Run the security check and resolve any staff review before completing this ticket.", discord.Color.orange())
        return await (target.response.send_message(embed=msg,ephemeral=True) if isinstance(target,discord.Interaction) else target.send(embed=msg))
    now=now_iso()
    inr_value=float(row["inr_value"] or 0) if "inr_value" in row.keys() else (amount*rate if rate else 0)
    flip=db.execute("UPDATE tickets SET status='done' WHERE channel_id=? AND status='open'",(target.channel.id,))
    if flip.rowcount == 0:
        db.commit()
        return
    cur=db.execute("INSERT INTO exchanges(guild_id,ticket_channel_id,ticket_type,amount,rate,status,created_at,opener_id,claimed_by,inr_value,input_amount,input_currency) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (target.guild.id,target.channel.id,flow,amount,rate,"completed",now,row["opener_id"],row["claimed_by"],inr_value,float(row["input_amount"] or amount) if "input_amount" in row.keys() else amount,row["input_currency"] if "input_currency" in row.keys() else "USDT"))
    transaction_id=cur.lastrowid
    db.commit()
    exchanger_id = row["claimed_by"] or 0
    if exchanger_id:
        db.execute("INSERT INTO user_stats(guild_id,user_id,completed_count,completed_volume) VALUES(?,?,1,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET completed_count=completed_count+1,completed_volume=completed_volume+excluded.completed_volume",(target.guild.id,exchanger_id,amount))
        db.commit()
    completed_dt=discord.utils.utcnow()
    await save_ticket_transcript(target.guild, target.channel, transaction_id)
    await send_done_record(target.guild,flow,amount,rate,transaction_id,completed_dt,claimed_by=row["claimed_by"],opener_id=row["opener_id"],inr_value=inr_value,input_amount=float(row["input_amount"] or amount) if "input_amount" in row.keys() else amount,input_currency=row["input_currency"] if "input_currency" in row.keys() else "USDT")
    await send_internal_done_record(target.guild,flow,amount,rate,transaction_id,row["opener_id"],row["claimed_by"])
    await update_total_counter(target.guild)
    await refresh_leaderboard(target.guild)
    await send_log(target.guild,"✅ Ticket Completed",f"Transaction `#{transaction_id}` • `{flow}` • `{amount:,.4f} USDT` • `{fmt_rate(rate) if rate else 'Manual quote'}`",discord.Color.green())
    flow_rep = {
        "I2C": "EXCHANGED UPI TO USDT",
        "C2I": "EXCHANGED USDT TO UPI",
        "C2C": "EXCHANGED CRYPTO TO CRYPTO",
    }.get(flow, "COMPLETED EXCHANGE")
    rep_mention = f"<@{row['claimed_by']}>" if row["claimed_by"] else "<@EXCHANGER_ID>"
    rep_line = f"+rep {rep_mention} **{flow_rep} [${float(amount):,.2f}]**"
    msg=embed("✅ EXCHANGE COMPLETED",
        f"**Transaction:** `#{transaction_id}`\n**Status:** `Completed & Logged`\n\n"
        f"**Copy this vouch:**\n```{rep_line}```\n"
        "If you do not vouch after the exchange, you may be **blacklisted** from future exchanges.\n\n"
        "This ticket will remain open for **30 minutes** so you can copy the message. After that, staff can allow it to be removed.\n\n"
        f"**Showcase:** {'Posted' if getcfg(target.guild.id).get('showcase_channel') or getcfg(target.guild.id).get('done_channel') else 'Not configured'}", discord.Color.green())
    if isinstance(target,discord.Interaction):
        await target.response.send_message(embed=msg)
        await target.channel.send(rep_line, allowed_mentions=discord.AllowedMentions(users=True))
    else:
        await target.send(embed=msg)
        await target.send(rep_line, allowed_mentions=discord.AllowedMentions(users=True))
    await target.channel.send("⭐ **Rate your exchanger**\nPlease select a rating from **1 to 5 stars** below.", view=FeedbackView(target.channel.id, row["opener_id"], row["claimed_by"] or 0))
    # Do not rename/move the channel during .x. Channel PATCH requests can
    # trigger Discord rate limits and delay the completion command. The ticket
    # remains in place, while permissions are updated safely below.
    try:
        opener = target.guild.get_member(row["opener_id"])
        if opener:
            await target.channel.set_permissions(
                opener, view_channel=True, send_messages=False,
                read_message_history=True, attach_files=False
            )
    except discord.HTTPException as exc:
        log.warning(f"Ticket permission update skipped: status={getattr(exc, 'status', 'unknown')} retry_after={getattr(exc, 'retry_after', 'unknown')}")


async def close_ticket(target):
    row = db.execute("SELECT ticket_type FROM tickets WHERE channel_id=?", (target.channel.id,)).fetchone()
    if not row:
        msg = embed("❌ Not a Ticket", "This is not a registered exchange ticket.", discord.Color.red())
        return await (target.response.send_message(embed=msg, ephemeral=True) if isinstance(target, discord.Interaction) else target.send(embed=msg))
    db.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (target.channel.id,)); db.commit()
    try:
        await target.channel.edit(name=f"closed-{target.channel.name}"[:95])
        await target.channel.set_permissions(target.guild.default_role, view_channel=False)
    except discord.HTTPException:
        pass
    await send_log(target.guild, "🔒 Ticket Closed", f"Type `{row['ticket_type']}` • No public completion record was created.", discord.Color.orange())
    msg = embed("🔒 Ticket Closed", "Ticket closed. No completion transaction was recorded.", discord.Color.orange())
    if isinstance(target, discord.Interaction): await target.response.send_message(embed=msg)
    else: await target.send(embed=msg)


# ----------------------------- Help -----------------------------
# Help is generated from the commands actually registered with discord.py, so
# newly-added commands cannot silently disappear from `.help`.
OWNER_ONLY_COMMANDS = {"access", "configreset", "reloadconfig", "backup"}
STAFF_HELP_COMMANDS = STAFF_COMMANDS | {"exprofile"}
PUBLIC_HELP_COMMANDS = PUBLIC_COMMANDS

HELP_GROUPS = [
    ("👋 General", {"help", "help2", "ping", "uptime", "rate", "rates", "tos", "terms", "rules", "calc"}),
    ("💱 Exchange", {"ticketpanel", "exchange", "panel", "applypanel", "apply", "3p", "tos3p", "upi", "p", "profile", "exprofile", "stats", "statistics", "total", "trans", "logs", "transactions", "audit", "leaderboard", "lb", "prizes", "securitycheck", "security"}),
    ("🎫 Tickets", {"ticketinfo", "claim", "done", "close", "reopen", "add", "remove", "rename", "ticketlock", "ticketunlock", "blacklist", "blacklistcheck", "unblacklist"}),
    ("⚙️ Configuration", {"config", "addexrole", "remexrole", "exroles", "setrate", "setc2cfee", "setpenalty", "settos", "setcategory", "seti2ccategory", "setc2icategory", "setc2ccategory", "setapplycategory", "setarchivecategory", "setstaff", "setstafflimit", "setunclaimedtimeout", "setlog", "setshowcase", "setdonechannel", "settotalchannel", "setpanelchannel", "setleaderboardchannel", "setlbchannel", "setleaderboardprize", "setprize", "set3prequired", "set3p", "setsecurity", "setaccountage", "setjoinage", "setdemo", "setdemointerval", "setdemorange", "diagnostics", "diag", "checkconfig", "rebuildstats", "setup", "configreset", "resetconfig", "reloadconfig"}),
    ("🛡️ Moderation", {"kick", "ban", "unban", "timeout", "untimeout", "clear", "purge", "slowmode", "lock", "unlock", "warn", "warnings", "nick", "role", "unrole"}),
    ("🧰 Server Tools", {"say", "announce", "channelid", "topic", "avatar", "userinfo", "serverinfo", "roleinfo", "channelinfo", "inviteinfo"}),
    ("🔐 Owner", OWNER_ONLY_COMMANDS),
]

HELP_DESCRIPTIONS = {
    "help": "Show every prefix command",
    "help2": "Show every non-owner prefix command",
    "ping": "Bot latency",
    "uptime": "Bot uptime",
    "tos": "Show TOS", "terms": "Show TOS", "rules": "Show rules/TOS",
    "rate": "Show current exchange rates", "rates": "Show current exchange rates",
    "calc": "Safe calculator",
    "ticketpanel": "Post exchange panel", "exchange": "Post exchange panel", "panel": "Post exchange panel",
    "applypanel": "Post exchanger application panel", "apply": "Open application panel",
    "3p": "Send 3P agreement", "tos3p": "Send 3P agreement", "upi": "Generate UPI QR",
    "p": "Private client profile", "profile": "Private client profile", "exprofile": "Exchanger profile",
    "stats": "Exchange statistics", "statistics": "Exchange statistics", "total": "Lifetime completed volume",
    "trans": "Anonymous transaction history", "logs": "Anonymous transaction history", "transactions": "Anonymous transaction history", "audit": "Audit history",
    "leaderboard": "Monthly exchanger leaderboard", "lb": "Monthly exchanger leaderboard", "prizes": "Show leaderboard rewards",
    "securitycheck": "Run account safety check", "security": "Run account safety check",
    "ticketinfo": "Show current ticket details", "claim": "Claim an exchange ticket", "addexrole": "Add an exchange-only ticket role", "remexrole": "Remove an exchange-only ticket role", "exroles": "List exchange ticket roles", "blacklist": "Blacklist a client", "blacklistcheck": "Check blacklist status", "unblacklist": "Remove a blacklist (admin/support)", "x": "Complete ticket, save transcript, post +rep and request client feedback", "done": "Complete an exchange", "close": "Close ticket", "reopen": "Reopen ticket",
    "add": "Add member to ticket", "remove": "Remove member from ticket", "rename": "Rename ticket", "ticketlock": "Lock client messaging", "ticketunlock": "Unlock client messaging",
    "config": "Show server configuration", "setrate": "Set I2C below/above tier or C2I rate", "setc2cfee": "Set Crypto to Crypto fee", "setpenalty": "Set 3P penalty", "settos": "Set TOS text",
    "setcategory": "Set fallback ticket category", "seti2ccategory": "Set I2C category", "setc2icategory": "Set C2I category", "setc2ccategory": "Set C2C category",
    "setapplycategory": "Set application category", "setarchivecategory": "Set application/support archive category", "setstaff": "Set legacy exchange role", "setstafflimit": "Set staff active-volume limit",
    "setunclaimedtimeout": "Set unclaimed-ticket timeout", "setlog": "Set private audit-log channel", "setshowcase": "Set public showcase channel", "setdonechannel": "Set completion/audit channel",
    "settotalchannel": "Set locked total counter", "setpanelchannel": "Set exchange panel channel", "setleaderboardchannel": "Set leaderboard channel", "setlbchannel": "Set leaderboard channel",
    "setleaderboardprize": "Set leaderboard prize", "setprize": "Set leaderboard prize", "set3prequired": "Require 3P", "set3p": "Require 3P",
    "setsecurity": "Configure security mode", "setaccountage": "Minimum account age", "setjoinage": "Minimum server age", "setdemo": "Demo showcase mode",
    "setdemointerval": "Demo interval", "setdemorange": "Demo amount range", "diagnostics": "Configuration health check", "diag": "Configuration health check", "checkconfig": "Configuration health check",
    "rebuildstats": "Rebuild exchanger statistics", "setup": "Setup guide", "configreset": "Reset configuration", "resetconfig": "Reset configuration", "reloadconfig": "Reload database configuration",
    "kick": "Kick member", "ban": "Ban member", "unban": "Unban by ID", "timeout": "Timeout member", "untimeout": "Remove timeout", "clear": "Delete messages", "purge": "Delete messages",
    "slowmode": "Set channel slowmode", "lock": "Lock channel", "unlock": "Unlock channel", "warn": "Issue persistent warning", "warnings": "View warnings", "nick": "Change nickname",
    "role": "Add role", "unrole": "Remove role", "say": "Send clean message", "announce": "Send announcement embed", "channelid": "Show channel ID", "topic": "Edit channel topic",
    "avatar": "Show member avatar", "userinfo": "Show member information", "serverinfo": "Show server information", "roleinfo": "Show role information", "channelinfo": "Show channel information", "inviteinfo": "Show invite information",
    "access": "Show owner access mode",
    "backup": "Take a database backup now", "export": "Export completed exchanges to CSV",
}


def _help_permission(command_name):
    if command_name in OWNER_ONLY_COMMANDS:
        return "Owner"
    if command_name in STAFF_HELP_COMMANDS:
        return "Staff"
    if command_name in PUBLIC_HELP_COMMANDS:
        return "Everyone"
    return "Admin / Discord permission"


def _help_group(command_name):
    for group, names in HELP_GROUPS:
        if command_name in names:
            return group
    return "🧩 Other"


def _registered_prefix_commands():
    # Exclude dynamically-generated aliases from appearing as fake standalone
    # commands: discord.py exposes aliases through Command.aliases.
    result = {}
    for cmd in bot.commands:
        result[cmd.name] = cmd
    return result


def _help_lines(include_owner=True):
    commands = _registered_prefix_commands()
    grouped = defaultdict(list)
    for name, cmd in commands.items():
        if not include_owner and name in OWNER_ONLY_COMMANDS:
            continue
        grouped[_help_group(name)].append(cmd)

    # Preserve the deliberate UI order, then put any future/unclassified command
    # at the bottom automatically.
    ordered_groups = [g for g, _ in HELP_GROUPS] + ["🧩 Other"]
    out = []
    for group in ordered_groups:
        cmds = grouped.get(group, [])
        if not cmds:
            continue
        cmds.sort(key=lambda c: c.name)
        lines = []
        for cmd in cmds:
            aliases = [a for a in cmd.aliases if a != cmd.name]
            syntax = f"{PREFIX}{cmd.name}"
            if aliases:
                syntax += "  " + " / ".join(f"{PREFIX}{a}" for a in aliases)
            desc = HELP_DESCRIPTIONS.get(cmd.name, (cmd.help or "No description").split("\n")[0])
            perm = _help_permission(cmd.name)
            lines.append(f"`{syntax}` — {desc} • **{perm}**")
        out.append((group, lines))
    return out


def _chunk_lines(lines, max_chars=1000):
    chunks, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(current)); current=[]; size=0
        current.append(line); size += len(line) + 1
    if current: chunks.append("\n".join(current))
    return chunks


def _embed_char_count(e):
    # Discord's 6000-character limit is the TOTAL across all embeds in one message.
    # Count the main text plus title/footer/fields so help can never trip error 50035.
    total = len(e.title or "") + len(e.description or "") + len(e.footer.text or "")
    for f in e.fields:
        total += len(f.name or "") + len(f.value or "")
    return total


async def _send_help(ctx, include_owner=True):
    title = "⚡ COMMAND CENTER" if include_owner else "📘 MEMBER COMMANDS"
    subtitle = (
        f"**{len(_registered_prefix_commands())} prefix commands registered.**\n"
        f"`{PREFIX}help` = complete command list • `{PREFIX}help2` = everything except owner-only commands"
        if include_owner else
        f"Commands available outside the owner-only section.\nUse `{PREFIX}help` for the complete command registry."
    )

    embeds = []
    # Keep each embed comfortably below Discord's per-embed description limit.
    for group, lines in _help_lines(include_owner=include_owner):
        for idx, chunk in enumerate(_chunk_lines(lines, max_chars=3000)):
            e = embed(
                f"{title} • {group}" if idx == 0 else f"{title} • {group} (cont.)",
                chunk,
            )
            if idx == 0:
                e.description = subtitle + "\n\n" + (e.description or "")
            embeds.append(e)

    # Discord allows max 10 embeds AND max 6000 total characters per message.
    # The old implementation only respected the 10-embed rule, so `.help` could
    # generate HTTP 400 / error 50035 when all command embeds were sent together.
    batch = []
    batch_chars = 0
    for e in embeds:
        size = _embed_char_count(e)
        if batch and (len(batch) >= 10 or batch_chars + size > 5800):
            await ctx.send(embeds=batch)
            batch = []
            batch_chars = 0
        batch.append(e)
        batch_chars += size
    if batch:
        await ctx.send(embeds=batch)


@bot.command()
async def help(ctx):
    """Show every registered prefix command, including owner-only commands."""
    await _send_help(ctx, include_owner=True)


@bot.command()
async def help2(ctx):
    """Show every registered prefix command except owner-only commands."""
    await _send_help(ctx, include_owner=False)


# ----------------------------- General / exchange commands -----------------------------
@bot.command()
async def ping(ctx):
    await ctx.send(embed=embed("🏓 Pong", f"Latency: **{round(bot.latency * 1000)}ms**"))

@bot.command()
async def uptime(ctx):
    s = int(time.time() - START_TIME)
    await ctx.send(embed=embed("⏱️ Uptime", f"`{s//86400}d {(s%86400)//3600}h {(s%3600)//60}m {s%60}s`"))

@bot.command(aliases=["terms","rules"])
async def tos(ctx):
    c=getcfg(ctx.guild.id); await ctx.send(embed=embed("📜 TERMS OF SERVICE",c.get('tos_text') or "No TOS configured."))

@bot.command(aliases=["rates"])
async def rate(ctx):
    c = getcfg(ctx.guild.id)
    await ctx.send(embed=embed(f"💹 LIVE RATES • {BRAND}", f"**I2C:** `{fmt_rate(c['i2c_rate'])} / USDT`\n**C2I:** `{fmt_rate(c['c2i_rate'])} / USDT`\n**C2C:** `{fmt_rate(c['c2c_rate'])} / USDT` (manual/confirm before trade)\n**3P decline surcharge:** `+₹{float(c['decline_penalty'] or 0):.2f} / USDT`"))

@bot.command()
async def calc(ctx, *, expression):
    try: result = safe_calc(expression)
    except Exception: return await ctx.send(embed=embed("❌ Calculation Error", f"Example: `{PREFIX}calc 25*4+10`", discord.Color.red()))
    await ctx.send(embed=embed("🧮 Calculator", f"`{expression}` = **{result}**", discord.Color.green()))

@bot.command(name="3p", aliases=["tos3p"])
async def threep(ctx): await send_3p(ctx)

@bot.command()
async def upi(ctx, action: str = None, *, value: str = None):
    """Save, list, or generate a saved UPI QR inside a ticket."""
    if not staff(ctx.author):
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange staff only.", discord.Color.red()))
    if (action or '').lower() == 'save':
        if not value:
            return await ctx.send(embed=embed("❌ Missing UPI", f"Use `{PREFIX}upi save <name> <upi_id>` (example: `{PREFIX}upi save rishi rishi@oksbi`).", discord.Color.red()))
        parts = value.split(maxsplit=1)
        if len(parts) != 2 or not extract_upi(parts[1]):
            return await ctx.send(embed=embed("❌ Invalid UPI", f"Use `{PREFIX}upi save rishi rishi@oksbi`.", discord.Color.red()))
        name, upi_id = parts[0].strip().lower(), extract_upi(parts[1])
        if not re.fullmatch(r'[a-z0-9_-]{1,32}', name):
            return await ctx.send(embed=embed("❌ Invalid Name", "Name may contain only letters, numbers, `_` or `-`.", discord.Color.red()))
        db.execute("INSERT INTO saved_upi(guild_id,user_id,name,upi_id,created_at) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,user_id,name) DO UPDATE SET upi_id=excluded.upi_id,created_at=excluded.created_at", (ctx.guild.id, ctx.author.id, name, upi_id, now_iso())); db.commit()
        return await ctx.send(embed=embed("✅ UPI SAVED", f"Saved **{name}** for {ctx.author.mention}.\n\nUse `{PREFIX}upi {name}` inside a ticket to generate the QR with that ticket's amount automatically.", discord.Color.green()))
    if (action or '').lower() == 'list':
        rows=db.execute("SELECT name,upi_id FROM saved_upi WHERE guild_id=? AND user_id=? ORDER BY name", (ctx.guild.id,ctx.author.id)).fetchall()
        text='\n'.join(f"• `{r['name']}` → `{r['upi_id']}`" for r in rows) or 'No saved UPI methods.'
        return await ctx.send(embed=embed("💳 SAVED UPI METHODS", text, discord.Color.blurple()))
    name=(action or '').lower(); qr_amount=None
    if value:
        try: qr_amount=float(value.replace(',',''))
        except ValueError: qr_amount=None
    row=db.execute("SELECT upi_id FROM saved_upi WHERE guild_id=? AND user_id=? AND name=?", (ctx.guild.id,ctx.author.id,name)).fetchone() if name else None
    if row:
        ticket=db.execute("SELECT input_amount,input_currency,inr_value FROM tickets WHERE channel_id=?", (ctx.channel.id,)).fetchone()
        if qr_amount is None and ticket and (ticket['input_currency'] or 'INR') == 'INR':
            qr_amount=float(ticket['input_amount'] or ticket['inr_value'] or 0)
        if qr_amount is not None and not 0 < qr_amount <= 10_000_000:
            return await ctx.send(embed=embed("❌ Invalid Amount", "Amount must be positive.", discord.Color.red()))
        buf=make_upi_qr(row['upi_id'], qr_amount)
        amount_text=f"₹{qr_amount:,.2f}" if qr_amount is not None else 'Not fixed'
        e=embed("💳 UPI PAYMENT QR", f"**UPI Name:** `{name}`\n**UPI ID:** `{row['upi_id']}`\n**Amount:** `{amount_text}`\n\nScan the QR to pay. The amount is linked to this ticket when available.", discord.Color.green())
        e.set_footer(text=f"{BRAND} • Payment details")
        return await ctx.send(embed=e, file=discord.File(buf, filename=f"upi_{name}.png"))
    if action and not value:
        try: qr_amount=float(action.replace(',',''))
        except ValueError: qr_amount=None
    ref=ctx.message.reference
    if not ref:
        return await ctx.send(embed=embed("❌ UPI NOT FOUND", f"Save one with `{PREFIX}upi save rishi rishi@oksbi`, then use `{PREFIX}upi rishi` in a ticket. Or reply to a UPI message and use `{PREFIX}upi`.", discord.Color.red()))
    try: msg=await ctx.channel.fetch_message(ref.message_id)
    except discord.HTTPException: return await ctx.send(embed=embed("❌ UPI NOT FOUND", "I couldn't read the replied message.", discord.Color.red()))
    upi_id=extract_upi(msg.content)
    if not upi_id: return await ctx.send(embed=embed("❌ UPI NOT FOUND", "No valid UPI ID was detected in the replied message.", discord.Color.red()))
    buf=make_upi_qr(upi_id, qr_amount)
    e=embed("💳 UPI PAYMENT QR", f"**UPI:** `{upi_id}`\n**Amount:** `{f'₹{qr_amount:,.2f}' if qr_amount is not None else 'Not fixed'}`")
    await ctx.send(embed=e, file=discord.File(buf, filename="upi_qr.png"))


async def send_panel(channel):
    cfg = getcfg(channel.guild.id)
    # Clean reference-inspired exchange panel. Only INR and C2C are shown.
    # Rates are configured with .setrate or the database/config system.
    lines = [
        f"**{BRAND}**",
        "",
        "**Exchange Rates**",
        "",
        f"{PANEL_E_I2C} **INR EXCHANGES**",
        f"> **INR → Crypto:** Normal Rate — `{fmt_rate(cfg.get('i2c_rate') or 100)} / $`",
        f"> **Crypto → INR:** Normal Rate — `{fmt_rate(cfg.get('c2i_rate') or 100)} / $`",
        "",
        f"{PANEL_E_C2C} **CRYPTO EXCHANGE**",
        f"> **Crypto → Crypto:** `{float(cfg.get('c2c_fee_percent') or 3):g}% Exchange Fee + Network Fees`",
        "",
        f"{PANEL_E_INFO} Select an option to proceed...",
    ]
    e = embed("Exchange Panel", "\n".join(lines), discord.Color.blurple())
    if bot.user:
        e.set_author(name=BRAND, icon_url=bot.user.display_avatar.url)
    if PANEL_FOOTER_TEXT:
        e.set_footer(text=PANEL_FOOTER_TEXT)
    if PANEL_IMAGE_URL:
        e.set_thumbnail(url=PANEL_IMAGE_URL)
    return await channel.send(embed=e, view=TicketPanel())


async def send_apply_panel(channel):
    cfg = getcfg(channel.guild.id)
    e = embed(f"📝 {BRAND.upper()} • EXCHANGER APPLICATION",
        "Want to join the exchange team? Submit an application privately.\n\n"
        "**What happens next**\n• Staff reviews your application\n• You may be asked for exchange experience / availability\n• Approval or rejection is handled by staff\n\n"
        f"**TOS:** {cfg['tos_text'][:600]}", discord.Color.blurple())
    return await channel.send(embed=e, view=ApplyPanel())

class ApplyPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Apply for Exchanger", emoji="📝", style=discord.ButtonStyle.success, custom_id="rishi:apply")
    async def apply(self, interaction, button):
        await interaction.response.send_modal(ApplicationModal())

class ApplicationModal(discord.ui.Modal, title="Exchanger Application"):
    experience = discord.ui.TextInput(label="Exchange experience", placeholder="Tell us what you have done before", required=True, max_length=700)
    availability = discord.ui.TextInput(label="Availability", placeholder="e.g. 4–6 hours/day", required=True, max_length=200)
    methods = discord.ui.TextInput(label="Payment / crypto experience", placeholder="UPI, USDT, BTC, networks, etc.", required=True, max_length=400)
    notes = discord.ui.TextInput(label="Why should we accept you?", style=discord.TextStyle.paragraph, required=True, max_length=700)
    async def on_submit(self, interaction):
        cfg=getcfg(interaction.guild.id); category=interaction.guild.get_channel(cfg.get('apply_category') or 0)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message(embed=embed("❌ APPLICATIONS NOT CONFIGURED","Ask an administrator to set the Apply category.",discord.Color.red()),ephemeral=True)
        existing=db.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND opener_id=? AND ticket_type='APPLY' AND status='open'",(interaction.guild.id,interaction.user.id)).fetchone()
        if existing and interaction.guild.get_channel(existing['channel_id']):
            return await interaction.response.send_message(embed=embed("📝 APPLICATION ALREADY OPEN",f"Your application is already open: <#{existing['channel_id']}>",discord.Color.orange()),ephemeral=True)
        overwrites={
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, read_message_history=True),
        }
        owner = interaction.guild.get_member(OWNER_ID) if OWNER_ID else None
        if owner and owner.id != interaction.user.id:
            overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
        ch=await interaction.guild.create_text_channel(f"apply-{interaction.user.name[:18]}",category=category,overwrites=overwrites,reason=f"{BRAND} application")
        details=f"**Experience:** {self.experience.value}\n**Availability:** {self.availability.value}\n**Methods:** {self.methods.value}\n**Why you:** {self.notes.value}"
        db.execute("INSERT INTO tickets(channel_id,guild_id,opener_id,ticket_type,amount,rate,status,created_at,notes,input_amount,input_currency,inr_value) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(ch.id,interaction.guild.id,interaction.user.id,'APPLY',0,0,'open',now_iso(),details[:2000],0,'',0));db.commit()
        await ch.send(embed=embed("📝 NEW EXCHANGER APPLICATION",f"**Applicant:** {interaction.user.mention}\n**ID:** `{interaction.user.id}`\n\n{details}\n\nStaff: review this application and use the ticket controls to close it.",discord.Color.blurple()),view=TicketActions())
        await interaction.response.send_message(embed=embed("✅ APPLICATION SUBMITTED",f"Your private application is ready: {ch.mention}",discord.Color.green()),ephemeral=True)
        await send_log(interaction.guild,"📝 Exchanger Application",f"Applicant `{interaction.user}` • {ch.mention}",discord.Color.blurple())

@bot.command(aliases=["exchange", "panel"])
async def ticketpanel(ctx):
    msg = await send_panel(ctx.channel)
    setcfg(ctx.guild.id, "panel_channel", ctx.channel.id); setcfg(ctx.guild.id, "panel_message", msg.id)
    await ctx.send(embed=embed("✅ Panel Posted", "Exchange panel posted and configuration saved."), delete_after=5)

@bot.command(aliases=["refreshpanel"])
async def updatepanel(ctx):
    if not owner_or_admin(ctx.author):
        return await ctx.send(embed=embed("❌ Access Denied", "Only the server owner or administrators can update the exchange panel.", discord.Color.red()), delete_after=6)
    cfg = getcfg(ctx.guild.id)
    channel_id = cfg.get("panel_channel")
    message_id = cfg.get("panel_message")
    channel = ctx.guild.get_channel(int(channel_id)) if channel_id else None
    if not channel or not message_id:
        return await ctx.send(embed=embed("❌ Panel Not Found", "Post a panel first using `.ticketpanel`.", discord.Color.red()), delete_after=6)
    try:
        message = await channel.fetch_message(int(message_id))
        new_embed = (await send_panel_preview(ctx.guild)).embeds[0] if False else None
    except Exception:
        message = None
    if message is None:
        new_msg = await send_panel(channel)
        setcfg(ctx.guild.id, "panel_channel", channel.id)
        setcfg(ctx.guild.id, "panel_message", new_msg.id)
        return await ctx.send(embed=embed("✅ Panel Recreated", "The old panel was missing, so a new panel was created.", discord.Color.green()), delete_after=6)
    cfg = getcfg(ctx.guild.id)
    lines = [
        f"**{BRAND}**", "", "**Exchange Rates**", "",
        f"{PANEL_E_I2C} **INR EXCHANGES**",
        f"> **INR → Crypto:** Normal Rate — `{fmt_rate(cfg.get('i2c_rate') or 100)} / $`",
        f"> **Crypto → INR:** Normal Rate — `{fmt_rate(cfg.get('c2i_rate') or 100)} / $`",
        "", f"{PANEL_E_C2C} **CRYPTO EXCHANGE**",
        f"> **Crypto → Crypto:** `{float(cfg.get('c2c_fee_percent') or 3):g}% Exchange Fee + Network Fees`",
        "", f"{PANEL_E_INFO} Select an option to proceed..."
    ]
    e = embed("Exchange Panel", "\n".join(lines), discord.Color.blurple())
    if bot.user: e.set_author(name=BRAND, icon_url=bot.user.display_avatar.url)
    if PANEL_FOOTER_TEXT: e.set_footer(text=PANEL_FOOTER_TEXT)
    if PANEL_IMAGE_URL: e.set_thumbnail(url=PANEL_IMAGE_URL)
    await message.edit(embed=e, view=TicketPanel())
    await ctx.send(embed=embed("✅ Panel Updated", "The existing exchange panel has been updated with the latest rates.", discord.Color.green()), delete_after=6)

@bot.command(aliases=["apply"])
async def applypanel(ctx):
    msg = await send_apply_panel(ctx.channel)
    await ctx.send(embed=embed("✅ Application Panel Posted", "The exchanger application panel is separate from exchange tickets."), delete_after=5)

@bot.command()
async def ticketinfo(ctx):
    row = db.execute("SELECT * FROM tickets WHERE channel_id=?", (ctx.channel.id,)).fetchone()
    if not row: return await ctx.send(embed=embed("❌ Not a Ticket", "This channel is not registered.", discord.Color.red()))
    if not ticket_manager(ctx.author, row["ticket_type"]):
        return await ctx.send(embed=embed("❌ ACCESS DENIED", "Exchange roles can manage only exchange tickets. Support/Report/Apply tickets are Admin/Owner only.", discord.Color.red()))
    claimed = ctx.guild.get_member(row["claimed_by"]).mention if row["claimed_by"] and ctx.guild.get_member(row["claimed_by"]) else "Unclaimed"
    inp=float(row["input_amount"] or row["amount"] or 0) if "input_amount" in row.keys() else float(row["amount"] or 0)
    cur=row["input_currency"] if "input_currency" in row.keys() else "USDT"
    await ctx.send(embed=embed("🎫 TICKET CONTROL CENTER", f"**Type:** `{row['ticket_type']}`\n**Input:** `{inp:,.2f} {cur}`\n**USDT:** `{float(row['amount'] or 0):,.4f}`\n**Rate:** `{fmt_rate(row['rate']) if row['rate'] else 'Manual quote'}`\n**INR Value:** `{fmt_rate(row['inr_value']) if 'inr_value' in row.keys() and row['inr_value'] else '—'}`\n**Status:** `{row['status']}`\n**Claimed by:** {claimed}\n**3P:** `{'Accepted' if row['p3_accepted'] else 'Not accepted'}`\n**3P declines:** `{row['p3_declined']}`"))

@bot.command()
async def claim(ctx):
    row=db.execute("SELECT ticket_type,amount,status,claimed_by FROM tickets WHERE channel_id=?",(ctx.channel.id,)).fetchone()
    if not row: return await ctx.send(embed=embed("❌ Not a Ticket","This channel is not a registered ticket.",discord.Color.red()))
    if not ticket_manager(ctx.author, row["ticket_type"]): return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can claim only I2C/C2I/C2C tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    if row["claimed_by"] and row["claimed_by"] != ctx.author.id: return await ctx.send(embed=embed("⚠️ Already Claimed","Another exchanger already claimed this ticket.",discord.Color.orange()))
    cfg=getcfg(ctx.guild.id); limit=float(cfg.get("staff_limit_usdt") or 0)
    active=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM tickets WHERE guild_id=? AND claimed_by=? AND status='open'",(ctx.guild.id,ctx.author.id)).fetchone()[0] or 0)
    if limit and active+float(row["amount"] or 0)>limit and not owner_or_admin(ctx.author): return await ctx.send(embed=embed("🚫 Claim Limit Reached",f"Limit `${limit:,.2f}` • Active `${active:,.4f}` • Ticket `${float(row['amount'] or 0):,.4f}`.\nUse `{PREFIX}setstafflimit <amount>` as admin/owner.",discord.Color.red()))
    db.execute("UPDATE tickets SET claimed_by=? WHERE channel_id=?",(ctx.author.id,ctx.channel.id)); db.commit(); active+=float(row["amount"] or 0)
    await ctx.send(embed=embed("🎟️ TICKET CLAIMED",f"✨ {ctx.author.mention} is handling this ticket.\n\n» **Exchanger's Limit:** `${limit:,.2f}`\n» **Claim Amount:** `${float(row['amount'] or 0):,.4f}`\n» **Active Total:** `${active:,.4f} / ${limit:,.2f}`" if limit else f"✨ {ctx.author.mention} is handling this ticket.\n\n» **Claim Amount:** `${float(row['amount'] or 0):,.4f}`\n» **Active Total:** `${active:,.4f}"))

@bot.command(name="x", aliases=["done"])
async def done(ctx):
    ok, ticket_row = can_manage_current_ticket(ctx)
    if not ok:
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can manage only exchange tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    row = db.execute("SELECT ticket_type,amount,rate,status,opener_id,claimed_by,input_amount,input_currency,inr_value FROM tickets WHERE channel_id=?", (ctx.channel.id,)).fetchone()
    if not row: return await ctx.send(embed=embed("❌ Not a Ticket", "This is not a registered exchange ticket.", discord.Color.red()))
    try:
        await finish_ticket(ctx, row)
    except discord.HTTPException as exc:
        # A Discord rate-limit or permission response must not silently kill .x.
        # The transaction is already committed before the notification steps.
        log.warning(f".x Discord request failed: status={getattr(exc, 'status', 'unknown')} retry_after={getattr(exc, 'retry_after', 'unknown')}")
        await ctx.send(embed=embed("⚠️ DISCORD RATE LIMIT", "The completion was processed, but Discord temporarily rate-limited a channel update. Please wait a moment and check the logs/showcase.", discord.Color.orange()), delete_after=10)

@bot.command()
async def close(ctx):
    ok, ticket_row = can_manage_current_ticket(ctx)
    if not ok:
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can manage only exchange tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    await close_ticket(ctx)

@bot.command()
async def reopen(ctx):
    ok, ticket_row = can_manage_current_ticket(ctx)
    if not ok:
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can manage only exchange tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    row = db.execute("SELECT opener_id,status FROM tickets WHERE channel_id=?", (ctx.channel.id,)).fetchone()
    if not row: return await ctx.send(embed=embed("❌ Not a Ticket", "", discord.Color.red()))
    if row["status"] == "done":
        return await ctx.send(embed=embed("❌ Already Completed", "Completed deals are final and cannot be reopened (it would be counted twice).", discord.Color.red()))
    user = ctx.guild.get_member(row["opener_id"])
    if user: await ctx.channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
    db.execute("UPDATE tickets SET status='open', created_at=? WHERE channel_id=?", (now_iso(), ctx.channel.id)); db.commit()
    await ctx.channel.edit(name=re.sub(r'^((done|closed|expired)-)+', '', ctx.channel.name)[:95])
    await ctx.send(embed=embed("🔓 Ticket Reopened", "Ticket is open again.", discord.Color.green()))

@bot.command()
async def add(ctx, member: discord.Member):
    ok, ticket_row = can_manage_current_ticket(ctx)
    if not ok:
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can manage only exchange tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
    await ctx.send(embed=embed("➕ User Added", f"`{member}` added to this ticket.", discord.Color.green()))

@bot.command()
async def remove(ctx, member: discord.Member):
    ok, ticket_row = can_manage_current_ticket(ctx)
    if not ok:
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can manage only exchange tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    await ctx.channel.set_permissions(member, view_channel=False)
    await ctx.send(embed=embed("➖ User Removed", f"`{member}` removed from this ticket.", discord.Color.orange()))

@bot.command()
async def rename(ctx, *, name):
    ok, ticket_row = can_manage_current_ticket(ctx)
    if not ok:
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can manage only exchange tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", name.lower())[:90]
    await ctx.channel.edit(name=name); await ctx.send(embed=embed("✏️ Ticket Renamed", f"`{name}`", discord.Color.green()))

@bot.command()
async def ticketlock(ctx):
    ok, ticket_row = can_manage_current_ticket(ctx)
    if not ok:
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can manage only exchange tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    row = db.execute("SELECT opener_id FROM tickets WHERE channel_id=?", (ctx.channel.id,)).fetchone()
    if row:
        user = ctx.guild.get_member(row["opener_id"])
        if user: await ctx.channel.set_permissions(user, send_messages=False)
    await ctx.send(embed=embed("🔒 Ticket Locked", "Client messaging is temporarily disabled."))

@bot.command()
async def ticketunlock(ctx):
    ok, ticket_row = can_manage_current_ticket(ctx)
    if not ok:
        return await ctx.send(embed=embed("❌ Access Denied", "Exchange roles can manage only exchange tickets. Support/Report/Apply are Admin/Owner only.", discord.Color.red()))
    row = db.execute("SELECT opener_id FROM tickets WHERE channel_id=?", (ctx.channel.id,)).fetchone()
    if row:
        user = ctx.guild.get_member(row["opener_id"])
        if user: await ctx.channel.set_permissions(user, send_messages=True)
    await ctx.send(embed=embed("🔓 Ticket Unlocked", "Client messaging is enabled again."))


# ----------------------------- Leaderboard -----------------------------
def leaderboard_period():
    now = discord.utils.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12: end = start.replace(year=start.year+1, month=1)
    else: end = start.replace(month=start.month+1)
    return start, end

def prize_list(cfg):
    return [cfg.get(f"{n}_prize") or ("$40" if n=="first" else "$25" if n=="second" else "$15" if n=="third" else "$10") for n in ["first","second","third","fourth","fifth","sixth","seventh","eighth","ninth","tenth"]]

async def build_leaderboard_embed(guild):
    start,end=leaderboard_period(); cfg=getcfg(guild.id)
    rows=db.execute("SELECT claimed_by,COUNT(*) deals,COALESCE(SUM(amount),0) volume FROM exchanges WHERE guild_id=? AND status='completed' AND claimed_by IS NOT NULL AND created_at>=? AND created_at<? GROUP BY claimed_by ORDER BY volume DESC,deals DESC LIMIT 10",(guild.id,start.isoformat(),end.isoformat())).fetchall()
    prizes=prize_list(cfg); lines=[]
    medals=["🥇","🥈","🥉","🏅","🏅","🏅","🏅","🏅","🏅","🏅"]
    for i,r in enumerate(rows):
        m=guild.get_member(int(r['claimed_by'])); name=m.mention if m else f"<@{r['claimed_by']}>"; prize=prizes[i] if i<len(prizes) else "—"
        lines.append(f"{medals[i]} **#{i+1}** {name} — **${float(r['volume']):,.2f}** exchanged • `{int(r['deals'])}` deals • 🎁 `{prize}`")
    if not lines: lines=["No completed exchanger deals yet this month."]
    e=embed("🏆 MONTHLY EXCHANGER LEADERBOARD",f"**Period:** {discord.utils.format_dt(start,'D')} → {discord.utils.format_dt(end,'D')}\n**Ranking:** completed USDT-equivalent volume\n\n"+"\n".join(lines),discord.Color.gold())
    e.add_field(name="🎁 Prize Configuration",value="\n".join(f"**#{i+1}:** `{prizes[i]}`" for i in range(min(10,len(prizes)))),inline=False)
    e.set_footer(text=f"{BRAND} • Rewards are configured by staff; no automatic payment is made by the bot")
    return e

async def refresh_leaderboard(guild):
    cfg=getcfg(guild.id); cid=cfg.get("leaderboard_channel"); mid=cfg.get("leaderboard_message")
    if not cid: return
    ch=guild.get_channel(cid)
    if not ch: return
    e=await build_leaderboard_embed(guild)
    if mid:
        try:
            msg=await ch.fetch_message(mid); await msg.edit(embed=e); return
        except (discord.NotFound, discord.HTTPException): pass
    try:
        msg=await ch.send(embed=e); setcfg(guild.id,"leaderboard_message",msg.id)
    except discord.HTTPException: pass

@bot.command(aliases=["lb"])
async def leaderboard(ctx):
    e=await build_leaderboard_embed(ctx.guild); await ctx.send(embed=e)
    cfg=getcfg(ctx.guild.id); cid=cfg.get('leaderboard_channel')
    if cid and ctx.channel.id != cid and owner_or_admin(ctx.author):
        try: await ctx.guild.get_channel(cid).send(embed=e)
        except Exception: pass

@bot.command(aliases=["setlbchannel"])
async def setleaderboardchannel(ctx, channel: discord.TextChannel):
    setcfg(ctx.guild.id,'leaderboard_channel',channel.id); setcfg(ctx.guild.id,'leaderboard_message',None); await refresh_leaderboard(ctx.guild); await ctx.send(embed=embed("🏆 Leaderboard Channel Set",f"Monthly leaderboard will be showcased in {channel.mention} and refreshed automatically after completed deals.",discord.Color.green()))

@bot.command(aliases=["setprize"])
async def setleaderboardprize(ctx, place: int, *, reward: str):
    if not 1 <= place <= 10: return await ctx.send(embed=embed("❌ Invalid Place","Choose a place from 1 to 10.",discord.Color.red()))
    fields=['first_prize','second_prize','third_prize','fourth_prize','fifth_prize','sixth_prize','seventh_prize','eighth_prize','ninth_prize','tenth_prize']
    setcfg(ctx.guild.id,fields[place-1],reward[:80]); await ctx.send(embed=embed("🎁 Prize Updated",f"**#{place}:** `{reward[:80]}`",discord.Color.green()))

for _place, _name in enumerate(("first","second","third","fourth","fifth","sixth","seventh","eighth","ninth","tenth"), 1):
    async def _set_prize(ctx, reward, _p=_place):
        fields=['first_prize','second_prize','third_prize','fourth_prize','fifth_prize','sixth_prize','seventh_prize','eighth_prize','ninth_prize','tenth_prize']
        setcfg(ctx.guild.id,fields[_p-1],str(reward)[:80]); await ctx.send(embed=embed("🎁 Prize Updated",f"**#{_p}:** `{str(reward)[:80]}`",discord.Color.green()))
    bot.command(name=f"set{_name}prize")(_set_prize)

@bot.command()
async def prizes(ctx):
    cfg=getcfg(ctx.guild.id); ps=prize_list(cfg); await ctx.send(embed=embed("🎁 LEADERBOARD PRIZES","\n".join(f"**#{i+1}:** `{p}`" for i,p in enumerate(ps))))

@bot.command()
async def rebuildstats(ctx):
    db.execute("DELETE FROM user_stats WHERE guild_id=?",(ctx.guild.id,)); rows=db.execute("SELECT claimed_by,COUNT(*) c,COALESCE(SUM(amount),0) v FROM exchanges WHERE guild_id=? AND status='completed' AND claimed_by IS NOT NULL GROUP BY claimed_by",(ctx.guild.id,)).fetchall()
    for r in rows: db.execute("INSERT INTO user_stats(guild_id,user_id,completed_count,completed_volume) VALUES(?,?,?,?)",(ctx.guild.id,r['claimed_by'],r['c'],r['v']))
    db.commit(); await ctx.send(embed=embed("♻️ EXCHANGER STATS REBUILT",f"Rebuilt `{len(rows)}` exchanger profiles from completed transactions.",discord.Color.green()))

@bot.command()
async def exprofile(ctx, member: discord.Member = None):
    member=member or ctx.author; row=db.execute("SELECT completed_count,completed_volume FROM user_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id)).fetchone(); c,v=(int(row['completed_count']),float(row['completed_volume'])) if row else (0,0)
    await ctx.send(embed=embed("👤 EXCHANGER PROFILE",f"**Exchanger:** {member.mention}\n**Completed deals:** `{c}`\n**Total volume:** `${v:,.2f}`\n**Current month rank:** see `{PREFIX}lb`",discord.Color.blurple()))

# ----------------------------- Private profiles / stats -----------------------------
@bot.command()
async def p(ctx, member: discord.Member = None):
    # Client profiles are public and are displayed in the same channel where the command is used.
    # Completed exchange data is read directly from the exchanges table, so .x updates it automatically.
    member = member or ctx.author
    rows = db.execute(
        "SELECT ticket_type, amount, rate, inr_value, input_amount, input_currency, created_at "
        "FROM exchanges WHERE guild_id=? AND opener_id=? AND status='completed' "
        "ORDER BY id DESC",
        (ctx.guild.id, member.id)
    ).fetchall()
    open_count = db.execute(
        "SELECT COUNT(*) FROM tickets WHERE guild_id=? AND opener_id=? AND status='open'",
        (ctx.guild.id, member.id)
    ).fetchone()[0]
    count = len(rows)
    i2c_inr = sum(float(r["input_amount"] or 0) for r in rows if r["ticket_type"] == "I2C")
    c2i_inr = sum(float(r["inr_value"] or 0) for r in rows if r["ticket_type"] == "C2I")
    c2c_usdt = sum(float(r["amount"] or 0) for r in rows if r["ticket_type"] == "C2C")
    latest = rows[0] if rows else None
    latest_text = "No completed exchanges yet."
    if latest:
        flow = latest["ticket_type"]
        if flow == "I2C":
            latest_text = f"`₹{float(latest['input_amount'] or 0):,.2f} INR → {float(latest['amount'] or 0):,.4f} USDT`"
        elif flow == "C2I":
            latest_text = f"`{float(latest['amount'] or 0):,.4f} USDT → ₹{float(latest['inr_value'] or 0):,.2f}`"
        else:
            latest_text = f"`{float(latest['amount'] or 0):,.4f} USDT`"
    description = (
        f"**Client:** {member.mention}\n"
        f"**Completed Deals:** `{count}`\n"
        f"**I2C Volume:** `₹{i2c_inr:,.2f}`\n"
        f"**C2I Volume:** `₹{c2i_inr:,.2f}`\n"
        f"**C2C Volume:** `{c2c_usdt:,.4f} USDT`\n"
        f"**Open Tickets:** `{open_count}`\n\n"
        f"**Latest Completed Exchange:**\n{latest_text}\n\n"
        "Your completed exchange amount is updated automatically after staff uses `.x`."
    )
    await ctx.send(embed=embed("👤 CLIENT EXCHANGE PROFILE", description, discord.Color.blurple()))

@bot.command()
async def profile(ctx, member: discord.Member = None): await p(ctx, member)

@bot.command(aliases=["statistics"])
async def stats(ctx):
    total, count = db.execute("SELECT COALESCE(SUM(amount),0),COUNT(*) FROM exchanges WHERE guild_id=? AND status='completed'", (ctx.guild.id,)).fetchone()
    c = getcfg(ctx.guild.id)
    await ctx.send(embed=embed("📊 EXCHANGE STATS", f"**Completed:** `{count}`\n**Total Volume:** `${float(total or 0):,.2f} USD`\n**I2C:** `{fmt_rate(c['i2c_rate'])}`\n**C2I:** `{fmt_rate(c['c2i_rate'])}`\n**C2C:** `{fmt_rate(c['c2c_rate'])}`", discord.Color.green()))

@bot.command()
async def total(ctx):
    total, count = db.execute("SELECT COALESCE(SUM(amount),0),COUNT(*) FROM exchanges WHERE guild_id=? AND status='completed'", (ctx.guild.id,)).fetchone()
    await ctx.send(embed=embed("💰 TOTAL EXCHANGE", f"`${float(total or 0):,.2f} USD` across `{int(count or 0)}` completed transactions."))

@bot.command(aliases=["logs", "transactions", "audit"])
async def trans(ctx, limit: int = 10):
    if not staff(ctx.author): return await ctx.send(embed=embed("❌ Access Denied", "Staff only.", discord.Color.red()))
    limit = max(1, min(limit, 25))
    rows = db.execute("SELECT id,ticket_type,amount,rate,inr_value,status,created_at FROM exchanges WHERE guild_id=? ORDER BY id DESC LIMIT ?", (ctx.guild.id, limit)).fetchall()
    lines = "\n".join(
        f"`#{r['id']}` • `{r['ticket_type']}` • `{r['amount']:,.2f} USDT` • "
        f"`{fmt_rate(r['rate']) if r['rate'] else 'Manual'}` • `{fmt_rate(r['inr_value']) if r['inr_value'] else '—'}` • `{r['status']}`"
        for r in rows
    ) or "No transactions yet."
    await ctx.send(embed=embed("📋 ANONYMOUS TRANSACTIONS", lines, discord.Color.blurple()))

@bot.command()
async def userstats(ctx, member: discord.Member = None):
    if not owner_or_admin(ctx.author): return await ctx.send(embed=embed("❌ Access Denied", "Admin/Owner only.", discord.Color.red()))
    member = member or ctx.author
    row = db.execute("SELECT completed_count,completed_volume FROM user_stats WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id)).fetchone()
    count, volume = (row["completed_count"], row["completed_volume"]) if row else (0, 0)
    await ctx.send(embed=embed("📈 PRIVATE USER STATS", f"`{member}` • Completed `{count}` • Volume `{float(volume):,.2f} USDT`"))


# ----------------------------- Security configuration -----------------------------
@bot.command(aliases=["security"])
async def securitycheck(ctx, member: discord.Member = None):
    member=member or ctx.author
    if member.id != ctx.author.id and not staff(ctx.author): return await ctx.send(embed=embed("❌ Access Denied","You can only check yourself unless you are staff.",discord.Color.red()))
    e,score,status=security_embed(member); await ctx.send(embed=e)

@bot.command(aliases=["set3p"])
async def set3prequired(ctx, mode: str):
    mode=mode.lower()
    if mode not in ('on','off','required','optional'): return await ctx.send(embed=embed("❌ Invalid Mode","Use `on`, `off`, `required`, or `optional`.",discord.Color.red()))
    setcfg(ctx.guild.id,'require_3p',1 if mode in ('on','required') else 0)
    await ctx.send(embed=embed("🤝 3P Completion Gate Updated",f"3P is now **{'REQUIRED' if mode in ('on','required') else 'OPTIONAL'}** before completion.",discord.Color.green()))

@bot.command()
async def setsecurity(ctx, mode: str):
    mode=mode.lower()
    if mode not in ('on','off','required','optional'): return await ctx.send(embed=embed("❌ Invalid Mode","Use `on`, `off`, `required`, or `optional`.",discord.Color.red()))
    setcfg(ctx.guild.id,'security_enabled',0 if mode=='off' else 1); setcfg(ctx.guild.id,'require_security_check',1 if mode=='required' else 0)
    await ctx.send(embed=embed("🛡️ Security Mode Updated",f"Security: `{'OFF' if mode=='off' else 'ON'}` • Ticket gate: `{'REQUIRED' if mode=='required' else 'OPTIONAL'}`",discord.Color.green()))

@bot.command()
async def setaccountage(ctx, days: int):
    if not 0 <= days <= 3650: return await ctx.send(embed=embed("❌ Invalid Age","Use 0–3650 days.",discord.Color.red()))
    setcfg(ctx.guild.id,'min_account_age_days',days); await ctx.send(embed=embed("🛡️ Account Age Threshold Updated",f"Minimum account age: `{days} day(s)`",discord.Color.green()))

@bot.command()
async def setjoinage(ctx, minutes: int):
    if not 0 <= minutes <= 43200: return await ctx.send(embed=embed("❌ Invalid Join Age","Use 0–43200 minutes.",discord.Color.red()))
    setcfg(ctx.guild.id,'min_server_age_minutes',minutes); await ctx.send(embed=embed("🛡️ Server Join Threshold Updated",f"Minimum server membership age: `{minutes} minute(s)`",discord.Color.green()))

# ----------------------------- Configuration -----------------------------
async def config_reply(ctx):
    c = getcfg(ctx.guild.id)
    def mention_channel(cid): return f"<#{cid}>" if cid else "Not set"
    def mention_cat(cid): return f"`{cid}`" if cid else "Not set"
    await ctx.send(embed=embed(f"⚙️ {BRAND} • CONFIG", 
        f"**Fallback category:** {mention_cat(c['ticket_category'])}\n"
        f"**I2C category:** {mention_cat(c['i2c_category'])}\n**C2I category:** {mention_cat(c['c2i_category'])}\n"
        f"**C2C category:** {mention_cat(c['c2c_category'])}\n**Apply category:** {mention_cat(c['apply_category'])}\n"
        f"**Archive category:** {mention_cat(c['archive_category'])}\n**Legacy staff role:** `{c['staff_role'] or 'Not set'}`\n"
        f"**Log:** {mention_channel(c['log_channel'])}\n**Showcase:** {mention_channel(c['showcase_channel'])}\n"
        f"**Done:** {mention_channel(c['done_channel'])}\n**Total counter:** {mention_channel(c['total_channel'])}\n"
        f"**Panel:** {mention_channel(c['panel_channel'])}\n**Leaderboard:** {mention_channel(c.get('leaderboard_channel'))}\n**Public showcase:** `{'ON' if c.get('public_showcase_enabled',1) else 'OFF'}`\n**Security:** `{'ON' if c.get('security_enabled',1) else 'OFF'}` • Gate `{'REQUIRED' if c.get('require_security_check') else 'OPTIONAL'}`\n**3P completion gate:** `{'REQUIRED' if c.get('require_3p') else 'OPTIONAL'}`\n\n"
        f"**I2C:** `{fmt_rate(c['i2c_rate'])}` • **C2I:** `{fmt_rate(c['c2i_rate'])}` • **C2C:** `{fmt_rate(c['c2c_rate'])}`\n"
        f"**3P surcharge:** `+₹{float(c['decline_penalty'] or 0):.2f}`\n"
        f"**Demo showcase:** `{'ON' if c.get('demo_enabled') else 'OFF'}` • `{int(c.get('demo_interval_minutes') or 15)} min` • `{c.get('demo_min_amount') or 10:,.0f}–{c.get('demo_max_amount') or 10000:,.0f} INR`\n"
        f"**Demo channel:** {mention_channel(c.get('demo_channel'))}\n**Staff claim limit:** `${float(c.get('staff_limit_usdt') or 0):,.2f} USDT`\n**Unclaimed timeout:** `{int(c.get('unclaimed_timeout_minutes') or 120)} min`\n"
        f"**TOS:** `{c['tos_text'][:300]}`"))

@bot.command()
async def config(ctx): await config_reply(ctx)

@bot.command()
async def setrate(ctx, flow: str, tier_or_rate: str, rate_value: float = None):
    flow = flow.upper()
    if tier_or_rate.upper() in {"BELOW", "UNDER", "SMALL", "ABOVE", "OVER", "LARGE"}:
        return await ctx.send(embed=embed("ℹ️ Tiered Rates Retired", f"Tickets use a single rate per flow, so below/above tiers had no effect.\n\nUse `{PREFIX}setrate I2C 105` or `{PREFIX}setrate C2I 100`.", discord.Color.orange()))
    # Backward-compatible single-rate commands for C2I and legacy I2C.
    try:
        rate = float(tier_or_rate) if rate_value is None else float(rate_value)
    except ValueError:
        return await ctx.send(embed=embed("❌ Invalid Rate", f"Use `{PREFIX}setrate I2C 105` or `{PREFIX}setrate C2I 100`.", discord.Color.red()))
    if flow not in ("I2C", "C2I") or rate <= 0 or rate > 100000:
        return await ctx.send(embed=embed("❌ Invalid Rate", f"Use `{PREFIX}setrate I2C 105` or `{PREFIX}setrate C2I 100`.", discord.Color.red()))
    setcfg(ctx.guild.id, {"I2C":"i2c_rate", "C2I":"c2i_rate"}[flow], rate)
    await ctx.send(embed=embed("💹 Rate Updated", f"**{flow}:** `{fmt_rate(rate)} / USDT`", discord.Color.green()))


@bot.command()
async def setc2cfee(ctx, percent: float):
    if not 0 <= percent <= 100:
        return await ctx.send(embed=embed("❌ Invalid Fee", "Fee must be between 0 and 100 percent.", discord.Color.red()))
    setcfg(ctx.guild.id, "c2c_fee_percent", percent)
    await ctx.send(embed=embed("💹 C2C Fee Updated", f"Crypto → Crypto fee: `{fmt_rate(percent)}% + Network Fees`", discord.Color.green()))

@bot.command()
async def setpenalty(ctx, amount: float):
    if not 0 <= amount <= 1000: return await ctx.send(embed=embed("❌ Invalid", "Penalty must be 0–1000.", discord.Color.red()))
    setcfg(ctx.guild.id, "decline_penalty", amount)
    await ctx.send(embed=embed("⚙️ 3P Penalty Updated", f"Non-agreement surcharge: `+₹{amount:.2f}/USDT`", discord.Color.green()))

@bot.command()
async def settos(ctx, *, text):
    setcfg(ctx.guild.id, "tos_text", text[:1500]); await ctx.send(embed=embed("⚙️ TOS Updated", "New TOS will appear in panels and 3P."))

async def set_category_cmd(ctx, field, category, label):
    setcfg(ctx.guild.id, field, category.id); await ctx.send(embed=embed("⚙️ Category Updated", f"**{label}:** {category.mention}", discord.Color.green()))

@bot.command()
async def setcategory(ctx, category: discord.CategoryChannel): await set_category_cmd(ctx,"ticket_category",category,"Fallback")
@bot.command()
async def seti2ccategory(ctx, category: discord.CategoryChannel): await set_category_cmd(ctx,"i2c_category",category,"I2C")
@bot.command()
async def setc2icategory(ctx, category: discord.CategoryChannel): await set_category_cmd(ctx,"c2i_category",category,"C2I")
@bot.command()
async def setc2ccategory(ctx, category: discord.CategoryChannel): await set_category_cmd(ctx,"c2c_category",category,"C2C")
@bot.command()
async def setapplycategory(ctx, category: discord.CategoryChannel): await set_category_cmd(ctx,"apply_category",category,"Apply")
@bot.command()
async def setarchivecategory(ctx, category: discord.CategoryChannel): await set_category_cmd(ctx,"archive_category",category,"Archive")

@bot.command()
async def addexrole(ctx, role: discord.Role):
    if not owner_or_admin(ctx.author):
        return await ctx.send(embed=embed("❌ ACCESS DENIED", "Administrator/Owner only.", discord.Color.red()))
    db.execute("INSERT OR IGNORE INTO exchange_roles(guild_id,role_id) VALUES(?,?)", (ctx.guild.id, role.id)); db.commit()
    await ctx.send(embed=embed("👮 EXCHANGE ROLE ADDED", f"{role.mention} can now access and claim **I2C / C2I / C2C** tickets.\n\nThis does **not** grant the role any server permissions." , discord.Color.green()))

@bot.command()
async def remexrole(ctx, role: discord.Role):
    if not owner_or_admin(ctx.author):
        return await ctx.send(embed=embed("❌ ACCESS DENIED", "Administrator/Owner only.", discord.Color.red()))
    cur=db.execute("DELETE FROM exchange_roles WHERE guild_id=? AND role_id=?", (ctx.guild.id, role.id)); db.commit()
    await ctx.send(embed=embed("👮 EXCHANGE ROLE REMOVED", f"{role.mention} was removed from exchange ticket access." if cur.rowcount else f"{role.mention} was not configured.", discord.Color.orange()))

@bot.command()
async def exroles(ctx):
    if not owner_or_admin(ctx.author):
        return await ctx.send(embed=embed("❌ ACCESS DENIED", "Administrator/Owner only.", discord.Color.red()))
    roles=configured_exchange_roles(ctx.guild)
    text="\n".join(f"• {r.mention} (`{r.id}`)" for r in roles) or "No exchange roles configured."
    await ctx.send(embed=embed("👮 EXCHANGE ROLES", text+"\n\nThese roles can access/claim exchange tickets only and receive no server permissions from the bot."))

@bot.command()
async def setstaff(ctx, role: discord.Role):
    if not owner_or_admin(ctx.author):
        return await ctx.send(embed=embed("❌ ACCESS DENIED", "Administrator/Owner only.", discord.Color.red()))
    setcfg(ctx.guild.id, "staff_role", role.id)
    db.execute("INSERT OR IGNORE INTO exchange_roles(guild_id,role_id) VALUES(?,?)", (ctx.guild.id, role.id)); db.commit()
    await ctx.send(embed=embed("👮 EXCHANGE ROLE UPDATED", f"{role.mention} is now an exchange role for I2C/C2I/C2C tickets.\n\nNo server permissions are granted by the bot."))

@bot.command()
async def setstafflimit(ctx, amount: float):
    if amount < 0 or amount > 10_000_000:
        return await ctx.send(embed=embed("❌ Invalid Staff Limit", f"Use `{PREFIX}setstafflimit 40` or `0` for no limit.", discord.Color.red()))
    setcfg(ctx.guild.id,"staff_limit_usdt",amount)
    await ctx.send(embed=embed("👮 Staff Claim Limit Updated", f"New per-staff active claim limit: `${amount:,.2f} USDT`" if amount else "Staff claim limit disabled.",discord.Color.green()))

@bot.command()
async def setunclaimedtimeout(ctx, minutes: int):
    if not 15 <= minutes <= 1440:
        return await ctx.send(embed=embed("❌ Invalid Timeout", f"Use `{PREFIX}setunclaimedtimeout 120` (15–1440 minutes).", discord.Color.red()))
    setcfg(ctx.guild.id,"unclaimed_timeout_minutes",minutes)
    await ctx.send(embed=embed("⏳ Unclaimed Timeout Updated", f"Unclaimed tickets will close after `{minutes}` minutes.",discord.Color.green()))

@bot.command()
async def setlog(ctx, channel: discord.TextChannel):
    setcfg(ctx.guild.id, "log_channel", channel.id)
    await ctx.send(embed=embed("📋 Log Channel Updated", channel.mention))

@bot.command()
async def setshowcase(ctx, channel: discord.TextChannel):
    setcfg(ctx.guild.id, "showcase_channel", channel.id)
    await ctx.send(embed=embed("📊 Showcase Channel Updated", channel.mention))

@bot.command()
async def setdonechannel(ctx, channel: discord.TextChannel):
    setcfg(ctx.guild.id, "done_channel", channel.id)
    await ctx.send(embed=embed("✅ Done Channel Updated", channel.mention))

@bot.command()
async def settotalchannel(ctx, channel: discord.TextChannel):
    setcfg(ctx.guild.id, "total_channel", channel.id)
    await update_total_counter(ctx.guild)
    await ctx.send(embed=embed("💰 Total Counter Updated", channel.mention))

@bot.command()
async def setpanelchannel(ctx, channel: discord.TextChannel):
    setcfg(ctx.guild.id, "panel_channel", channel.id)
    await ctx.send(embed=embed("🎫 Panel Channel Updated", channel.mention))

@bot.command()
async def setdemo(ctx, mode: str, channel: discord.TextChannel = None):
    """Enable/disable clearly labeled demo showcase posts."""
    mode = mode.lower()
    if mode not in ("on", "off"):
        return await ctx.send(embed=embed("❌ Invalid Mode", f"Use `{PREFIX}setdemo on #showcase` or `{PREFIX}setdemo off`.", discord.Color.red()))
    if mode == "on":
        target = channel or ctx.guild.get_channel(getcfg(ctx.guild.id).get("demo_channel") or 0) or ctx.channel
        setcfg(ctx.guild.id, "demo_enabled", 1)
        setcfg(ctx.guild.id, "demo_channel", target.id)
        await ctx.send(embed=embed(
            "🧪 Demo Showcase Enabled",
            f"Sample posts will appear in {target.mention}.\n"
            "They are permanently labeled **DEMO ONLY** and never enter real totals/history.",
            discord.Color.orange()
        ))
    else:
        setcfg(ctx.guild.id, "demo_enabled", 0)
        await ctx.send(embed=embed("🧪 Demo Showcase Disabled", "No automated sample transactions will be posted.", discord.Color.green()))


@bot.command()
async def setdemointerval(ctx, minutes: int):
    if not 15 <= minutes <= 1440:
        return await ctx.send(embed=embed("❌ Invalid Interval", "Use 15–1440 minutes.", discord.Color.red()))
    setcfg(ctx.guild.id, "demo_interval_minutes", minutes)
    await ctx.send(embed=embed("⏱️ Demo Interval Updated", f"Sample interval: `{minutes} minutes`.", discord.Color.green()))


@bot.command()
async def setdemorange(ctx, minimum: int, maximum: int):
    if minimum < 1 or maximum > 10000 or minimum > maximum:
        return await ctx.send(embed=embed("❌ Invalid Range", "Range must be between ₹1 and ₹10,000.", discord.Color.red()))
    setcfg(ctx.guild.id, "demo_min_amount", minimum)
    setcfg(ctx.guild.id, "demo_max_amount", maximum)
    await ctx.send(embed=embed("🎲 Demo Range Updated", f"Random sample amount: `₹{minimum:,}`–`₹{maximum:,}` (rounded to ₹10).", discord.Color.green()))


@bot.command(aliases=["diag","checkconfig"])
async def diagnostics(ctx):
    cfg=getcfg(ctx.guild.id); checks=[]
    checks.append(("Bot permissions", bool(ctx.guild.me and ctx.guild.me.guild_permissions.manage_channels and ctx.guild.me.guild_permissions.manage_roles)))
    for key,label in [('i2c_category','I2C category'),('c2i_category','C2I category'),('c2c_category','C2C category'),('apply_category','Apply category'),('staff_role','Staff role'),('showcase_channel','Public showcase'),('done_channel','Private audit logs'),('leaderboard_channel','Leaderboard channel')]:
        if key in ('showcase_channel','done_channel','leaderboard_channel'): ok=bool(cfg.get(key) and ctx.guild.get_channel(cfg.get(key)))
        elif key=='staff_role': ok=bool(cfg.get(key) and ctx.guild.get_role(cfg.get(key)))
        else: ok=bool(cfg.get(key) and isinstance(ctx.guild.get_channel(cfg.get(key)),discord.CategoryChannel))
        checks.append((label,ok))
    lines='\n'.join(('🟢' if ok else '🔴')+f' **{label}**' for label,ok in checks)
    e=embed("🧪 BOT DIAGNOSTICS",lines+"\n\n🟢 = ready • 🔴 = needs configuration/permission",discord.Color.green() if all(x[1] for x in checks) else discord.Color.orange())
    await ctx.send(embed=e)

@bot.command()
async def setup(ctx):
    await ctx.send(embed=embed(f"🛠️ {BRAND} • SETUP GUIDE", 
        f"1. `{PREFIX}setstaff @Staff`\n2. Set categories: `{PREFIX}seti2ccategory`, `{PREFIX}setc2icategory`, `{PREFIX}setc2ccategory`, `{PREFIX}setapplycategory`\n"
        f"3. `{PREFIX}setlog #logs`\n4. `{PREFIX}setdonechannel #completed`\n5. `{PREFIX}settotalchannel #total-counter`\n6. `{PREFIX}setrate I2C 105`\n7. `{PREFIX}setrate C2I 100`\n8. `{PREFIX}setpenalty 1`\n9. `{PREFIX}settos ...`\n10. `{PREFIX}ticketpanel`\n11. `{PREFIX}setstafflimit 40`\n12. `{PREFIX}setunclaimedtimeout 120`\n13. `{PREFIX}setdemo on #showcase` (optional, clearly labeled demo samples)\n14. `{PREFIX}setdemointerval 15`\n15. `{PREFIX}setdemorange 1 10000`"))

@bot.command(aliases=["resetconfig"])
async def configreset(ctx):
    if ctx.author.id != OWNER_ID:
        return await ctx.send(embed=embed("❌ Access Denied", "Owner only.", discord.Color.red()))
    db.execute("DELETE FROM config WHERE guild_id=?", (ctx.guild.id,)); db.commit(); getcfg(ctx.guild.id)
    await ctx.send(embed=embed("♻️ Config Reset", "Defaults restored. Database history was not deleted.", discord.Color.orange()))

@bot.command()
async def reloadconfig(ctx):
    if ctx.author.id != OWNER_ID:
        return await ctx.send(embed=embed("❌ Access Denied", "Owner only.", discord.Color.red()))
    getcfg(ctx.guild.id); await ctx.send(embed=embed("🔄 Config Reloaded", "Current database configuration loaded."))


# ----------------------------- Moderation / server tools -----------------------------
@bot.command()
async def blacklist(ctx, member: discord.Member, *, reason="No reason provided"):
    if not staff(ctx.author):
        return await ctx.send(embed=embed("❌ ACCESS DENIED", "Staff/exchanger only.", discord.Color.red()))
    if member.id == ctx.author.id:
        return await ctx.send(embed=embed("❌ INVALID", "You cannot blacklist yourself.", discord.Color.red()))
    db.execute("INSERT INTO blacklists(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET moderator_id=excluded.moderator_id,reason=excluded.reason,created_at=excluded.created_at", (ctx.guild.id, member.id, ctx.author.id, reason[:500], now_iso()))
    db.commit()
    await ctx.send(embed=embed("🚫 USER BLACKLISTED", f"**User:** {member.mention}\n**Reason:** {reason[:500]}", discord.Color.red()))

@bot.command()
async def blacklistcheck(ctx, member: discord.Member):
    if not staff(ctx.author):
        return await ctx.send(embed=embed("❌ ACCESS DENIED", "Staff/exchanger only.", discord.Color.red()))
    row=db.execute("SELECT reason,moderator_id,created_at FROM blacklists WHERE guild_id=? AND user_id=?", (ctx.guild.id,member.id)).fetchone()
    if not row:
        return await ctx.send(embed=embed("✅ NOT BLACKLISTED", f"{member.mention} is not blacklisted.", discord.Color.green()))
    await ctx.send(embed=embed("🚫 BLACKLISTED", f"**User:** {member.mention}\n**Reason:** {row['reason'] or 'No reason provided.'}\n**Blacklisted at:** {row['created_at']}", discord.Color.red()))

@bot.command()
async def unblacklist(ctx, member: discord.Member):
    if not owner_or_admin(ctx.author):
        return await ctx.send(embed=embed("❌ ACCESS DENIED", "Admin/Owner only. Support/admin should handle blacklist appeals.", discord.Color.red()))
    cur=db.execute("DELETE FROM blacklists WHERE guild_id=? AND user_id=?", (ctx.guild.id,member.id)); db.commit()
    if not cur.rowcount:
        return await ctx.send(embed=embed("ℹ️ NOT BLACKLISTED", f"{member.mention} is not currently blacklisted.", discord.Color.orange()))
    await ctx.send(embed=embed("✅ BLACKLIST REMOVED", f"{member.mention} can create exchange tickets again.", discord.Color.green()))

@bot.command()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.kick(reason=reason); await ctx.send(embed=embed("👢 Member Kicked", f"`{member}`\n{reason}", discord.Color.orange()))

@bot.command()
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.ban(reason=reason); await ctx.send(embed=embed("🔨 Member Banned", f"`{member}`\n{reason}", discord.Color.red()))

@bot.command()
async def unban(ctx, user_id: int):
    try: user = await bot.fetch_user(user_id); await ctx.guild.unban(user)
    except (discord.HTTPException, discord.NotFound): return await ctx.send(embed=embed("❌ Unban Failed", "Check the user ID.", discord.Color.red()))
    await ctx.send(embed=embed("✅ Unbanned", f"`{user_id}`", discord.Color.green()))

@bot.command()
async def timeout(ctx, member: discord.Member, minutes: int, *, reason="No reason provided"):
    if not 1 <= minutes <= 40320: return await ctx.send(embed=embed("❌ Invalid Duration", "Use 1–40320 minutes.", discord.Color.red()))
    await member.timeout(timedelta(minutes=minutes), reason=reason); await ctx.send(embed=embed("🔇 Timed Out", f"`{member}` • `{minutes} min`", discord.Color.orange()))

@bot.command()
async def untimeout(ctx, member: discord.Member):
    await member.timeout(None); await ctx.send(embed=embed("🔊 Timeout Removed", f"`{member}`", discord.Color.green()))

@bot.command()
async def clear(ctx, amount: int):
    if not 1 <= amount <= 100: return await ctx.send(embed=embed("❌ Invalid Amount", "Use 1–100.", discord.Color.red()))
    deleted = await ctx.channel.purge(limit=amount + 1); m = await ctx.send(embed=embed("🧹 Cleared", f"Deleted `{max(0,len(deleted)-1)}` messages.", discord.Color.green())); await m.delete(delay=4)

@bot.command(name="purge")
async def purge_cmd(ctx, amount: int): await clear.callback(ctx, amount)

@bot.command()
async def slowmode(ctx, seconds: int):
    if not 0 <= seconds <= 21600: return await ctx.send(embed=embed("❌ Invalid Slowmode", "Use 0–21600 seconds.", discord.Color.red()))
    await ctx.channel.edit(slowmode_delay=seconds); await ctx.send(embed=embed("🐢 Slowmode Updated", f"`{seconds}s`", discord.Color.green()))

async def set_channel_lock(ctx, locked):
    ow = ctx.channel.overwrites_for(ctx.guild.default_role); ow.send_messages = False if locked else None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=ow)
    await ctx.send(embed=embed("🔒 Channel Locked" if locked else "🔓 Channel Unlocked", ""))

@bot.command()
async def lock(ctx): await set_channel_lock(ctx, True)
@bot.command()
async def unlock(ctx): await set_channel_lock(ctx, False)

@bot.command()
async def warn(ctx, member: discord.Member, *, reason="No reason provided"):
    db.execute("INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)", (ctx.guild.id,member.id,ctx.author.id,reason[:500],now_iso())); db.commit()
    await ctx.send(embed=embed("⚠️ Warning", f"`{member}` warned.\n**Reason:** {reason}", discord.Color.orange()))

@bot.command()
async def warnings(ctx, member: discord.Member = None):
    member = member or ctx.author
    rows = db.execute("SELECT id,reason,created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10", (ctx.guild.id,member.id)).fetchall()
    text = "\n".join(f"`#{r['id']}` • {r['reason'] or 'No reason'}" for r in rows) or "No warnings."
    await ctx.send(embed=embed("⚠️ Warnings", f"`{member}`\n{text}"))

@bot.command()
async def nick(ctx, member: discord.Member, *, name=None): await member.edit(nick=name); await ctx.send(embed=embed("✏️ Nickname Updated", f"`{member}`", discord.Color.green()))

@bot.command()
async def role(ctx, member: discord.Member, role: discord.Role): await member.add_roles(role); await ctx.send(embed=embed("🎭 Role Added", f"`{role.name}` → `{member}`", discord.Color.green()))

@bot.command()
async def unrole(ctx, member: discord.Member, role: discord.Role): await member.remove_roles(role); await ctx.send(embed=embed("🎭 Role Removed", f"`{role.name}` → `{member}`", discord.Color.orange()))

@bot.command()
async def say(ctx, *, text):
    try: await ctx.message.delete()
    except discord.HTTPException: pass
    await ctx.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())

@bot.command()
async def announce(ctx, *, text):
    try: await ctx.message.delete()
    except discord.HTTPException: pass
    await ctx.send(embed=embed("📢 ANNOUNCEMENT", text[:4000]))

@bot.command()
async def channelid(ctx): await ctx.send(embed=embed("🆔 Channel ID", f"`{ctx.channel.id}`"), delete_after=10)

@bot.command()
async def topic(ctx, *, text): await ctx.channel.edit(topic=text[:1024]); await ctx.send(embed=embed("📝 Topic Updated", "Channel topic updated.", discord.Color.green()))

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author; e = embed("🖼️ Avatar", f"[Open Avatar]({member.display_avatar.url})"); e.set_image(url=member.display_avatar.url); await ctx.send(embed=e)

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    await ctx.send(embed=embed("👤 User Info", f"**User:** `{member}`\n**ID:** `{member.id}`\n**Created:** {discord.utils.format_dt(member.created_at, 'R')}"))

@bot.command()
async def serverinfo(ctx):
    g=ctx.guild; await ctx.send(embed=embed("🏠 Server Info", f"**Name:** `{g.name}`\n**Members:** `{g.member_count}`\n**Channels:** `{len(g.channels)}`\n**Roles:** `{len(g.roles)}`"))

@bot.command()
async def roleinfo(ctx, role: discord.Role): await ctx.send(embed=embed("🎭 Role Info", f"**Name:** `{role.name}`\n**ID:** `{role.id}`\n**Members:** `{len(role.members)}`\n**Position:** `{role.position}`"))

@bot.command()
async def channelinfo(ctx):
    await ctx.send(embed=embed("📺 Channel Info", f"**Name:** `{ctx.channel.name}`\n**ID:** `{ctx.channel.id}`\n**Type:** `{ctx.channel.type}`\n**Category:** `{ctx.channel.category.name if ctx.channel.category else 'None'}`"))

@bot.command()
async def inviteinfo(ctx, invite_code: str):
    try:
        inv = await bot.fetch_invite(invite_code, with_counts=True)
        await ctx.send(embed=embed("🔗 Invite Info", f"**Code:** `{inv.code}`\n**Server:** `{inv.guild.name if inv.guild else 'Unknown'}`\n**Uses:** `{inv.uses}`"))
    except discord.HTTPException:
        await ctx.send(embed=embed("❌ Invite Error", "Could not read that invite.", discord.Color.red()))


# ----------------------------- Owner access -----------------------------
@bot.command()
async def access(ctx):
    if ctx.author.id != OWNER_ID:
        return await ctx.send(embed=embed("❌ Access Denied", "Owner only.", discord.Color.red()))
    await ctx.send(embed=embed(
        "🔐 ACCESS MODE",
        f"**Owner ID:** `{OWNER_ID}`\n**Administrators:** full command access.\n"
        "**Allow-list:** disabled — no non-admin user can be granted prefix-command access.",
        discord.Color.blurple()
    ))

# ----------------------------- Slash commands -----------------------------
SLASH_PUBLIC = {"help","rate","leaderboard"}

@bot.tree.interaction_check
async def _slash_access(interaction: discord.Interaction):
    if not interaction.guild:
        return False
    cmd = getattr(interaction.command, "name", "") if interaction.command else ""
    return owner_or_admin(interaction.user) or cmd in SLASH_PUBLIC

@bot.tree.command(name="help", description="Show the command center")
async def slash_help(interaction: discord.Interaction):
    e = embed(f"⚡ {BRAND} • SLASH HELP", "Type `/` to see available application commands. Admin-only commands are permission protected by Discord.")
    e.add_field(name="💱 Exchange", value="`/rate` • `/ticketpanel` • `/profile` • `/stats` • `/total`", inline=False)
    e.add_field(name="🛡️ Admin/Staff", value="Prefix commands remain available to authorised users; Discord also exposes command permissions in Server Settings → Integrations.", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="rate", description="Show current exchange rates")
async def slash_rate(interaction: discord.Interaction):
    c=getcfg(interaction.guild.id); await interaction.response.send_message(embed=embed("💹 LIVE RATES", f"I2C `{fmt_rate(c['i2c_rate'])}` • C2I `{fmt_rate(c['c2i_rate'])}` • C2C `{fmt_rate(c['c2c_rate'])}`"), ephemeral=True)

@bot.tree.command(name="ticketpanel", description="Post the exchange panel")
async def slash_ticketpanel(interaction: discord.Interaction):
    msg=await send_panel(interaction.channel); setcfg(interaction.guild.id,"panel_channel",interaction.channel.id); setcfg(interaction.guild.id,"panel_message",msg.id)
    await interaction.response.send_message("✅ Panel posted.", ephemeral=True)

@bot.tree.command(name="profile", description="Show a client exchange profile")
async def slash_profile(interaction: discord.Interaction, member: discord.Member | None = None):
    member = member or interaction.user
    rows = db.execute("SELECT ticket_type, amount, inr_value, input_amount FROM exchanges WHERE guild_id=? AND opener_id=? AND status='completed' ORDER BY id DESC", (interaction.guild.id, member.id)).fetchall()
    count = len(rows)
    i2c_inr = sum(float(r["input_amount"] or 0) for r in rows if r["ticket_type"] == "I2C")
    c2i_inr = sum(float(r["inr_value"] or 0) for r in rows if r["ticket_type"] == "C2I")
    c2c_usdt = sum(float(r["amount"] or 0) for r in rows if r["ticket_type"] == "C2C")
    text = (f"**Client:** {member.mention}\n**Completed Deals:** `{count}`\n**I2C Volume:** `₹{i2c_inr:,.2f}`\n**C2I Volume:** `₹{c2i_inr:,.2f}`\n**C2C Volume:** `{c2c_usdt:,.4f} USDT`\n\nCompleted exchange amounts update automatically after `.x`.")
    await interaction.response.send_message(embed=embed("👤 CLIENT EXCHANGE PROFILE", text, discord.Color.blurple()), ephemeral=False)

@bot.tree.command(name="stats", description="Show exchange statistics")
async def slash_stats(interaction: discord.Interaction):
    total,count=db.execute("SELECT COALESCE(SUM(amount),0),COUNT(*) FROM exchanges WHERE guild_id=? AND status='completed'",(interaction.guild.id,)).fetchone()
    await interaction.response.send_message(embed=embed("📊 EXCHANGE STATS",f"Completed `{count}` • Volume `{float(total or 0):,.2f} USDT`"),ephemeral=True)


@bot.tree.command(name="leaderboard", description="Show the current monthly exchanger leaderboard")
async def slash_leaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=await build_leaderboard_embed(interaction.guild),ephemeral=False)

@bot.tree.command(name="applypanel", description="Post the separate exchanger application panel")
async def slash_applypanel(interaction: discord.Interaction):
    msg=await send_apply_panel(interaction.channel); await interaction.response.send_message("✅ Application panel posted.",ephemeral=True)

# ----------------------------- Backups / export (new) -----------------------------
def run_db_backup():
    """Consistent snapshot using SQLite's online backup API (safe while the bot is running)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = discord.utils.utcnow().strftime("%Y%m%d-%H%M%S")
    dest_path = BACKUP_DIR / f"{Path(DB).stem}-{stamp}.db"
    dest = sqlite3.connect(dest_path)
    try:
        db.backup(dest)
    finally:
        dest.close()
    old = sorted(BACKUP_DIR.glob(f"{Path(DB).stem}-*.db"))
    for f in old[:-BACKUP_KEEP]:
        try: f.unlink()
        except OSError: pass
    return dest_path


@tasks.loop(hours=24)
async def backup_loop():
    try:
        path = await asyncio.to_thread(run_db_backup)
        log.info("Database backup written: %s", path)
    except Exception:
        log.exception("Database backup failed")

@backup_loop.before_loop
async def before_backup_loop():
    await bot.wait_until_ready()


@bot.command()
async def backup(ctx):
    """Owner only: take a database snapshot right now."""
    if ctx.author.id != OWNER_ID:
        return await ctx.send(embed=embed("❌ Access Denied", "Owner only.", discord.Color.red()))
    path = await asyncio.to_thread(run_db_backup)
    await ctx.send(embed=embed("💾 Backup Created", f"Saved `{path.name}`.\nKeeping the newest `{BACKUP_KEEP}` snapshots.", discord.Color.green()))


@bot.command(name="export")
async def export_cmd(ctx, days: int = 30):
    """Admin only: export completed exchanges as a CSV file."""
    if not owner_or_admin(ctx.author):
        return await ctx.send(embed=embed("❌ Access Denied", "Administrator/Owner only.", discord.Color.red()))
    if not 1 <= days <= 3650:
        return await ctx.send(embed=embed("❌ Invalid Range", f"Use `{PREFIX}export 30` (1–3650 days).", discord.Color.red()))
    since = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
    rows = db.execute("SELECT id,created_at,ticket_type,amount,rate,inr_value,input_amount,input_currency,opener_id,claimed_by FROM exchanges WHERE guild_id=? AND status='completed' AND created_at>=? ORDER BY id", (ctx.guild.id, since)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id","created_at","type","usdt_amount","rate","inr_value","input_amount","input_currency","client_id","exchanger_id"])
    for r in rows:
        w.writerow(list(r))
    if not rows:
        return await ctx.send(embed=embed("📄 Nothing To Export", f"No completed exchanges in the last `{days}` day(s).", discord.Color.orange()))
    file = discord.File(io.BytesIO(buf.getvalue().encode("utf-8")), filename=f"exchanges-last-{days}d.csv")
    await ctx.send(embed=embed("📄 Export Ready", f"`{len(rows)}` completed exchange(s) from the last `{days}` day(s).", discord.Color.green()), file=file)


# ----------------------------- Events / errors -----------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        text = "You do not have permission to use this command."
    else:
        log.error("Slash command failed: %r", error, exc_info=error)
        text = "Something went wrong running that command. Please try again."
    msg = embed("⚠️ COMMAND FAILED", text, discord.Color.red())
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
    except discord.HTTPException:
        pass

@bot.event
async def on_ready():
    # Persistent views survive restarts because their custom IDs are stable.
    if not getattr(bot, "_views_registered", False):
        bot.add_view(TicketPanel()); bot.add_view(TicketActions()); bot.add_view(AgreementView()); bot.add_view(ApplyPanel())
        bot._views_registered = True
    if not demo_showcase_loop.is_running():
        demo_showcase_loop.start()
    if not unclaimed_cleanup_loop.is_running():
        unclaimed_cleanup_loop.start()
    if not backup_loop.is_running():
        backup_loop.start()
    try:
        if not getattr(bot, "_slash_synced", False):
            await bot.tree.sync()
            bot._slash_synced = True
    except discord.HTTPException as exc:
        log.warning("Slash sync error: %s", exc)
    for guild in bot.guilds:
        try:
            if guild.me and guild.me.guild_permissions.change_nickname:
                await guild.me.edit(nick=BRAND, reason=BRAND)
        except discord.HTTPException:
            pass
    log.info("%s online as %s | %d server(s)", BRAND, bot.user, len(bot.guilds))


@bot.event
async def on_command_error(ctx, error):
    err = getattr(error, "original", error)
    name = getattr(getattr(ctx, "command", None), "qualified_name", "this command")
    usage = {
        "setrate": f"`{PREFIX}setrate I2C 104`", "setstaff": f"`{PREFIX}setstaff @Staff`",
        "setstafflimit": f"`{PREFIX}setstafflimit 40`", "setunclaimedtimeout": f"`{PREFIX}setunclaimedtimeout 120`",
        "setlog": f"`{PREFIX}setlog #logs`", "setshowcase": f"`{PREFIX}setshowcase #showcase`",
        "setdonechannel": f"`{PREFIX}setdonechannel #completed`", "settotalchannel": f"`{PREFIX}settotalchannel #total-counter`",
        "seti2ccategory": f"`{PREFIX}seti2ccategory #CATEGORY`", "setc2icategory": f"`{PREFIX}setc2icategory #CATEGORY`",
        "setc2ccategory": f"`{PREFIX}setc2ccategory #CATEGORY`", "ticketpanel": f"`{PREFIX}ticketpanel`",
        "3p": f"`{PREFIX}3p` inside a registered ticket", "upi": f"Reply to a message containing a UPI ID, then `{PREFIX}upi 500`", "blacklist": f"`{PREFIX}blacklist @user [reason]`", "blacklistcheck": f"`{PREFIX}blacklistcheck @user`", "unblacklist": f"`{PREFIX}unblacklist @user`",
    }
    if isinstance(err, commands.CheckFailure):
        return await ctx.send(embed=embed("🔐 ACCESS DENIED", "Only the configured **Owner ID** or a server member with **Administrator** can use bot commands.", discord.Color.red()),delete_after=8)
    if isinstance(err, commands.CommandNotFound):
        raw=ctx.message.content[len(PREFIX):].split() if ctx.message.content.startswith(PREFIX) else []
        bad=raw[0] if raw else "that command"
        return await ctx.send(embed=embed("❌ UNKNOWN COMMAND",f"`{PREFIX}{bad}` is not a registered command.\n\nUse `{PREFIX}help` to see valid commands.",discord.Color.red()),delete_after=8)
    if isinstance(err, commands.MissingRequiredArgument):
        return await ctx.send(embed=embed("❌ MISSING ARGUMENT",f"You missed **{err.param.name}** for `{PREFIX}{name}`.\n\nCorrect format: {usage.get(name, f'`{PREFIX}{name}` — see `{PREFIX}help`')}",discord.Color.red()),delete_after=10)
    if isinstance(err, commands.BadArgument):
        return await ctx.send(embed=embed("❌ INVALID ARGUMENT",f"Discord could not understand one of the arguments for `{PREFIX}{name}`.\n\nTry: {usage.get(name, f'`{PREFIX}{name}` — see `{PREFIX}help`')}",discord.Color.red()),delete_after=10)
    if isinstance(err, commands.CommandOnCooldown):
        return await ctx.send(embed=embed("⏳ SLOW DOWN",f"Try again in `{err.retry_after:.1f}s`.",discord.Color.orange()),delete_after=6)
    if isinstance(err, commands.MissingPermissions):
        return await ctx.send(embed=embed("❌ DISCORD PERMISSION MISSING",f"Discord rejected `{PREFIX}{name}` because your account lacks the required server permission.\n\nIf you are the bot owner, Owner access bypasses this bot-level check but cannot bypass Discord's own permissions.",discord.Color.red()),delete_after=10)
    if isinstance(err, discord.Forbidden):
        return await ctx.send(embed=embed("❌ BOT PERMISSION MISSING",f"Discord blocked `{PREFIX}{name}`. Check the bot's role/permissions and its role position.\n\nCommon requirements: **Manage Channels, Manage Permissions, Manage Messages, Manage Roles** depending on the command.",discord.Color.red()),delete_after=10)
    log.error("Command %s failed: %r", name, err, exc_info=err)
    await ctx.send(embed=embed("⚠️ COMMAND FAILED",f"`{PREFIX}{name}` failed unexpectedly.\n\n**What to do:** check the bot permissions and command format, then try again.\nIf it keeps failing, send the error from the bot console to the owner/developer.",discord.Color.red()),delete_after=10)


if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is missing. Copy .env.example to .env and set it.")
if not OWNER_ID:
    log.warning("OWNER_ID is not set - only server Administrators will be able to use the bot.")

bot.run(TOKEN)
