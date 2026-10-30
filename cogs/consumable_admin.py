"""Admin command for syncing consumable effects."""
"""/소비효과동기화"""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services.consumable_sync import sync_effects


class ConsumableAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='소비효과동기화', description='Google Sheets 소비아이템 효과 동기화')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count = await asyncio.to_thread(sync_effects)
            await interaction.followup.send(f'소비아이템 효과 {count}개 동기화 완료', ephemeral=True)
        except (ValueError, Exception) as exc:
            await interaction.followup.send(f'동기화 실패: {type(exc).__name__}: {exc}', ephemeral=True)


async def setup(bot):
    await bot.add_cog(ConsumableAdmin(bot))
