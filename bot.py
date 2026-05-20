from __future__ import annotations

import asyncio
import os
import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks
from aiohttp import web
from dotenv import load_dotenv

import storage
import detector

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------- Bot setup ----------

intents = discord.Intents.default()
intents.message_content = True


class ReminderBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await storage.init_db()
        await self.tree.sync()
        self.check_reminders.start()
        log.info("Bot ready. Reminder loop started.")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        has_intent, target_date, _ = detector.detect_reminder_intent(message.content)
        if not has_intent:
            return

        default_time = await storage.get_default_time(message.guild.id)
        remind_at = datetime.combine(
            target_date,
            datetime.strptime(default_time, "%H:%M").time(),
        )

        message_link = (
            f"https://discord.com/channels/{message.guild.id}"
            f"/{message.channel.id}/{message.id}"
        )

        reminder_id = await storage.add_reminder(
            guild_id=message.guild.id,
            user_id=message.author.id,
            message_id=message.id,
            message_link=message_link,
            task_description=message.content,
            remind_at=remind_at,
        )

        await message.add_reaction("⏰")
        reply = await message.reply(
            f"✨ リマインド、ちゃんと覚えたよ〜！\n"
            f"🗓 **{target_date.strftime('%m月%d日')} {default_time}** に教えてあげる！\n"
            f"🔖 ID: `{reminder_id}`　キャンセルしたいときは `/cancel` でどうぞ",
            mention_author=False,
        )
        await reply.delete(delay=15)

    # ---------- Reminder dispatch loop ----------

    @tasks.loop(minutes=1)
    async def check_reminders(self) -> None:
        now = datetime.now()
        due = await storage.get_due_reminders(now)
        for r in due:
            await self._dispatch_reminder(r)

    @check_reminders.before_loop
    async def _before_check(self) -> None:
        await self.wait_until_ready()

    async def _dispatch_reminder(self, r, *, dry_run: bool = False) -> None:
        guild = self.get_guild(r["guild_id"])
        if not guild:
            return

        channel = await self._resolve_reminder_channel(guild)
        if not channel:
            log.warning("No reminder channel found for guild %s", guild.id)
            return

        member = guild.get_member(r["user_id"])
        mention = member.mention if member else f"<@{r['user_id']}>"

        remind_at = r["remind_at"]
        embed = discord.Embed(
            title="🔔 やることあるよ〜！",
            description=f">>> {r['task_description']}",
            color=0xFF6B9D,
            timestamp=remind_at,
        )
        embed.add_field(name="👤 設定した人", value=mention, inline=True)
        embed.add_field(
            name="💬 元メッセージ",
            value=f"[ここからジャンプ！]({r['message_link']})",
            inline=True,
        )
        if dry_run:
            embed.set_footer(text=f"🧪 これはテスト送信です　ID: {r['id']}")
        else:
            embed.set_footer(text=f"ID: {r['id']}")

        await channel.send(f"@everyone {mention}", embed=embed)
        if not dry_run:
            await storage.mark_notified(r["id"])
        log.info("Dispatched reminder #%s to guild %s (dry_run=%s)", r["id"], guild.id, dry_run)

    async def _resolve_reminder_channel(
        self, guild: discord.Guild
    ) -> Optional[discord.TextChannel]:
        config = await storage.get_guild_config(guild.id)
        if config and config["reminder_channel_id"]:
            ch = guild.get_channel(config["reminder_channel_id"])
            if isinstance(ch, discord.TextChannel):
                return ch

        # 名前で fallback 検索
        for name in ("reminders", "reminder", "リマインド", "リマインダー"):
            for ch in guild.text_channels:
                if ch.name.lower() == name:
                    return ch

        return None


client = ReminderBot()

# ---------- UI Components ----------


class CancelSelect(discord.ui.Select):
    def __init__(self, rows: list) -> None:
        options = []
        for r in rows[:25]:  # Discord の Select 上限は 25 件
            remind_at = r["remind_at"]
            label = f"{remind_at.strftime('%m/%d %H:%M')}　{r['task_description'][:40]}"
            desc = r["task_description"][:100]
            options.append(
                discord.SelectOption(label=label, value=str(r["id"]), description=desc)
            )
        super().__init__(
            placeholder="キャンセルするリマインドを選んでね（複数選択OK）",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        deleted = []
        for rid in self.values:
            if await storage.delete_reminder(int(rid), interaction.guild.id):
                deleted.append(rid)

        if deleted:
            ids = " ".join(f"`{rid}`" for rid in deleted)
            await interaction.response.edit_message(
                content=f"🗑 {len(deleted)}件のリマインドを消したよ！お疲れさまでした ✨\n（ID: {ids}）",
                view=None,
                embed=None,
            )
        else:
            await interaction.response.edit_message(
                content="😢 削除できなかった…もう一度試してみてね！",
                view=None,
                embed=None,
            )


class CancelView(discord.ui.View):
    def __init__(self, rows: list) -> None:
        super().__init__(timeout=60)
        self.add_item(CancelSelect(rows))

    async def on_timeout(self) -> None:
        # タイムアウト時は何もしない（メッセージはそのまま残る）
        pass


# ---------- Slash commands ----------


@client.tree.command(name="remind", description="リマインドを設定します（例: 明日PRを出す　来週ミーティング準備 14:00）")
@app_commands.describe(
    text="タスク内容（日付・時刻も一緒に書いてね。例: 明日PRを出す / 来週ミーティング準備 14:00）",
)
async def cmd_remind(
    interaction: discord.Interaction,
    text: str,
) -> None:
    default_time = await storage.get_default_time(interaction.guild.id)

    target_date = detector.extract_date_from_text(text)
    if target_date is None:
        await interaction.response.send_message(
            "😥 日付がわからなかった！「明日」「今週中」「05/25」みたいな日付キーワードを含めてみてね\n"
            "例: `/remind 明日PRを出す` `/remind 今週中にデプロイ` `/remind 来週ミーティング 14:00`",
            ephemeral=True,
        )
        return

    time_str = detector.extract_time_from_text(text) or default_time
    remind_at = datetime.combine(
        target_date,
        datetime.strptime(time_str, "%H:%M").time(),
    )

    if remind_at < datetime.now():
        await interaction.response.send_message(
            "😅 その日時はもう過ぎてるよ〜！", ephemeral=True
        )
        return

    # 先にメッセージを送信してリンクを確定させる
    await interaction.response.send_message(
        f"✨ リマインドをセットしたよ！\n"
        f"📋 **{text}**\n"
        f"🗓 `{remind_at.strftime('%Y年%m月%d日 %H:%M')}` に通知するね",
    )
    msg = await interaction.original_response()
    message_link = (
        f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}/{msg.id}"
    )

    reminder_id = await storage.add_reminder(
        guild_id=interaction.guild.id,
        user_id=interaction.user.id,
        message_id=msg.id,
        message_link=message_link,
        task_description=text,
        remind_at=remind_at,
    )

    await msg.edit(
        content=f"✨ リマインドをセットしたよ！\n"
                f"📋 **{text}**\n"
                f"🗓 `{remind_at.strftime('%Y年%m月%d日 %H:%M')}` に通知するね\n"
                f"🔖 ID: `{reminder_id}`　キャンセルは `/cancel`",
    )


@client.tree.command(name="reminders", description="現在設定中のリマインド一覧を表示します")
async def cmd_reminders(interaction: discord.Interaction) -> None:
    rows = await storage.list_reminders(interaction.guild.id)
    if not rows:
        await interaction.response.send_message(
            "📭 いまはリマインドが何も設定されていないよ！\n"
            "チャットで「今日やる」って言うか、`/remind` で追加してみてね ✨",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="📋 リマインド一覧",
        description=f"現在 **{len(rows)}件** のリマインドが待機中だよ！",
        color=0x87CEEB,
    )
    for r in rows[:15]:
        remind_at = r["remind_at"]
        member = interaction.guild.get_member(r["user_id"])
        name = member.display_name if member else f"User {r['user_id']}"
        task_preview = r["task_description"][:50] + (
            "…" if len(r["task_description"]) > 50 else ""
        )
        embed.add_field(
            name=f"🔔 {remind_at.strftime('%m/%d (%a) %H:%M')}　｜　{name}",
            value=f"`ID: {r['id']}`　{task_preview}",
            inline=False,
        )
    if len(rows) > 15:
        embed.set_footer(text=f"※ 多すぎるので上位15件だけ表示しています")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name="cancel", description="リマインドを選んでキャンセルします")
async def cmd_cancel(interaction: discord.Interaction) -> None:
    rows = await storage.list_reminders(interaction.guild.id)
    if not rows:
        await interaction.response.send_message(
            "📭 キャンセルできるリマインドがないよ！\n"
            "チャットで「今日やる」か `/remind` で追加してみてね ✨",
            ephemeral=True,
        )
        return

    view = CancelView(rows)
    await interaction.response.send_message(
        "🗑 どのリマインドを消す？　複数まとめて選択もできるよ！",
        view=view,
        ephemeral=True,
    )


@client.tree.command(name="setdefaulttime", description="自動検知リマインドのデフォルト時刻を変更します（管理者のみ）")
@app_commands.describe(time="デフォルト時刻（HH:MM 形式、例: 09:00 / 22:00）")
@app_commands.checks.has_permissions(manage_channels=True)
async def cmd_set_default_time(interaction: discord.Interaction, time: str) -> None:
    import re
    if not re.fullmatch(r"\d{1,2}:\d{2}", time):
        await interaction.response.send_message(
            "😥 時刻は `HH:MM` 形式で入力してね！　例: `09:00` `22:30`",
            ephemeral=True,
        )
        return

    h, m = map(int, time.split(":"))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await interaction.response.send_message(
            "😥 時刻の範囲がおかしいよ！　0:00 〜 23:59 で指定してね",
            ephemeral=True,
        )
        return

    normalized = f"{h:02d}:{m:02d}"
    await storage.set_default_time(interaction.guild.id, normalized)
    await interaction.response.send_message(
        f"✅ デフォルトのリマインド時刻を **{normalized}** に変更したよ！\n"
        f"これ以降「今日やる」などで自動設定されるリマインドは {normalized} に通知されるよ 🕐",
        ephemeral=True,
    )


@cmd_set_default_time.error
async def _set_default_time_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ チャンネル管理権限が必要です。", ephemeral=True
        )


@client.tree.command(
    name="setreminderchannel",
    description="リマインドを送信するチャンネルを設定します（管理者のみ）",
)
@app_commands.describe(channel="リマインド通知先チャンネル")
@app_commands.checks.has_permissions(manage_channels=True)
async def cmd_set_channel(
    interaction: discord.Interaction, channel: discord.TextChannel
) -> None:
    await storage.set_reminder_channel(interaction.guild.id, channel.id)
    await interaction.response.send_message(
        f"✅ リマインドチャンネルを {channel.mention} に設定しました。", ephemeral=True
    )


@client.tree.command(name="testremind", description="リマインド通知のテスト送信をします")
@app_commands.describe(reminder_id="テスト送信するリマインドの ID（省略すると架空のサンプルを送信）")
async def cmd_testremind(
    interaction: discord.Interaction,
    reminder_id: Optional[int] = None,
) -> None:
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    channel = await client._resolve_reminder_channel(guild)
    if not channel:
        await interaction.followup.send(
            "😥 リマインドチャンネルが設定されていないよ！\n"
            "`/setreminderchannel #チャンネル名` で設定してね。",
            ephemeral=True,
        )
        return

    if reminder_id is not None:
        rows = await storage.list_reminders(guild.id)
        target = next((r for r in rows if r["id"] == reminder_id), None)
        if target is None:
            await interaction.followup.send(
                f"😢 ID `{reminder_id}` のリマインドが見つからなかった…\n"
                "`/reminders` で一覧を確認してみてね！",
                ephemeral=True,
            )
            return
        await client._dispatch_reminder(target, dry_run=True)
        await interaction.followup.send(
            f"🧪 ID `{reminder_id}` のテスト送信を {channel.mention} に送ったよ！\n"
            f"（本番のリマインドはそのまま残っているよ）",
            ephemeral=True,
        )
    else:
        # 架空のサンプルを送る
        embed = discord.Embed(
            title="🔔 やることあるよ〜！",
            description=">>> これはサンプルのリマインドだよ！\nここにタスクの内容が表示されるよ ✨",
            color=0xFF6B9D,
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 設定した人", value=interaction.user.mention, inline=True)
        embed.add_field(name="💬 元メッセージ", value="[ここからジャンプ！](https://discord.com)", inline=True)
        embed.set_footer(text="🧪 これはテスト送信です　ID: 0")
        await channel.send(f"@everyone {interaction.user.mention}", embed=embed)
        await interaction.followup.send(
            f"🧪 サンプルのテスト通知を {channel.mention} に送ったよ！確認してみてね！",
            ephemeral=True,
        )


@cmd_set_channel.error
async def _set_channel_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ チャンネル管理権限が必要です。", ephemeral=True
        )


# ---------- Keep-alive Web server (Render 無料枠のスリープ対策) ----------

async def _health(request: web.Request) -> web.Response:
    return web.Response(text="Bot is alive!")

async def _start_web_server() -> None:
    app = web.Application()
    app.router.add_get("/", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Web server started on port %s", port)

# ---------- Entrypoint ----------

async def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN が .env に設定されていません。")
    await _start_web_server()
    async with client:
        await client.start(token)

if __name__ == "__main__":
    asyncio.run(main())
