"""운영자 전용 캐릭터 현재 수치 설정."""
"""/캐릭터수치설정"""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services.stat_admin import set_character_value

class StatAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='캐릭터수치설정', description='운영자: 캐릭터의 현재 체력·정신력·골드·누적 경험치를 설정')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(대상=[
        app_commands.Choice(name='체력', value='hp'),
        app_commands.Choice(name='정신력', value='mental'),
        app_commands.Choice(name='골드', value='gold'),
        app_commands.Choice(name='경험치', value='exp'),
    ])
    async def set_stat(self, interaction: discord.Interaction, 소유자: discord.Member, 캐릭터: str,
                       대상: app_commands.Choice[str], 값: int):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(set_character_value, 소유자.id, 캐릭터, 대상.value, 값)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        note = ' (최대치 범위로 조정됨)' if result['clamped'] else ''
        extra = f"\n레벨: {result['level_before']} → {result['level_after']}" if 대상.value == 'exp' else ''
        cap = f" / {result['maximum']}" if result['maximum'] is not None else ''
        await interaction.followup.send(
            f"{result['name']} {result['label']}: {result['before']} → {result['after']}{cap}{note}{extra}",
            ephemeral=True)

async def setup(bot):
    await bot.add_cog(StatAdmin(bot))
