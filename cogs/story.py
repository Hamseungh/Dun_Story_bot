"""Slash command story progression; /투표 automatically advances on unanimous participation."""
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from services import game_sessions
from services import story_engine
from services.story_sync import sync

log = logging.getLogger(__name__)


def scene_embed(view):
    scene=view['scene']; embed=discord.Embed(title=scene['title'],description=scene['content'][:4000],color=discord.Color.blurple())
    if view['choices']:
        lines=[]
        for x in view['choices']:
            label = discord.utils.escape_markdown(x['text'])
            if x.get('condition_text'):
                label += ' (' + discord.utils.escape_markdown(x['condition_text']) + ')'
            number = x['display_order']
            if x.get('available', True):
                lines.append(f"**{number}.** {label}")
            else:
                lines.append(f"~~{number}. {label}~~ 🔒")
        embed.add_field(name='선택지',value='> '+'\n> '.join(lines)[:1024],inline=False)
        embed.set_footer(text=f"/투표 선택지번호 · 투표 {view['voted']}/{view['members']}명")
    elif scene['scene_type']=='ending': embed.set_footer(text='엔딩입니다. /진행으로 게임을 종료하세요.')
    else: embed.set_footer(text='선택지가 없는 장면입니다. /진행으로 다음 장면으로 이동하세요.')
    if view.get('warning'):
        embed.add_field(name='⚠️ 행동 불가', value=view['warning'][:1024], inline=False)
    return embed


def effect_lines(effects):
    """Show only actually changed, player-facing effects (never internal flags)."""
    labels = {'hp': '체력', 'mental': '정신력', 'gold': '골드', 'exp': '경험치'}
    lines = []
    for effect in effects:
        kind = effect['type']
        if kind == 'flag_set':
            continue
        delta = effect.get('actual_delta')
        if delta is None or delta == 0:
            continue
        name = discord.utils.escape_markdown(str(effect.get('character', '캐릭터')))
        if kind in ('item_add', 'item_remove'):
            item = discord.utils.escape_markdown(str(effect.get('item_name') or effect['target']))
            lines.append(f"{name}: **{item}** {abs(delta)}개 {'획득' if delta > 0 else '상실'}")
        elif kind in labels:
            lines.append(f"{name}: {labels[kind]} {'+' if delta > 0 else ''}{delta}")
    return lines


async def send_effects(channel, effects):
    lines = effect_lines(effects)
    if not lines:
        return
    # Stay within Discord's 2000-character message limit.
    message = '✨ **적용된 효과**'
    for line in lines:
        if len(message) + len(line) + 1 > 1900:
            await channel.send(message)
            message = '✨ **적용된 효과 (계속)**'
        message += '\n`' + line + '`'
    await channel.send(message)


class Story(commands.Cog):
    def __init__(self,bot): self.bot=bot

    async def refresh_board(self, interaction):
        """Best-effort edit of the existing board; never roll back a completed session."""
        if not interaction.guild:
            return
        board = self.bot.get_cog('WeeklyBoard')
        if board is None:
            log.warning('게시판 자동 갱신 생략: WeeklyBoard 코그가 로드되지 않았습니다.')
            return
        try:
            await board.publish(interaction.guild.id, force=True, sync=False)
        except Exception:
            log.exception('세션 종료 후 게시판 자동 갱신 실패 (guild=%s)', interaction.guild.id)

    async def session(self,interaction):
        if not isinstance(interaction.channel,discord.Thread): raise ValueError('게임 스레드에서 사용해 주세요.')
        session=await asyncio.to_thread(game_sessions.get_by_thread,interaction.channel.id)
        if not session: raise ValueError('등록된 게임 스레드가 아닙니다.')
        return session

    @app_commands.command(name='진행',description='첫 장면 시작, 선택지 없는 장면 진행, 엔딩 확정')
    @app_commands.guild_only()
    async def progress(self,interaction:discord.Interaction):
        await interaction.response.defer()
        try:
            session=await self.session(interaction)
            result=await asyncio.to_thread(story_engine.progress,session['session_id'],interaction.user.id)
            if result['kind']=='finished':
                await interaction.followup.send('🏁 엔딩을 확정하고 게임을 종료했어요.')
                await self.refresh_board(interaction)
            else: await interaction.followup.send(embed=scene_embed(result['view']))
        except ValueError as exc: await interaction.followup.send(str(exc),ephemeral=True)

    @app_commands.command(name='투표',description='선택지 번호로 투표합니다. 전원 투표 시 자동 진행합니다.')
    @app_commands.describe(선택지번호='장면에 표시된 선택지 번호')
    @app_commands.guild_only()
    async def vote(self,interaction:discord.Interaction,선택지번호:int):
        await interaction.response.defer(ephemeral=True)
        try:
            session=await self.session(interaction)
            result=await asyncio.to_thread(story_engine.vote,session['session_id'],interaction.user.id,선택지번호)
            if result['kind']=='pending':
                await interaction.followup.send(f"투표 완료! ({result['voted']}/{result['members']}명) 전원 투표 전까지 변경할 수 있어요.",ephemeral=True)
            elif result['kind']=='tie':
                await interaction.followup.send('투표 완료. 동률이므로 재투표를 시작했어요.',ephemeral=True)
                await interaction.channel.send('⚖️ **동률 발생!**\n -# 전원 `/투표`로 다시 선택해 주세요.')
            elif result['kind']=='actor_pending':
                await interaction.followup.send('투표가 확정됐어요. 행동 담당자의 수락을 기다립니다.',ephemeral=True)
                people=', '.join(discord.utils.escape_markdown(str(x['name'])) for x in result['eligible'])
                await interaction.channel.send(
                    f"📌 **채택된 선택지:** {discord.utils.escape_markdown(result['winner']['text'])}\n\n"
                    f"▫️ **조건을 충족한 참가자**: {people}\n"
                    '-# 해당 참가자 중 한 명이 `/행동`을 사용하면 효과가 적용되고 다음 장면으로 이동합니다.')
            else:
                await interaction.followup.send('전원 투표 완료! 다음 장면으로 자동 진행했어요.',ephemeral=True)
                await interaction.channel.send(f"📌 **채택된 선택지:** {discord.utils.escape_markdown(result['winner']['text'])}")
                if result.get('check'):
                    ch=result['check']
                    await interaction.channel.send(f"🎲 **판정 {'성공' if ch['success'] else '실패'}!** (확률 {ch['rate']}% · 난수 {ch['roll']}/100)")
                await send_effects(interaction.channel, result.get('effects', []))
                await interaction.channel.send(embed=scene_embed(result['view']))
        except ValueError as exc: await interaction.followup.send(str(exc),ephemeral=True)

    @app_commands.command(name='행동',description='조건을 충족한 내 캐릭터가 채택된 선택지의 행동을 맡습니다.')
    @app_commands.guild_only()
    async def act(self,interaction:discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            session=await self.session(interaction)
            result=await asyncio.to_thread(story_engine.accept_actor,session['session_id'],interaction.user.id)
            await interaction.followup.send('행동 담당자로 확정됐어요.',ephemeral=True)
            await interaction.channel.send(f"**{discord.utils.escape_markdown(result['actor']['name'])}**이/가 행동합니다.")
            if result.get('check'):
                ch=result['check']
                await interaction.channel.send(f"🎲 **판정 {'성공' if ch['success'] else '실패'}!** (확률 {ch['rate']}% · 난수 {ch['roll']}/100)")
            await send_effects(interaction.channel, result.get('effects', []))
            await interaction.channel.send(embed=scene_embed(result['view']))
        except ValueError as exc:
            await interaction.followup.send(str(exc),ephemeral=True)

    @app_commands.command(name='행동거부', description='행동을 거부합니다. 행동 가능한 참가자 전원이 거부하면 재투표합니다.')
    @app_commands.guild_only()
    async def refuse(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            session = await self.session(interaction)
            result = await asyncio.to_thread(story_engine.refuse_actor, session['session_id'], interaction.user.id)
            members = await asyncio.to_thread(game_sessions.members, session['session_id'])
            actor_name = next((m['character_name'] for m in members if str(m['user_id']) == str(interaction.user.id)), None)
            actor_name = discord.utils.escape_markdown(actor_name or '참가 캐릭터')
            if result['kind'] == 'refusal_pending':
                await interaction.followup.send(
                    f"행동 거부 완료! ({result['refused']}/{result['total']}명) 다른 참가자의 결정을 기다립니다.",
                    ephemeral=True)
                await interaction.channel.send(
                    f"🚫 {actor_name} 님이 행동을 거부했습니다. "
                    f"({result['refused']}/{result['total']}명 거부)\n"
                    '행동 가능한 참가자 중 한 명이라도 `/행동`을 선택하면 정상 진행합니다.')
            else:
                await interaction.followup.send('행동 가능한 참가자 전원이 거부해 재투표를 시작합니다.', ephemeral=True)
                await interaction.channel.send(
                    f'🚫 {actor_name} 님이 행동을 거부했습니다.\n'
                    '-# 이전 투표를 초기화하고 선택지 투표를 다시 시작합니다.\n'
                    '-# 참가자 전원이 `/투표`로 다시 선택해 주세요.')
                await interaction.channel.send(embed=scene_embed(result['view']))
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @app_commands.command(name='지원팀호출', description='참가자 전원이 호출하면 현재 스토리를 중단합니다.')
    @app_commands.guild_only()
    async def support_team(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            session = await self.session(interaction)
            result = await asyncio.to_thread(story_engine.call_support_team,
                                             session['session_id'], interaction.user.id)
            if result['kind'] == 'pending':
                await interaction.followup.send(
                    f"지원팀 호출에 동의했습니다. ({result['called']}/{result['total']}명)", ephemeral=True)
                await interaction.channel.send(
                    f"🚨 <@{interaction.user.id}> 님이 지원팀을 호출했습니다. "
                    f"({result['called']}/{result['total']}명)\n"
                    '-# 참가자 전원이 `/지원팀호출`을 실행하면 스토리가 중단됩니다.')
            else:
                await interaction.followup.send('지원팀 호출이 완료됐습니다.', ephemeral=True)
                await interaction.channel.send(
                    '🚨 **참가자 전원이 지원팀을 호출했습니다.**\n'
                    '-# 현재 스토리가 중단되고 게임이 종료됐습니다. 기존 캐릭터 데이터는 유지됩니다.')
                await self.refresh_board(interaction)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @app_commands.command(name='투표현황',description='투표 완료 및 미투표 인원을 확인합니다.')
    @app_commands.guild_only()
    async def status(self,interaction:discord.Interaction):
        try:
            session=await self.session(interaction)
            result=await asyncio.to_thread(story_engine.vote_status,session['session_id'])
            remain=' '.join(f'<@{x}>' for x in result['remaining']) or '없음'
            message = f"투표 {result['voted']}/{result['total']}명 완료\n미투표: {remain}"
            if result['total'] == 0:
                message += '\n⚠️ 행동 가능한 참가자가 없습니다. 아이템으로 회복하거나 /지원팀호출을 이용해 주세요.'
            await interaction.response.send_message(message)
        except ValueError as exc: await interaction.response.send_message(str(exc),ephemeral=True)

    @app_commands.command(name='현재장면',description='현재 장면을 다시 표시합니다.')
    @app_commands.guild_only()
    async def current(self,interaction:discord.Interaction):
        try:
            session=await self.session(interaction)
            result=await asyncio.to_thread(story_engine.current,session['session_id'])
            await interaction.response.send_message(embed=scene_embed(result))
        except ValueError as exc: await interaction.response.send_message(str(exc),ephemeral=True)

    @app_commands.command(name='장면동기화',description='Scenes/Choices 시트를 동기화합니다. (운영자 전용)')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def sync_story(self,interaction:discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            scenes,choices=await asyncio.to_thread(sync)
            await interaction.followup.send(f'장면 {scenes}개, 선택지 {choices}개 동기화 완료.',ephemeral=True)
        except Exception as exc: await interaction.followup.send(f'동기화 실패: {exc}',ephemeral=True)

async def setup(bot): await bot.add_cog(Story(bot))
