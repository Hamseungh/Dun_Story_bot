"""Private player consumable command."""
"""아이템사용"""
from services.shop import selected as _selected_character
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services.consumables import use_item


async def _active_name(interaction):
    try:
        return _selected_character(interaction.guild_id, interaction.user.id)['name']
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return None


class Consumables(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='아이템사용', description='캐릭터의 소비아이템을 사용합니다')
    @app_commands.describe(아이템이름='사용할 소비아이템 이름')
    async def use(self, interaction: discord.Interaction, 아이템이름: str):
        캐릭터 = await _active_name(interaction)
        if 캐릭터 is None: return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await asyncio.to_thread(use_item, interaction.user.id, 캐릭터, 아이템이름)
            safe = discord.utils.escape_markdown
            await interaction.followup.send(
                f"**{safe(result['character'])}**: {safe(result['item'])} 사용 완료\n"
                f"{result['resource']} +{result['recovered']} "
                f"({result['current']}/{result['maximum']})\n"
                f"남은 수량: {result['remaining']}개", ephemeral=False)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Consumables(bot))
