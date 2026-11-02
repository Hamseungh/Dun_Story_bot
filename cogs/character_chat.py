"""Character RP webhook chat, scoped to administrator-approved channels."""
"""/이미지등록 /헌터선택 /캐릭터채팅설정"""

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands
from database import DB_PATH

log = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def connect():
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def owned_character(user_id, name):
    with connect() as db:
        return db.execute('SELECT character_id,name FROM characters WHERE user_id=? AND name=? COLLATE NOCASE',
                          (str(user_id), name.strip())).fetchone()


def save_avatar(character_id, image, mime):
    with connect() as db:
        db.execute('INSERT INTO character_chat_avatars(character_id,avatar_data,mime_type) VALUES (?,?,?) '
                   'ON CONFLICT(character_id) DO UPDATE SET avatar_data=excluded.avatar_data,mime_type=excluded.mime_type',
                   (character_id, image, mime))


def select_character(guild_id, user_id, character_id):
    with connect() as db:
        db.execute('INSERT INTO character_chat_selection(guild_id,user_id,character_id) VALUES (?,?,?) '
                   'ON CONFLICT(guild_id,user_id) DO UPDATE SET character_id=excluded.character_id',
                   (str(guild_id), str(user_id), character_id))


def configure_channel(guild_id, channel_id, enabled):
    with connect() as db:
        if enabled:
            db.execute('INSERT OR IGNORE INTO character_chat_channels VALUES (?,?)', (str(guild_id),str(channel_id)))
        else:
            db.execute('DELETE FROM character_chat_channels WHERE guild_id=? AND channel_id=?',
                       (str(guild_id),str(channel_id)))


def get_chat_identity(guild_id, channel_id, user_id):
    with connect() as db:
        return db.execute("""SELECT c.name,c.character_id,a.avatar_data
            FROM character_chat_channels ch
            JOIN character_chat_selection s ON s.guild_id=ch.guild_id
            JOIN characters c ON c.character_id=s.character_id AND c.user_id=s.user_id
            LEFT JOIN character_chat_avatars a ON a.character_id=c.character_id
            WHERE ch.guild_id=? AND ch.channel_id=? AND s.user_id=?""",
            (str(guild_id),str(channel_id),str(user_id))).fetchone()


class CharacterChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._locks = {}

    async def character_autocomplete(self, interaction: discord.Interaction, current: str):
        def lookup():
            with connect() as db:
                return [r['name'] for r in db.execute('SELECT name FROM characters WHERE user_id=? ORDER BY name',
                                                       (str(interaction.user.id),)).fetchall()]
        names = await asyncio.to_thread(lookup)
        return [app_commands.Choice(name=n[:100],value=n) for n in names if current.casefold() in n.casefold()][:25]

    @app_commands.command(name='이미지등록',description='내 캐릭터의 채팅 프로필 이미지를 첨부 파일로 등록합니다.')
    @app_commands.describe(캐릭터_이름='등록된 내 캐릭터',이미지='PNG/JPEG/WEBP/GIF 이미지 (2MB 이하)')
    @app_commands.autocomplete(캐릭터_이름=character_autocomplete)
    async def register_image(self, interaction: discord.Interaction, 캐릭터_이름: str, 이미지: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        char = await asyncio.to_thread(owned_character, interaction.user.id, 캐릭터_이름)
        if not char:
            await interaction.followup.send('본인이 소유한 캐릭터를 찾을 수 없어요.',ephemeral=True)
            return
        mime = 이미지.content_type or ''
        if mime not in ('image/png','image/jpeg','image/webp','image/gif') or 이미지.size > MAX_IMAGE_BYTES:
            await interaction.followup.send('PNG/JPEG/WEBP/GIF 이미지(2MB 이하)만 등록할 수 있어요.',ephemeral=True)
            return
        try:
            data = await 이미지.read()
            if len(data) > MAX_IMAGE_BYTES or not data:
                raise ValueError('이미지 크기가 올바르지 않습니다.')
            await asyncio.to_thread(save_avatar,char['character_id'],data,mime)
        except (discord.HTTPException, ValueError, sqlite3.Error):
            log.exception('프로필 이미지 등록 실패')
            await interaction.followup.send('이미지를 저장하지 못했어요. 다시 시도해 주세요.',ephemeral=True)
            return
        await interaction.followup.send(f'**{char["name"]}**의 채팅 프로필 이미지를 등록했어요.',ephemeral=True)

    @app_commands.command(name='헌터선택',description='이 서버에서 채팅할 캐릭터를 선택합니다.')
    @app_commands.guild_only()
    @app_commands.describe(캐릭터_이름='등록된 내 캐릭터')
    @app_commands.autocomplete(캐릭터_이름=character_autocomplete)
    async def choose(self, interaction: discord.Interaction, 캐릭터_이름: str):
        char = await asyncio.to_thread(owned_character,interaction.user.id,캐릭터_이름)
        if not char:
            await interaction.response.send_message('본인이 소유한 캐릭터를 찾을 수 없어요.',ephemeral=True)
            return
        await asyncio.to_thread(select_character,interaction.guild_id,interaction.user.id,char['character_id'])
        await interaction.response.send_message(f'이 서버의 채팅 캐릭터를 **{char["name"]}**(으)로 선택했어요.',ephemeral=True)

    @app_commands.command(name='캐릭터채팅설정',description='지정 채널의 캐릭터 채팅을 켜거나 끕니다. (관리자)')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(채널='적용할 텍스트 채널',활성화='참: 활성화 / 거짓: 해제')
    async def setup_channel(self, interaction: discord.Interaction, 채널: discord.TextChannel, 활성화: bool):
        me = interaction.guild.me
        perms = 채널.permissions_for(me)
        if 활성화 and not all((perms.view_channel,perms.send_messages,perms.manage_messages,
                                perms.manage_webhooks,perms.read_message_history)):
            await interaction.response.send_message('봇에 채널 보기, 메시지 보내기/관리, 웹훅 관리, 메시지 기록 읽기 권한이 필요해요.',ephemeral=True)
            return
        await asyncio.to_thread(configure_channel,interaction.guild_id,채널.id,활성화)
        await interaction.response.send_message(f'{채널.mention} 캐릭터 채팅을 {"활성화" if 활성화 else "해제"}했어요.',ephemeral=True)

    @commands.Cog.listener('on_message')
    async def relay(self, message: discord.Message):
        if not message.guild or message.author.bot or message.webhook_id or not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        if not message.content and not message.attachments:
            return
        # 스레드는 부모 텍스트 채널의 캐릭터 채팅 설정을 상속합니다.
        parent_channel = message.channel.parent if isinstance(message.channel, discord.Thread) else message.channel
        if not isinstance(parent_channel, discord.TextChannel):
            return
        identity = await asyncio.to_thread(get_chat_identity,message.guild.id,parent_channel.id,message.author.id)
        if not identity:
            return
        key = (message.guild.id,message.channel.id)
        lock = self._locks.setdefault(key,asyncio.Lock())
        async with lock:
            try:
                hooks = await parent_channel.webhooks()
                hook = next((h for h in hooks if h.user and h.user.id == self.bot.user.id and h.name == 'Character Chat'),None)
                if hook is None:
                    hook = await parent_channel.create_webhook(name='Character Chat',reason='캐릭터 채팅')
                files = []
                for attachment in message.attachments:
                    files.append(await attachment.to_file())
                if identity['avatar_data']:
                    # Editing the webhook's own avatar supports uploaded image bytes.
                    # Channel lock prevents interleaving of avatar/name changes.
                    await hook.edit(name='Character Chat', avatar=identity['avatar_data'])
                else:
                    await hook.edit(name='Character Chat', avatar=None)
                content = message.content or None
                if content and len(content) > 2000:
                    return
                await hook.send(content=content,username=identity['name'][:80],
                                files=files,allowed_mentions=discord.AllowedMentions.none(),wait=True,
                                **({'thread': message.channel} if isinstance(message.channel, discord.Thread) else {}))
                await message.delete()
            except (discord.HTTPException,discord.Forbidden):
                log.exception('캐릭터 채팅 전송 실패: guild=%s channel=%s',message.guild.id,message.channel.id)



async def setup(bot):
    await bot.add_cog(CharacterChat(bot))
