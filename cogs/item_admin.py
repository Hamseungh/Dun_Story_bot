"""Guild administrator item import and grant."""
"""/아이템동기화 /아이템지급"""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services.item_sync import sync_items
from services.items import grant


class ItemAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='아이템동기화', description='Google Sheets Items 탭 동기화')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count = await asyncio.to_thread(sync_items)
            await interaction.followup.send(f'아이템 {count}개 동기화 완료', ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f'동기화 실패: {type(exc).__name__}: {exc}', ephemeral=True)

    @app_commands.command(name='아이템지급', description='운영자가 캐릭터에게 아이템 지급')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(대상='캐릭터 소유자', 캐릭터='대상 사용자의 캐릭터 이름', 아이템id='Items 시트 item_id', 수량='지급 수량')
    async def give(self, interaction: discord.Interaction, 대상: discord.Member, 캐릭터: str, 아이템id: str, 수량: app_commands.Range[int,1,999999]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            item_name, total = await asyncio.to_thread(grant, 대상.id, 캐릭터, 아이템id, 수량)
            await interaction.followup.send(f'{대상.mention} / {discord.utils.escape_markdown(캐릭터)}: {discord.utils.escape_markdown(item_name)} {수량}개 지급 (총 {total}개)', ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


async def setup(bot):
    await bot.add_cog(ItemAdmin(bot))
