import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Iterable

import discord
from discord.ext import commands

TOKEN = "Discord_token"
PREFIXES = ["!", "."]
DATA_FILE = "guild_log_config.json"
UTC = timezone.utc

EMBED_COLOR = discord.Color.blurple()
ERROR_COLOR = discord.Color.red()
SUCCESS_COLOR = discord.Color.green()
WARN_COLOR = discord.Color.orange()

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.messages = True
intents.voice_states = True
intents.moderation = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(*PREFIXES),
    intents=intents,
    help_command=None,
    case_insensitive=True,
)

def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def guild_key(guild_id: int) -> str:
    return str(guild_id)


def get_guild_config(guild_id: int) -> dict:
    data = load_data()
    return data.get(guild_key(guild_id), {})


def update_guild_config(guild_id: int, config: dict) -> None:
    data = load_data()
    data[guild_key(guild_id)] = config
    save_data(data)


def remove_guild_config(guild_id: int) -> None:
    data = load_data()
    data.pop(guild_key(guild_id), None)
    save_data(data)


def truncate(text: Optional[str], limit: int = 1024) -> str:
    if text is None:
        return "-"
    text = str(text).strip()
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def format_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return discord.utils.format_dt(dt, style="F")


def list_names(items: Iterable[str]) -> str:
    items = [str(i) for i in items if i]
    if not items:
        return "-"
    return ", ".join(items)


def parse_duration(value: str) -> Optional[timedelta]:
    value = value.strip().lower()
    if not value:
        return None

    units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }

    unit = value[-1]
    amount = value[:-1]

    if unit not in units or not amount.isdigit():
        return None

    seconds = int(amount) * units[unit]
    if seconds <= 0:
        return None

    return timedelta(seconds=seconds)


def human_timedelta(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    chunks = []

    for label, size in (
        ("أسبوع", 604800),
        ("يوم", 86400),
        ("ساعة", 3600),
        ("دقيقة", 60),
        ("ثانية", 1),
    ):
        if seconds >= size:
            count, seconds = divmod(seconds, size)
            chunks.append(f"{count} {label}")

    return " و ".join(chunks[:3]) if chunks else "0 ثانية"


def make_embed(
    title: str,
    description: Optional[str] = None,
    color: discord.Color = EMBED_COLOR,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Power Logs Bot")
    return embed


async def safe_send(channel: Optional[discord.TextChannel], embed: discord.Embed) -> None:
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except Exception:
        pass


def resolve_log_channel(guild: discord.Guild, key: str) -> Optional[discord.TextChannel]:
    config = get_guild_config(guild.id)
    channels = config.get("channels", {})
    channel_id = channels.get(key)
    if not channel_id:
        return None

    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


async def send_log(guild: discord.Guild, key: str, embed: discord.Embed) -> None:
    channel = resolve_log_channel(guild, key)
    await safe_send(channel, embed)


async def recent_audit_actor(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: Optional[int] = None,
    within_seconds: int = 12,
) -> Optional[discord.abc.User]:
    me = guild.me
    if me is None or not me.guild_permissions.view_audit_log:
        return None

    now = discord.utils.utcnow()
    try:
        async for entry in guild.audit_logs(limit=6, action=action):
            if target_id is not None and getattr(entry.target, "id", None) != target_id:
                continue
            if entry.created_at and (now - entry.created_at).total_seconds() > within_seconds:
                continue
            return entry.user
    except Exception:
        return None
    return None


def staff_roles_from_guild(guild: discord.Guild) -> list[discord.Role]:
    result = []
    for role in guild.roles:
        perms = role.permissions
        if any(
            [
                perms.administrator,
                perms.manage_guild,
                perms.manage_channels,
                perms.manage_messages,
                perms.moderate_members,
                perms.kick_members,
                perms.ban_members,
                perms.view_audit_log,
            ]
        ):
            result.append(role)
    return result


async def build_log_category(
    guild: discord.Guild,
    creator: discord.Member,
    selected_roles: list[discord.Role],
) -> tuple[discord.CategoryChannel, dict, list[int]]:
    me = guild.me
    if me is None:
        raise RuntimeError("تعذر العثور على البوت داخل السيرفر")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
        ),
        creator: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        ),
    }

    for role in selected_roles:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        )

    category = await guild.create_category(
        "📁｜logs",
        overwrites=overwrites,
        reason="Power log setup",
    )
    managed_ids = [category.id]

    channel_map = {}
    names = {
        "messages": "📝｜message-logs",
        "members": "👥｜member-logs",
        "voice": "🔊｜voice-logs",
        "mod": "🛡️｜mod-logs",
        "server": "⚙️｜server-logs",
    }

    for key, name in names.items():
        ch = await guild.create_text_channel(
            name,
            category=category,
            overwrites=overwrites,
            reason="Power log setup",
        )
        channel_map[key] = ch.id
        managed_ids.append(ch.id)

    return category, channel_map, managed_ids


async def log_command_action(
    ctx: commands.Context,
    action: str,
    color: discord.Color = EMBED_COLOR,
    **fields,
) -> None:
    embed = make_embed(action, color=color)
    embed.add_field(name="المنفذ", value=f"{ctx.author.mention}\n`{ctx.author.id}`", inline=True)
    embed.add_field(name="الروم", value=ctx.channel.mention if ctx.guild else "-", inline=True)
    embed.add_field(name="الأمر", value=f"`{ctx.message.content}`", inline=False)

    for name, value in fields.items():
        embed.add_field(name=name, value=truncate(value), inline=False)

    if ctx.guild:
        await send_log(ctx.guild, "mod", embed)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="السيرفر واللوقات",
            )
        )
    except Exception:
        pass


@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"Joined guild: {guild.name} ({guild.id})")


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return

    embed = make_embed("تم حذف رسالة", color=WARN_COLOR)
    embed.add_field(name="الكاتب", value=f"{message.author.mention}\n`{message.author.id}`", inline=True)
    embed.add_field(name="الروم", value=message.channel.mention, inline=True)
    embed.add_field(name="وقت الإرسال", value=format_dt(message.created_at), inline=False)
    embed.add_field(name="المحتوى", value=truncate(message.content, 1000), inline=False)

    if message.attachments:
        files = "\n".join(f"[{a.filename}]({a.url})" for a in message.attachments[:10])
        embed.add_field(name="المرفقات", value=truncate(files, 1000), inline=False)

    await send_log(message.guild, "messages", embed)


@bot.event
async def on_bulk_message_delete(messages: list[discord.Message]):
    if not messages:
        return

    first = messages[0]
    if not first.guild:
        return

    embed = make_embed("حذف جماعي للرسائل", color=WARN_COLOR)
    embed.add_field(name="الروم", value=first.channel.mention, inline=True)
    embed.add_field(name="العدد", value=str(len(messages)), inline=True)

    preview = []
    for msg in messages[:10]:
        preview.append(f"{msg.author}: {truncate(msg.content, 70)}")

    embed.add_field(name="معاينة", value=truncate("\n".join(preview), 1000), inline=False)
    await send_log(first.guild, "messages", embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not before.guild or before.author.bot:
        return
    if before.content == after.content:
        return

    embed = make_embed("تم تعديل رسالة")
    embed.add_field(name="الكاتب", value=f"{before.author.mention}\n`{before.author.id}`", inline=True)
    embed.add_field(name="الروم", value=before.channel.mention, inline=True)
    embed.add_field(name="الرابط", value=f"[اذهب للرسالة]({after.jump_url})", inline=True)
    embed.add_field(name="قبل", value=truncate(before.content, 1000), inline=False)
    embed.add_field(name="بعد", value=truncate(after.content, 1000), inline=False)

    await send_log(before.guild, "messages", embed)


@bot.event
async def on_member_join(member: discord.Member):
    embed = make_embed("عضو دخل السيرفر", color=SUCCESS_COLOR)
    embed.add_field(name="العضو", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="تاريخ إنشاء الحساب", value=format_dt(member.created_at), inline=True)
    embed.add_field(name="عدد أعضاء السيرفر", value=str(member.guild.member_count), inline=True)
    await send_log(member.guild, "members", embed)


@bot.event
async def on_member_remove(member: discord.Member):
    embed = make_embed("عضو خرج من السيرفر", color=WARN_COLOR)
    embed.add_field(name="العضو", value=f"{member} (`{member.id}`)", inline=True)
    embed.add_field(name="تاريخ الدخول", value=format_dt(member.joined_at), inline=True)
    embed.add_field(
        name="الأدوار",
        value=truncate(", ".join(role.name for role in member.roles[1:][::-1]) or "-", 1000),
        inline=False,
    )
    await send_log(member.guild, "members", embed)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    actor = await recent_audit_actor(guild, discord.AuditLogAction.ban, target_id=user.id)
    embed = make_embed("تم حظر عضو", color=ERROR_COLOR)
    embed.add_field(name="المستهدف", value=f"{user}\n`{user.id}`", inline=True)
    embed.add_field(
        name="المنفذ",
        value=f"{actor.mention}\n`{actor.id}`" if actor else "غير معروف",
        inline=True,
    )
    await send_log(guild, "mod", embed)


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    actor = await recent_audit_actor(guild, discord.AuditLogAction.unban, target_id=user.id)
    embed = make_embed("تم فك حظر عضو", color=SUCCESS_COLOR)
    embed.add_field(name="المستهدف", value=f"{user}\n`{user.id}`", inline=True)
    embed.add_field(
        name="المنفذ",
        value=f"{actor.mention}\n`{actor.id}`" if actor else "غير معروف",
        inline=True,
    )
    await send_log(guild, "mod", embed)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.nick != after.nick:
        embed = make_embed("تم تغيير النك نيم")
        embed.add_field(name="العضو", value=f"{after.mention}\n`{after.id}`", inline=True)
        embed.add_field(name="قبل", value=before.nick or before.name, inline=True)
        embed.add_field(name="بعد", value=after.nick or after.name, inline=True)
        await send_log(after.guild, "members", embed)

    before_roles = {r.id: r for r in before.roles}
    after_roles = {r.id: r for r in after.roles}

    added = [r.mention for rid, r in after_roles.items() if rid not in before_roles and not r.is_default()]
    removed = [r.mention for rid, r in before_roles.items() if rid not in after_roles and not r.is_default()]

    if added or removed:
        embed = make_embed("تم تحديث أدوار عضو")
        embed.add_field(name="العضو", value=f"{after.mention}\n`{after.id}`", inline=True)
        embed.add_field(name="أدوار تمت إضافتها", value=truncate(list_names(added), 1000), inline=False)
        embed.add_field(name="أدوار تمت إزالتها", value=truncate(list_names(removed), 1000), inline=False)
        await send_log(after.guild, "members", embed)

    if before.timed_out_until != after.timed_out_until:
        embed = make_embed("تم تغيير حالة التايم أوت", color=WARN_COLOR)
        embed.add_field(name="العضو", value=f"{after.mention}\n`{after.id}`", inline=True)
        embed.add_field(name="قبل", value=format_dt(before.timed_out_until), inline=True)
        embed.add_field(name="بعد", value=format_dt(after.timed_out_until), inline=True)
        await send_log(after.guild, "mod", embed)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if (
        before.channel == after.channel
        and before.mute == after.mute
        and before.deaf == after.deaf
        and before.self_mute == after.self_mute
        and before.self_deaf == after.self_deaf
    ):
        return

    embed = make_embed("تحديث صوتي")
    embed.add_field(name="العضو", value=f"{member.mention}\n`{member.id}`", inline=True)

    if before.channel != after.channel:
        if before.channel is None and after.channel is not None:
            embed.description = "دخل روم صوتي"
            embed.add_field(name="الروم", value=after.channel.mention, inline=True)
        elif before.channel is not None and after.channel is None:
            embed.description = "خرج من روم صوتي"
            embed.add_field(name="الروم", value=before.channel.mention, inline=True)
        else:
            embed.description = "انتقل بين رومين صوتية"
            embed.add_field(name="من", value=before.channel.mention, inline=True)
            embed.add_field(name="إلى", value=after.channel.mention, inline=True)
    else:
        changes = []
        if before.mute != after.mute:
            changes.append(f"Server Mute: `{before.mute}` -> `{after.mute}`")
        if before.deaf != after.deaf:
            changes.append(f"Server Deaf: `{before.deaf}` -> `{after.deaf}`")
        if before.self_mute != after.self_mute:
            changes.append(f"Self Mute: `{before.self_mute}` -> `{after.self_mute}`")
        if before.self_deaf != after.self_deaf:
            changes.append(f"Self Deaf: `{before.self_deaf}` -> `{after.self_deaf}`")

        embed.description = "تغيرت حالة الصوت"
        embed.add_field(name="الروم", value=after.channel.mention if after.channel else "-", inline=True)
        embed.add_field(name="التغييرات", value=truncate("\n".join(changes), 1000), inline=False)

    await send_log(member.guild, "voice", embed)


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    actor = await recent_audit_actor(channel.guild, discord.AuditLogAction.channel_create, target_id=channel.id)
    embed = make_embed("تم إنشاء روم", color=SUCCESS_COLOR)
    embed.add_field(
        name="الروم",
        value=f"{channel.mention if hasattr(channel, 'mention') else channel.name}\n`{channel.id}`",
        inline=True,
    )
    embed.add_field(name="النوع", value=str(channel.type), inline=True)
    embed.add_field(
        name="المنفذ",
        value=f"{actor.mention}\n`{actor.id}`" if actor else "غير معروف",
        inline=True,
    )
    await send_log(channel.guild, "server", embed)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    actor = await recent_audit_actor(channel.guild, discord.AuditLogAction.channel_delete, target_id=channel.id)
    embed = make_embed("تم حذف روم", color=ERROR_COLOR)
    embed.add_field(name="الاسم", value=f"{channel.name}\n`{channel.id}`", inline=True)
    embed.add_field(name="النوع", value=str(channel.type), inline=True)
    embed.add_field(
        name="المنفذ",
        value=f"{actor.mention}\n`{actor.id}`" if actor else "غير معروف",
        inline=True,
    )
    await send_log(channel.guild, "server", embed)


@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    changes = []
    if before.name != after.name:
        changes.append(f"الاسم: `{before.name}` -> `{after.name}`")
    if getattr(before, "topic", None) != getattr(after, "topic", None):
        changes.append("تم تعديل وصف الروم")
    if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
        changes.append(f"السلو مود: `{getattr(before, 'slowmode_delay', 0)}` -> `{getattr(after, 'slowmode_delay', 0)}`")

    if not changes:
        return

    actor = await recent_audit_actor(after.guild, discord.AuditLogAction.channel_update, target_id=after.id)
    embed = make_embed("تم تحديث روم")
    embed.add_field(name="الروم", value=f"{after.name}\n`{after.id}`", inline=True)
    embed.add_field(
        name="المنفذ",
        value=f"{actor.mention}\n`{actor.id}`" if actor else "غير معروف",
        inline=True,
    )
    embed.add_field(name="التغييرات", value=truncate("\n".join(changes), 1000), inline=False)
    await send_log(after.guild, "server", embed)


@bot.event
async def on_guild_role_create(role: discord.Role):
    actor = await recent_audit_actor(role.guild, discord.AuditLogAction.role_create, target_id=role.id)
    embed = make_embed("تم إنشاء رتبة", color=SUCCESS_COLOR)
    embed.add_field(name="الرتبة", value=f"{role.mention}\n`{role.id}`", inline=True)
    embed.add_field(
        name="المنفذ",
        value=f"{actor.mention}\n`{actor.id}`" if actor else "غير معروف",
        inline=True,
    )
    await send_log(role.guild, "server", embed)


@bot.event
async def on_guild_role_delete(role: discord.Role):
    actor = await recent_audit_actor(role.guild, discord.AuditLogAction.role_delete, target_id=role.id)
    embed = make_embed("تم حذف رتبة", color=ERROR_COLOR)
    embed.add_field(name="الاسم", value=f"{role.name}\n`{role.id}`", inline=True)
    embed.add_field(
        name="المنفذ",
        value=f"{actor.mention}\n`{actor.id}`" if actor else "غير معروف",
        inline=True,
    )
    await send_log(role.guild, "server", embed)


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    changes = []
    if before.name != after.name:
        changes.append(f"الاسم: `{before.name}` -> `{after.name}`")
    if before.color != after.color:
        changes.append(f"اللون: `{before.color}` -> `{after.color}`")
    if before.hoist != after.hoist:
        changes.append(f"منفصل في القائمة: `{before.hoist}` -> `{after.hoist}`")
    if before.mentionable != after.mentionable:
        changes.append(f"قابل للمنشن: `{before.mentionable}` -> `{after.mentionable}`")

    if not changes:
        return

    actor = await recent_audit_actor(after.guild, discord.AuditLogAction.role_update, target_id=after.id)
    embed = make_embed("تم تحديث رتبة")
    embed.add_field(name="الرتبة", value=f"{after.mention}\n`{after.id}`", inline=True)
    embed.add_field(
        name="المنفذ",
        value=f"{actor.mention}\n`{actor.id}`" if actor else "غير معروف",
        inline=True,
    )
    embed.add_field(name="التغييرات", value=truncate("\n".join(changes), 1000), inline=False)
    await send_log(after.guild, "server", embed)


@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    changes = []
    if before.name != after.name:
        changes.append(f"اسم السيرفر: `{before.name}` -> `{after.name}`")
    if before.afk_timeout != after.afk_timeout:
        changes.append(f"AFK Timeout: `{before.afk_timeout}` -> `{after.afk_timeout}`")
    if before.verification_level != after.verification_level:
        changes.append(f"Verification Level: `{before.verification_level}` -> `{after.verification_level}`")

    if not changes:
        return

    embed = make_embed("تم تحديث إعدادات السيرفر")
    embed.add_field(name="السيرفر", value=f"{after.name}\n`{after.id}`", inline=True)
    embed.add_field(name="التغييرات", value=truncate("\n".join(changes), 1000), inline=False)
    await send_log(after, "server", embed)

@bot.command(name="setuplogs", aliases=["لوقات", "إعداد_اللوقات"])
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def setup_logs(ctx: commands.Context, *roles: discord.Role):
    config = get_guild_config(ctx.guild.id)
    if config.get("channels"):
        return await ctx.send(
            embed=make_embed(
                "اللوقات موجودة مسبقاً",
                "استخدم `!cleanupbot` إذا تبغى تمسح النظام القديم أول.",
                WARN_COLOR,
            )
        )

    selected_roles = list(dict.fromkeys(roles)) or staff_roles_from_guild(ctx.guild)
    if not selected_roles:
        selected_roles = [ctx.author.top_role]

    try:
        category, channel_map, managed_ids = await build_log_category(ctx.guild, ctx.author, selected_roles)
    except discord.Forbidden:
        return await ctx.send(
            embed=make_embed(
                "ما قدرت أسوي اللوقات",
                "تأكد أن البوت عنده صلاحيات Administrator أو على الأقل Manage Channels + Manage Roles + View Audit Log.",
                ERROR_COLOR,
            )
        )
    except RuntimeError as e:
        return await ctx.send(embed=make_embed("صار خطأ", str(e), ERROR_COLOR))

    update_guild_config(
        ctx.guild.id,
        {
            "category_id": category.id,
            "channels": channel_map,
            "managed_ids": managed_ids,
            "staff_role_ids": [r.id for r in selected_roles],
        },
    )

    lines = [f"**{k}**: <#{v}>" for k, v in channel_map.items()]
    embed = make_embed("تم إنشاء نظام اللوقات", color=SUCCESS_COLOR)
    embed.description = "\n".join(lines)
    embed.add_field(
        name="الرتب المسموح لها بالمشاهدة",
        value=truncate(", ".join(r.mention for r in selected_roles), 1000),
        inline=False,
    )
    await ctx.send(embed=embed)
    await log_command_action(
        ctx,
        "إنشاء نظام اللوقات",
        color=SUCCESS_COLOR,
        تفاصيل="تم إنشاء فئة اللوقات والرومات الخاصة بها.",
    )


@bot.command(name="cleanupbot", aliases=["cleanup", "مسح_النظام"])
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def cleanup_bot(ctx: commands.Context):
    config = get_guild_config(ctx.guild.id)
    managed_ids = config.get("managed_ids", [])

    if not managed_ids:
        return await ctx.send(
            embed=make_embed("ما لقيت شيء أحذفه", "ما فيه بيانات محفوظة لهذا السيرفر.", WARN_COLOR)
        )

    deleted = 0
    for channel_id in managed_ids[::-1]:
        ch = ctx.guild.get_channel(channel_id)
        if ch is None:
            continue
        try:
            await ch.delete(reason=f"Cleanup requested by {ctx.author}")
            deleted += 1
            await asyncio.sleep(0.6)
        except Exception:
            pass

    remove_guild_config(ctx.guild.id)
    await ctx.send(
        embed=make_embed("تم تنظيف النظام", f"تم حذف **{deleted}** عنصر أنشأه البوت.", SUCCESS_COLOR)
    )


@bot.command(name="mute", aliases=["timeout", "ميوت", "تايم"])
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def mute_member(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "بدون سبب"):
    me = ctx.guild.me
    if me is None:
        return await ctx.send(embed=make_embed("صار خطأ", "تعذر التعرف على البوت داخل السيرفر.", ERROR_COLOR))

    if member == ctx.author:
        return await ctx.send(embed=make_embed("ما تقدر تميوت نفسك", color=ERROR_COLOR))
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        return await ctx.send(embed=make_embed("ما تقدر تميوت عضو أعلى منك أو نفس رتبتك", color=ERROR_COLOR))
    if member.top_role >= me.top_role:
        return await ctx.send(embed=make_embed("رتبة العضو أعلى من البوت", color=ERROR_COLOR))

    td = parse_duration(duration)
    if td is None:
        return await ctx.send(
            embed=make_embed(
                "صيغة الوقت غلط",
                "مثال: `!mute @user 10m سبام` أو `!mute @user 2h`",
                ERROR_COLOR,
            )
        )

    max_timeout = timedelta(days=28)
    if td > max_timeout:
        return await ctx.send(embed=make_embed("مدة التايم أوت طويلة", "الحد الأعلى 28 يوم.", ERROR_COLOR))

    until = discord.utils.utcnow() + td

    try:
        await member.timeout(until, reason=f"{ctx.author} | {reason}")
    except discord.Forbidden:
        return await ctx.send(
            embed=make_embed(
                "ما قدرت أنفذ التايم أوت",
                "تأكد أن البوت عنده Moderate Members ورتبته أعلى من العضو.",
                ERROR_COLOR,
            )
        )

    embed = make_embed("تم إعطاء تايم أوت", color=SUCCESS_COLOR)
    embed.add_field(name="العضو", value=member.mention, inline=True)
    embed.add_field(name="المدة", value=human_timedelta(td), inline=True)
    embed.add_field(name="السبب", value=truncate(reason, 1000), inline=False)
    await ctx.send(embed=embed)

    await log_command_action(
        ctx,
        "تايم أوت عضو",
        color=WARN_COLOR,
        المستهدف=f"{member.mention} (`{member.id}`)",
        المدة=human_timedelta(td),
        السبب=reason,
    )


@bot.command(name="unmute", aliases=["untimeout", "فك", "فك_ميوت", "فك_تايم"])
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def unmute_member(ctx: commands.Context, member: discord.Member, *, reason: str = "بدون سبب"):
    try:
        await member.timeout(None, reason=f"{ctx.author} | {reason}")
    except discord.Forbidden:
        return await ctx.send(embed=make_embed("ما قدرت أفك التايم أوت", color=ERROR_COLOR))

    embed = make_embed("تم فك التايم أوت", color=SUCCESS_COLOR)
    embed.add_field(name="العضو", value=member.mention, inline=True)
    embed.add_field(name="السبب", value=truncate(reason, 1000), inline=False)
    await ctx.send(embed=embed)

    await log_command_action(
        ctx,
        "فك تايم أوت",
        color=SUCCESS_COLOR,
        المستهدف=f"{member.mention} (`{member.id}`)",
        السبب=reason,
    )


@bot.command(name="vmute", aliases=["voice_mute", "ميوت_صوت"])
@commands.guild_only()
@commands.has_permissions(mute_members=True)
async def voice_mute(ctx: commands.Context, member: discord.Member, *, reason: str = "بدون سبب"):
    me = ctx.guild.me
    if me is None:
        return await ctx.send(embed=make_embed("صار خطأ", "تعذر التعرف على البوت داخل السيرفر.", ERROR_COLOR))

    if not member.voice or not member.voice.channel:
        return await ctx.send(embed=make_embed("العضو ليس في روم صوتي", color=ERROR_COLOR))
    if member.top_role >= me.top_role:
        return await ctx.send(embed=make_embed("رتبة العضو أعلى من البوت", color=ERROR_COLOR))

    try:
        await member.edit(mute=True, reason=f"{ctx.author} | {reason}")
    except discord.Forbidden:
        return await ctx.send(embed=make_embed("ما قدرت أسوي ميوت صوتي", color=ERROR_COLOR))

    embed = make_embed("تم عمل ميوت صوتي", color=SUCCESS_COLOR)
    embed.add_field(name="العضو", value=member.mention, inline=True)
    embed.add_field(name="الروم", value=member.voice.channel.mention, inline=True)
    embed.add_field(name="السبب", value=truncate(reason, 1000), inline=False)
    await ctx.send(embed=embed)

    await log_command_action(
        ctx,
        "ميوت صوتي",
        color=WARN_COLOR,
        المستهدف=f"{member.mention} (`{member.id}`)",
        السبب=reason,
    )


@bot.command(name="vunmute", aliases=["voice_unmute", "فك_ميوت_صوت"])
@commands.guild_only()
@commands.has_permissions(mute_members=True)
async def voice_unmute(ctx: commands.Context, member: discord.Member, *, reason: str = "بدون سبب"):
    if not member.voice or not member.voice.channel:
        return await ctx.send(embed=make_embed("العضو ليس في روم صوتي", color=ERROR_COLOR))

    try:
        await member.edit(mute=False, reason=f"{ctx.author} | {reason}")
    except discord.Forbidden:
        return await ctx.send(embed=make_embed("ما قدرت أفك الميوت الصوتي", color=ERROR_COLOR))

    embed = make_embed("تم فك الميوت الصوتي", color=SUCCESS_COLOR)
    embed.add_field(name="العضو", value=member.mention, inline=True)
    embed.add_field(name="الروم", value=member.voice.channel.mention, inline=True)
    embed.add_field(name="السبب", value=truncate(reason, 1000), inline=False)
    await ctx.send(embed=embed)

    await log_command_action(
        ctx,
        "فك ميوت صوتي",
        color=SUCCESS_COLOR,
        المستهدف=f"{member.mention} (`{member.id}`)",
        السبب=reason,
    )


@bot.command(name="lock", aliases=["قفل", "lockroom"])
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
async def lock_channel(
    ctx: commands.Context,
    channel: Optional[discord.abc.GuildChannel] = None,
    *,
    reason: str = "بدون سبب",
):
    channel = channel or ctx.channel

    try:
        if isinstance(channel, discord.TextChannel):
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(
                ctx.guild.default_role,
                overwrite=overwrite,
                reason=f"{ctx.author} | {reason}",
            )
        elif isinstance(channel, discord.VoiceChannel):
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.connect = False
            await channel.set_permissions(
                ctx.guild.default_role,
                overwrite=overwrite,
                reason=f"{ctx.author} | {reason}",
            )
        else:
            return await ctx.send(
                embed=make_embed("هذا النوع من الرومات غير مدعوم حالياً", color=ERROR_COLOR)
            )
    except discord.Forbidden:
        return await ctx.send(embed=make_embed("ما قدرت أقفل الروم", color=ERROR_COLOR))

    await ctx.send(
        embed=make_embed(
            "تم قفل الروم",
            f"الروم: {channel.mention if hasattr(channel, 'mention') else channel.name}",
            SUCCESS_COLOR,
        )
    )

    await log_command_action(
        ctx,
        "قفل روم",
        color=WARN_COLOR,
        الروم=f"{channel.name} (`{channel.id}`)",
        السبب=reason,
    )


@bot.command(name="unlock", aliases=["فتح", "unlockroom"])
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
async def unlock_channel(
    ctx: commands.Context,
    channel: Optional[discord.abc.GuildChannel] = None,
    *,
    reason: str = "بدون سبب",
):
    channel = channel or ctx.channel

    try:
        if isinstance(channel, discord.TextChannel):
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = None
            await channel.set_permissions(
                ctx.guild.default_role,
                overwrite=overwrite,
                reason=f"{ctx.author} | {reason}",
            )
        elif isinstance(channel, discord.VoiceChannel):
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.connect = None
            await channel.set_permissions(
                ctx.guild.default_role,
                overwrite=overwrite,
                reason=f"{ctx.author} | {reason}",
            )
        else:
            return await ctx.send(
                embed=make_embed("هذا النوع من الرومات غير مدعوم حالياً", color=ERROR_COLOR)
            )
    except discord.Forbidden:
        return await ctx.send(embed=make_embed("ما قدرت أفتح الروم", color=ERROR_COLOR))

    await ctx.send(
        embed=make_embed(
            "تم فتح الروم",
            f"الروم: {channel.mention if hasattr(channel, 'mention') else channel.name}",
            SUCCESS_COLOR,
        )
    )

    await log_command_action(
        ctx,
        "فتح روم",
        color=SUCCESS_COLOR,
        الروم=f"{channel.name} (`{channel.id}`)",
        السبب=reason,
    )


@bot.command(name="clear", aliases=["purge", "مسح"])
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def clear_messages(ctx: commands.Context, amount: int):
    if amount <= 0:
        return await ctx.send(embed=make_embed("لازم العدد يكون أكبر من 0", color=ERROR_COLOR))

    amount = min(amount, 500)
    deleted = await ctx.channel.purge(limit=amount + 1)

    msg = await ctx.send(
        embed=make_embed("تم حذف الرسائل", f"تم حذف **{len(deleted) - 1}** رسالة.", SUCCESS_COLOR)
    )
    await asyncio.sleep(4)

    try:
        await msg.delete()
    except Exception:
        pass

    await log_command_action(ctx, "حذف رسائل", color=WARN_COLOR, العدد=str(len(deleted) - 1))


@bot.command(name="helpme", aliases=["help", "مساعدة", "اوامر"])
async def help_command(ctx: commands.Context):
    embed = make_embed("أوامر البوت")
    embed.description = (
        "**إعدادات**\n"
        "`!setuplogs [@roles...]` إنشاء نظام اللوقات\n"
        "`!cleanupbot` حذف كل ما أنشأه البوت من لوقات\n\n"
        "**إدارة**\n"
        "`!mute @member 10m السبب` تايم أوت\n"
        "`!unmute @member السبب` فك التايم أوت\n"
        "`!vmute @member السبب` ميوت صوتي\n"
        "`!vunmute @member السبب` فك ميوت صوتي\n"
        "`!lock [#channel] السبب` قفل روم\n"
        "`!unlock [#channel] السبب` فتح روم\n"
        "`!clear 20` حذف رسائل\n\n"
        "**صيغة الوقت**\n"
        "`10m` = 10 دقائق | `2h` = ساعتين | `1d` = يوم | `1w` = أسبوع"
    )
    await ctx.send(embed=embed)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if ctx.command and hasattr(ctx.command, "on_error"):
        return

    error = getattr(error, "original", error)

    if isinstance(error, commands.MissingPermissions):
        return await ctx.send(embed=make_embed("ما عندك صلاحية تستخدم هذا الأمر", color=ERROR_COLOR))

    if isinstance(error, commands.BotMissingPermissions):
        missing = ", ".join(error.missing_permissions)
        return await ctx.send(embed=make_embed("البوت ناقصه صلاحيات", truncate(missing), ERROR_COLOR))

    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(
            embed=make_embed("الكوماند غلط", f"الكوماند غلط: `{error.param.name}`", ERROR_COLOR)
        )

    if isinstance(error, commands.BadArgument):
        return await ctx.send(embed=make_embed("الكوماند غلط", color=ERROR_COLOR))

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.NoPrivateMessage):
        return await ctx.send(embed=make_embed("هذا الأمر يشتغل داخل السيرفر فقط", color=ERROR_COLOR))

    print(f"Unhandled command error: {error}")
    await ctx.send(
        embed=make_embed(
            "صار خطأ غير متوقع",
            "شيك التيرمنال لو كنت مطور البوت.",
            ERROR_COLOR,
        )
    )


@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild

    
    voice_status_channel_id = 1481850652299886654 
    voice_log_channel_id = 1481859663526887555     

    voice_status_channel = guild.get_channel(voice_status_channel_id)
    voice_log_channel = guild.get_channel(voice_log_channel_id)


    if before.channel is None and after.channel is not None:
        if voice_status_channel:
            embed = discord.Embed(
                title="🎤 دخول روم صوتي",
                color=discord.Color.green()
            )
            embed.add_field(name="العضو", value=member.mention, inline=True)
            embed.add_field(name="دخل إلى", value=after.channel.mention, inline=True)
            embed.set_footer(text=f"User ID: {member.id}")
            await voice_status_channel.send(embed=embed)
        return

   
    if before.channel is not None and after.channel is None:
        moderator = None

        try:
            async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.member_disconnect):
                if entry.target.id == member.id:
                    moderator = entry.user
                    break
        except:
            moderator = None

        # إذا أحد فصله -> يرسل في روم اللوق
        if moderator:
            if voice_log_channel:
                embed = discord.Embed(
                    title="🔌 فصل عضو من الروم الصوتي",
                    color=discord.Color.red()
                )
                embed.add_field(name="العضو", value=member.mention, inline=True)
                embed.add_field(name="تم فصله من", value=before.channel.mention, inline=True)
                embed.add_field(name="بواسطة", value=moderator.mention, inline=False)
                embed.set_footer(text=f"User ID: {member.id}")
                await voice_log_channel.send(embed=embed)
        else:
            # إذا خرج بنفسه -> يرسل في روم الحال
            if voice_status_channel:
                embed = discord.Embed(
                    title="📤 خروج من روم صوتي",
                    color=discord.Color.orange()
                )
                embed.add_field(name="العضو", value=member.mention, inline=True)
                embed.add_field(name="خرج من", value=before.channel.mention, inline=True)
                embed.set_footer(text=f"User ID: {member.id}")
                await voice_status_channel.send(embed=embed)
        return

    if before.channel is not None and after.channel is not None and before.channel != after.channel:
        moderator = None

        try:
            async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.member_move):
                if entry.target.id == member.id:
                    moderator = entry.user
                    break
        except:
            moderator = None

        if voice_log_channel:
            embed = discord.Embed(
                title="🔁 نقل عضو بين الرومات",
                color=discord.Color.blurple()
            )
            embed.add_field(name="العضو", value=member.mention, inline=True)
            embed.add_field(name="من", value=before.channel.mention, inline=True)
            embed.add_field(name="إلى", value=after.channel.mention, inline=True)
            embed.add_field(
                name="بواسطة",
                value=moderator.mention if moderator else "غير معروف",
                inline=False
            )
            embed.set_footer(text=f"User ID: {member.id}")
            await voice_log_channel.send(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("TOKEN not found. Set DISCORD_TOKEN in environment variables.")

    bot.run(TOKEN, log_handler=None)
