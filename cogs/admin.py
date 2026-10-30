"""운영자용 주간 스토리 게시판 슬래시 명령어."""
"""/게시판설정 /시나리오동기화 /게시판갱신"""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from services.weekly_board import WeeklyBoard, save_board_config

log = logging.getLogger(__name__)


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='게시판설정', description='주간 스토리 게시 채널을 설정합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(채널='주간 스토리를 게시할 일반 텍스트 채널')
    async def board_setup(self, interaction: discord.Interaction, 채널: discord.TextChannel):
        me = interaction.guild.me
        perms = 채널.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history):
            await interaction.response.send_message(
                '봇에 채널 보기, 메시지 보내기, 링크 첨부, 메시지 기록 읽기 권한이 필요합니다.', ephemeral=True
            )
            return
        save_board_config(interaction.guild_id, 채널.id)
        await interaction.response.send_message(
            f'게시판을 {채널.mention}(으)로 설정했어요. `/게시판갱신`으로 첫 게시물을 올려 주세요.',
            ephemeral=True,
        )

    @app_commands.command(name='시나리오동기화', description='Google Sheets 스토리와 주간 편성을 동기화합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync_scenarios(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            from services.sheets_loader import sync_sheets
            await asyncio.to_thread(sync_sheets)
            await interaction.followup.send(
                '스토리와 주간 편성 동기화가 완료됐어요. 게시판 반영은 `/게시판갱신`으로 진행하세요.',
                ephemeral=True,
            )
        except Exception as exc:
            log.exception('시나리오 동기화 실패')
            await interaction.followup.send(
                f'동기화 실패: `{type(exc).__name__}`. 서버 로그를 확인해 주세요.', ephemeral=True
            )

    @app_commands.command(name='게시판갱신', description='이번 주 스토리 게시판을 즉시 갱신합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def refresh_board(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        board = self.bot.get_cog('WeeklyBoard')
        if not isinstance(board, WeeklyBoard):
            await interaction.followup.send('WeeklyBoard가 로드되지 않았어요.', ephemeral=True)
            return
        try:
            status, detail = await board.publish(interaction.guild_id, force=True, sync=True)
            await interaction.followup.send(f'**{status}** — {detail}', ephemeral=True)
        except Exception as exc:
            log.exception('게시판 수동 갱신 실패')
            await interaction.followup.send(
                f'게시판 갱신 실패: `{type(exc).__name__}`. 서버 로그를 확인해 주세요.',
                ephemeral=True,
            )

    async def _refresh_after_admin(self, guild_id):
        board = self.bot.get_cog('WeeklyBoard')
        if isinstance(board, WeeklyBoard):
            try:
                await board.publish(guild_id, force=True, sync=False)
            except Exception:
                log.exception('운영자 작업 후 게시판 갱신 실패 (guild=%s)', guild_id)

    @app_commands.command(name='세션목록', description='현재 서버의 세션 목록을 확인합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(종료포함='종료된 세션도 포함합니다. (최신 25개)')
    async def session_list(self, interaction: discord.Interaction, 종료포함: bool = False):
        from services.operator_sessions import sessions
        rows = await asyncio.to_thread(sessions, interaction.guild_id, 종료포함)
        if not rows:
            await interaction.response.send_message('표시할 세션이 없습니다.', ephemeral=True)
            return
        lines = []
        for r in rows:
            thread = f" <#{r['thread_id']}>" if r['thread_id'] else ''
            lines.append(f"`#{r['session_id']}` **{discord.utils.escape_markdown(r['title'])}** · {r['status']} · {r['members']}명{thread}")
        await interaction.response.send_message('**세션 목록 (최신 25개)**\n' + '\n'.join(lines)[:1750], ephemeral=True)

    @app_commands.command(name='스토리상태', description='스토리의 진행 세션과 클리어 횟수를 확인합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(스토리id='Stories 시트의 story_id')
    async def story_state(self, interaction: discord.Interaction, 스토리id: str):
        from services.operator_sessions import story_status
        try:
            story, cleared, active = await asyncio.to_thread(story_status, 스토리id.strip())
            limit = story['clear_limit'] if story['clear_limit'] is not None else '무제한'
            running = ', '.join(f"#{r['session_id']} ({r['status']})" for r in active) or '없음'
            await interaction.response.send_message(
                f"**{story['title']}** (`{story['story_id']}`)\n"
                f"유형: {story['availability']} · 활성: {bool(story['enabled'])}\n"
                f"클리어: **{cleared}/{limit}** · 진행 중: {running}", ephemeral=True)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(name='세션강제종료', description='멈춘 세션을 클리어 처리 없이 강제 종료합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(세션id='/세션목록에서 확인한 번호', 확인='실행하려면 True로 지정')
    async def force_end(self, interaction: discord.Interaction, 세션id: int, 확인: bool):
        if not 확인:
            await interaction.response.send_message('강제 종료를 취소했습니다. 실행하려면 확인=True로 지정하세요.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        from services.operator_sessions import force_finish
        try:
            session = await asyncio.to_thread(force_finish, interaction.guild_id, 세션id, interaction.user.id)
            await self._refresh_after_admin(interaction.guild_id)
            await interaction.followup.send(
                f"세션 `#{세션id}`을 강제 종료했습니다. (이전 상태: {session['status']})\n"
                '클리어 횟수는 증가하지 않으며 캐릭터 진행 데이터는 되돌리지 않습니다.', ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @app_commands.command(name='스토리클리어횟수수정', description='스토리의 누적 클리어 횟수를 운영자가 변경합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(스토리id='Stories 시트의 story_id', 횟수='변경할 누적 클리어 횟수 (0 이상)')
    async def edit_clears(self, interaction: discord.Interaction, 스토리id: str, 횟수: app_commands.Range[int, 0, None]):
        await interaction.response.defer(ephemeral=True)
        from services.operator_sessions import set_clear_count
        try:
            before = await asyncio.to_thread(set_clear_count, 스토리id.strip(), 횟수, interaction.user.id, interaction.guild_id)
            await self._refresh_after_admin(interaction.guild_id)
            await interaction.followup.send(f'`{스토리id}` 클리어 횟수: **{before} → {횟수}회**', ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    async def cog_app_command_error(self, interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            msg = '서버 관리 권한이 필요합니다.'
        else:
            log.error('운영자 명령 오류', exc_info=(type(error), error, error.__traceback__))
            msg = '명령 처리 중 오류가 발생했어요. 서버 로그를 확인해 주세요.'
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
