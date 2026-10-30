"""Private transfer commands. The recipient must own the named target character."""
"""/아이템양도 /골드양도"""
from services.shop import selected as _selected_character
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services import transfers


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


async def _active_name(interaction):
    try:
        return _selected_character(interaction.guild_id, interaction.user.id)['name']
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return None


class Transfers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='아이템양도', description='내 캐릭터의 아이템을 다른 캐릭터에게 양도합니다')
    @app_commands.describe(받는사람='받는 캐릭터의 소유자', 받는캐릭터='받는 캐릭터 이름', 아이템이름='양도할 아이템 이름', 수량='양도 수량')
    async def item_transfer(self, interaction: discord.Interaction, 받는사람: discord.User, 받는캐릭터: str, 아이템이름: str, 수량: app_commands.Range[int, 1, 999999]):
        보내는캐릭터 = await _active_name(interaction)
        if 보내는캐릭터 is None: return
        await interaction.response.defer(ephemeral=True)
        try:
            source, target, item, count = await asyncio.to_thread(
                transfers.transfer_item, interaction.user.id, 보내는캐릭터,
                받는사람.id, 받는캐릭터, 아이템이름, int(수량))
            await interaction.followup.send(f'아이템 양도 완료: **{safe(source)}** → **{safe(target)}**\n{safe(item)} ×{count:,}개', ephemeral=False)
        except ValueError as exc:
            await interaction.followup.send(safe(exc), ephemeral=True)

    @app_commands.command(name='골드양도', description='내 캐릭터의 골드를 다른 캐릭터에게 양도합니다')
    @app_commands.describe(받는사람='받는 캐릭터의 소유자', 받는캐릭터='받는 캐릭터 이름', 금액='양도할 골드')
    async def gold_transfer(self, interaction: discord.Interaction, 받는사람: discord.User, 받는캐릭터: str, 금액: app_commands.Range[int, 1, 1000000000]):
        보내는캐릭터 = await _active_name(interaction)
        if 보내는캐릭터 is None: return
        await interaction.response.defer(ephemeral=True)
        try:
            source, target, amount = await asyncio.to_thread(
                transfers.transfer_gold, interaction.user.id, 보내는캐릭터,
                받는사람.id, 받는캐릭터, int(금액))
            await interaction.followup.send(f'골드 양도 완료: **{safe(source)}** → **{safe(target)}**\n{amount:,} 골드', ephemeral=False)
        except ValueError as exc:
            await interaction.followup.send(safe(exc), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Transfers(bot))
