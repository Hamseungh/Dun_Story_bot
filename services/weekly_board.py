"""주간 스토리 게시판 서비스 (Python 3.15 / discord.py 2.x)."""
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from database import DB_PATH

log = logging.getLogger(__name__)
KST = ZoneInfo('Asia/Seoul')


def connect_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def monday_of(day):
    return (day - timedelta(days=day.weekday())).isoformat()


def current_publication_week(now=None):
    """월요일 09:00 이전에는 지난주 게시물을 유지한다."""
    now = now or datetime.now(KST)
    day = now.date()
    if now.weekday() == 0 and now.time() < time(9, 0):
        day -= timedelta(days=7)
    return monday_of(day)


def get_board_config(guild_id):
    with connect_db() as conn:
        return conn.execute(
            'SELECT * FROM board_messages WHERE guild_id = ?', (str(guild_id),)
        ).fetchone()


def get_weekly_stories(week_start):
    with connect_db() as conn:
        return conn.execute('''
            SELECT s.*, w.display_order,
              (SELECT COUNT(*) FROM game_sessions g WHERE g.story_id=s.story_id
               AND g.status='finished' AND g.clear_completed=1) + COALESCE((SELECT adjustment FROM story_clear_adjustments a WHERE a.story_id=s.story_id),0) AS clear_count,
              (SELECT COUNT(*) FROM game_sessions g WHERE g.story_id=s.story_id
               AND g.status!='finished') AS running_count
            FROM stories s LEFT JOIN weekly_stories w
              ON w.story_id=s.story_id AND w.week_start=?
            WHERE s.enabled=1 AND
              (s.availability='permanent' OR (s.availability='weekly' AND w.story_id IS NOT NULL))
            ORDER BY CASE WHEN s.availability='weekly' THEN 0 ELSE 1 END,
                     w.display_order, s.title, s.story_id
        ''', (week_start,)).fetchall()


def save_board_config(guild_id, channel_id):
    with connect_db() as conn:
        conn.execute('''
            INSERT INTO board_messages(guild_id, channel_id, message_id, last_week_start, updated_at)
            VALUES (?, ?, NULL, NULL, NULL)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = CASE
                    WHEN board_messages.channel_id = excluded.channel_id
                    THEN board_messages.message_id ELSE NULL END,
                last_week_start = CASE
                    WHEN board_messages.channel_id = excluded.channel_id
                    THEN board_messages.last_week_start ELSE NULL END
        ''', (str(guild_id), str(channel_id)))


def record_published(guild_id, channel_id, message_id, week_start):
    with connect_db() as conn:
        conn.execute('''
            UPDATE board_messages
            SET channel_id = ?, message_id = ?, last_week_start = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ?
        ''', (str(channel_id), str(message_id), week_start, str(guild_id)))


def build_embeds(stories, week_start):
    """Two independently titled embeds within one stable board message."""
    monday = datetime.strptime(week_start, '%Y-%m-%d').date()
    sunday = monday + timedelta(days=6)
    embeds = []
    for availability, title, description, color in (
        ('permanent', '📍 상시 수락 가능한 의뢰 목록',
         '-# 기간 제한 없이 플레이할 수 있는 게이트입니다.', discord.Color.dark_grey()),
        ('weekly', '📅 이번 주 수락 가능한 의뢰 목록',
         f'{monday:%Y.%m.%d} ~ {sunday:%Y.%m.%d}', discord.Color.blurple()),
    ):
        embed = discord.Embed(title=title, description=description, color=color)
        selected = [story for story in stories if story['availability'] == availability]
        if not selected:
            embed.add_field(name='안내', value='현재 등록된 게이트가 없습니다.', inline=False) 
        for story in selected[:25]:
            limit, count = story['clear_limit'], story['clear_count']
            progress = f'{count}/{limit}회' if limit is not None else f'{count}회 (무제한)'
            state = ('클리어 횟수 소진' if limit is not None and count >= limit
                     else '진행 중 · 신규 입장 불가' if story['running_count'] else '입장 가능')
            desc = (f'> {story['description']}' or '> 소개가 없습니다.')[:680]
            value = (f"**장르:** {story['genre'] or '미정'}\n{desc}\n"
                     f"> `클리어: {progress} · 상태: {state}`")
            embed.add_field(name=str(story['title'])[:200], value=value[:1024], inline=False)
        if len(selected) > 25:
            embed.set_footer(text=f'표시 제한으로 {len(selected)-25}개는 생략되었습니다.')
        else:
            embed.set_footer(text='새 게임은 /게이트입장에서 선택 · 진행 중인 게임은 계속 진행 가능')
        embeds.append(embed)
    return embeds


def build_embed(stories, week_start):
    """Legacy compatibility for imports expecting a single embed."""
    return build_embeds(stories, week_start)[0]


class WeeklyBoard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.publish_lock = asyncio.Lock()
        self.refresh_loop.start()

    def cog_unload(self):
        self.refresh_loop.cancel()

    async def publish(self, guild_id, *, force=False, sync=False):
        """게시판 갱신. 결과: (게시/변경없음/미설정/편성없음, 설명)."""
        async with self.publish_lock:
            config = get_board_config(guild_id)
            if config is None:
                return '미설정', '먼저 /게시판설정으로 채널을 지정하세요.'

            week = current_publication_week()
            if not force and config['last_week_start'] == week and config['message_id']:
                return '변경없음', '이미 이번 주 게시물이 등록되어 있습니다.'

            if sync:
                # 동기화 함수는 동기식 I/O이므로 이벤트 루프를 막지 않는다.
                from services.sheets_loader import sync_sheets
                await asyncio.to_thread(sync_sheets)

            stories = get_weekly_stories(week)

            channel_id = int(config['channel_id'])
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError('게시판 채널은 일반 텍스트 채널이어야 합니다.')
            if channel.guild.id != int(guild_id):
                raise ValueError('설정된 채널이 해당 서버에 속하지 않습니다.')

            embeds = build_embeds(stories, week)
            message = None
            if config['message_id']:
                try:
                    message = await channel.fetch_message(int(config['message_id']))
                except discord.NotFound:
                    pass
            if message:
                await message.edit(embeds=embeds, content=None)
            else:
                message = await channel.send(embeds=embeds)

            record_published(guild_id, channel.id, message.id, week)
            return '게시', f'{channel.mention}에 주간/상시 스토리 {len(stories)}개를 갱신했습니다.'

    @tasks.loop(minutes=1)
    async def refresh_loop(self):
        # 월요일 오전 9시 이후부터 이번 주 게시판으로 전환.
        for guild in self.bot.guilds:
            try:
                config = get_board_config(guild.id)
                if config is None:
                    continue
                if config['last_week_start'] == current_publication_week() and config['message_id']:
                    continue
                status, detail = await self.publish(guild.id, sync=True)
                if status != '게시':
                    log.warning('게시판 갱신 건너뜀 (%s): %s', guild.id, detail)
            except Exception:
                log.exception('게시판 자동 갱신 실패 (guild=%s)', guild.id)

    @refresh_loop.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(WeeklyBoard(bot))
