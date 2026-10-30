"""Operator sync command for versioned Conditions and Effects."""
"""/조건효과동기화"""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services.story_rule_sync import sync

class StoryRulesAdmin(commands.Cog):
    @app_commands.command(name='조건효과동기화',description='Conditions/Effects 시트를 동기화합니다. (운영자 전용)')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def sync_rules(self, interaction:discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            conditions,effects=await asyncio.to_thread(sync)
            await interaction.followup.send(f'조건 {conditions}개, 효과 {effects}개 동기화 완료.',ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f'조건/효과 동기화 실패: {exc}',ephemeral=True)

async def setup(bot): await bot.add_cog(StoryRulesAdmin())
