"""/게이트입장, /참가, /게이트정보. Python 3.15 / discord.py 2.x."""
from services.shop import selected as _selected_character
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from services import game_sessions as db

log = logging.getLogger(__name__)


async def update_participants_embed(bot, channel, session):
    participants = await asyncio.to_thread(db.members, session['session_id'])
    lines = [f"<@{m['user_id']}> — {discord.utils.escape_markdown(str(m['character_name']))}"
             for m in participants if m.get('character_name')]
    listing = '\n'.join(lines) or '아직 참가한 캐릭터가 없습니다.'
    if len(listing) > 1024:
        listing = listing[:1019] + '…'
    async for message in channel.history(limit=100, oldest_first=True):
        if message.author.id != bot.user.id or not message.embeds:
            continue
        old = message.embeds[0]
        if old.title != f"🎲 {session['game_name']}":
            continue
        if not any(f.name == '참가 방법' for f in old.fields):
            continue
        updated = old.copy()
        index = next((i for i, f in enumerate(updated.fields) if f.name == '참가 캐릭터'), None)
        if index is None:
            updated.add_field(name='참가 캐릭터', value=listing, inline=False)
        else:
            updated.set_field_at(index, name='참가 캐릭터', value=listing, inline=False)
        await message.edit(embed=updated)
        break


async def _active_name(interaction):
    try:
        return _selected_character(interaction.guild_id, interaction.user.id)['name']
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return None


class StoryPicker(discord.ui.Select):
    def __init__(self, stories, game_name, owner_id):
        options = [discord.SelectOption(
            label=str(s['title'])[:100], value=s['story_id'],
            description=(('상시' if s['availability']=='permanent' else '주간') + ' · ' + ('완료' if s['clear_limit'] is not None and s['clear_count']>=s['clear_limit'] else '진행 중' if s['running_count'] else '입장 가능') + ' · ' + str(s['genre'] or '스토리'))[:100]
        ) for s in stories]
        super().__init__(placeholder='플레이할 스토리를 선택하세요', options=options)
        self.game_name = game_name
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('게임 생성자만 선택할 수 있어요.', ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel) or not interaction.guild:
            await interaction.response.send_message('일반 텍스트 채널에서만 게임을 만들 수 있어요.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.view.stop()
        for item in self.view.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self.view)
        except discord.HTTPException:
            pass
        session_id = None
        try:
            session_id, story = await asyncio.to_thread(
                db.create_session, interaction.guild.id, interaction.channel.id,
                interaction.user.id, self.values[0], self.game_name)
            thread = await interaction.channel.create_thread(
                name=f'🎲 {self.game_name}'[:100],
                type=discord.ChannelType.public_thread,
                auto_archive_duration=1440,
                reason=f'텍스트 RPG 게임 #{session_id}'
            )
            await asyncio.to_thread(db.set_thread, session_id, thread.id)
            embed = discord.Embed(title=f'🎲 {self.game_name}',
                                  description=story['description'] or '스토리 소개 없음',
                                  color=discord.Color.blurple())
            embed.add_field(name='스토리', value=story['title'], inline=True)
            embed.add_field(name='세션', value=f'`{session_id}`', inline=True)
            embed.add_field(name='참가 캐릭터', value='아직 참가한 캐릭터가 없습니다.', inline=False)
            embed.add_field(name='참가 방법', value='이 스레드에서 `/참가`을 사용하세요. 생성자도 참가해야 합니다.', inline=False)
            embed.set_footer(text='참가 후 /진행으로 스토리를 시작하세요.')
            await thread.send(embed=embed)
            await interaction.followup.send(f'게임 스레드가 생성됐어요: {thread.mention}', ephemeral=True)
            board = interaction.client.get_cog('WeeklyBoard')
            if board is not None:
                try:
                    await board.publish(interaction.guild.id, force=True, sync=False)
                except Exception:
                    log.exception('세션 시작 후 게시판 갱신 실패 (guild=%s)', interaction.guild.id)
        except Exception as exc:
            if session_id is not None:
                await asyncio.to_thread(db.remove_session, session_id)
            await interaction.followup.send(f'게임 생성에 실패했어요: {type(exc).__name__}: {exc}', ephemeral=True)


class StoryPickerView(discord.ui.View):
    def __init__(self, stories, name, owner_id):
        super().__init__(timeout=180)
        self.add_item(StoryPicker(stories, name, owner_id))


class Game(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='의뢰수락', description='의뢰 진행 스레드를 생성합니다.')
    @app_commands.describe(게임이름='생성할 게임 이름')
    @app_commands.guild_only()
    async def game_start(self, interaction: discord.Interaction, 게임이름: str):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message('일반 텍스트 채널에서 실행해 주세요.', ephemeral=True)
            return
        name = 게임이름.strip()
        if not 1 <= len(name) <= 80:
            await interaction.response.send_message('게임 이름은 1~80자로 입력해 주세요.', ephemeral=True)
            return
        week, stories = await asyncio.to_thread(db.available_stories)
        if not stories:
            await interaction.response.send_message('이번 주 편성 또는 상시 플레이 가능한 스토리가 없습니다.', ephemeral=True)
            return
        view = StoryPickerView(stories[:25], name, interaction.user.id)
        await interaction.response.send_message('입장할 게이트를 선택해 주세요.', view=view, ephemeral=True)

    @app_commands.command(name='참가', description='해당 스레드 의뢰에 합류합니다.')
    @app_commands.guild_only()
    async def game_join(self, interaction: discord.Interaction):
        캐릭터이름 = await _active_name(interaction)
        if 캐릭터이름 is None: return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message('스레드에서 사용해 주세요.', ephemeral=True)
            return
        session = await asyncio.to_thread(db.get_by_thread, interaction.channel.id)
        if not session:
            await interaction.response.send_message('등록된 게이트 스레드가 아닙니다.', ephemeral=True)
            return
        from services.characters import join_with_character
        try:
            character = await asyncio.to_thread(join_with_character,
                session['session_id'], interaction.user.id, 캐릭터이름)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"{interaction.user.mention}님이 **{discord.utils.escape_markdown(character['name'])}** 캐릭터로 참가했습니다!")
        # 첫 번째 스레드 안내 임베드의 참가 캐릭터 목록을 갱신한다.
        # 참가 성공 자체는 임베드 편집 실패와 무관하게 유지한다.
        try:
            await update_participants_embed(self.bot, interaction.channel, session)
        except (discord.HTTPException, discord.Forbidden, AttributeError):
            log.exception('참가 캐릭터 안내 임베드 갱신 실패 (session=%s)', session['session_id'])

    @app_commands.command(name='퇴장', description='현재 게이트에서 참가 캐릭터를 퇴장시킵니다.')
    @app_commands.guild_only()
    async def game_leave(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message('게임 스레드에서 사용해 주세요.', ephemeral=True)
            return
        session = await asyncio.to_thread(db.get_by_thread, interaction.channel.id)
        if not session:
            await interaction.response.send_message('등록된 게이트 스레드가 아닙니다.', ephemeral=True)
            return
        try:
            result = await asyncio.to_thread(db.leave_session, session['session_id'], interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        name = discord.utils.escape_markdown(result['name'])
        message = f'🚪 **{name}** 님이 게이트에서 퇴장했습니다.\n-# 남은 참가자: {result["remaining"]}명'
        if result['status'] == 'finished':
            message += '\n-# 마지막 참가자가 퇴장해 세션이 종료되었습니다.'
        elif result['revote']:
            message += '\n-# 행동 가능한 담당자가 없어 이전 투표를 초기화하고 재투표를 시작합니다.'
        await interaction.response.send_message(message)
        try:
            await update_participants_embed(self.bot, interaction.channel, session)
        except (discord.HTTPException, discord.Forbidden, AttributeError):
            log.exception('퇴장 후 참가자 임베드 갱신 실패')
        if result['status'] == 'finished' and interaction.guild:
            board = self.bot.get_cog('WeeklyBoard')
            if board:
                try:
                    await board.publish(interaction.guild.id, force=True, sync=False)
                except Exception:
                    log.exception('퇴장 종료 후 게시판 갱신 실패')

    @app_commands.command(name='의뢰정보', description='현재 의뢰의 정보와 참가자를 확인합니다.')
    @app_commands.guild_only()
    async def game_info(self, interaction: discord.Interaction):
        session = await asyncio.to_thread(db.get_by_thread, interaction.channel.id) if isinstance(interaction.channel, discord.Thread) else None
        if not session:
            await interaction.response.send_message('게임 스레드 안에서 사용해 주세요.', ephemeral=True)
            return
        members = await asyncio.to_thread(db.members, session['session_id'])
        embed = discord.Embed(title=session['game_name'], color=discord.Color.green())
        embed.add_field(name='스토리 ID', value=session['story_id'])
        embed.add_field(name='상태', value=session['status'])
        embed.add_field(name='시작 장면', value=session['current_scene_id'] or '없음')
        embed.add_field(name='참가자', value='\n'.join(f"<@{m['user_id']}> — {discord.utils.escape_markdown(m['character_name'] or '캐릭터 미선택')}" for m in members) or '없음', inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Game(bot))
