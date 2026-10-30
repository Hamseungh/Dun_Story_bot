"""Player inventory and equipment commands (all responses private)."""
"""/인벤토리 /아이템정보 /장비장착 /장비해제"""
from services.shop import selected as _selected_character
import asyncio
import math
import re
import unicodedata
import discord
from discord import app_commands
from discord.ext import commands
from services import items as db


def safe(s):
    return discord.utils.escape_markdown(str(s))


PAGE_SIZE = 20


def natural_name_key(name):
    """Compare numeric segments by their numeric value (2 before 10)."""
    normalized = unicodedata.normalize('NFKC', str(name or '')).casefold()
    return tuple((0, int(part)) if part.isdigit() else (1, part)
                 for part in re.split(r'(\d+)', normalized) if part)


def item_sort_key(row):
    name = unicodedata.normalize('NFKC', str(row['name'] or ''))
    folded = name.casefold()
    first = folded[:1]
    # 숫자 -> 영문 A-Z -> 한글 가나다 -> 기타.
    if first.isdigit():
        group = 0
    elif 'a' <= first <= 'z':
        group = 1
    elif first and ('가' <= first <= '힣' or 'ㄱ' <= first <= 'ㅎ'):
        group = 2
    else:
        group = 3
    return (group, natural_name_key(name), name)


async def _active_name(interaction):
    try:
        return _selected_character(interaction.guild_id, interaction.user.id)['name']
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return None


class InventoryPages(discord.ui.View):
    def __init__(self, owner_id: int, character_name: str, rows):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.character_name = character_name
        self.rows = sorted(rows, key=item_sort_key)
        self.page = 0
        self.total_pages = max(1, math.ceil(len(self.rows) / PAGE_SIZE))
        self.message = None
        self.refresh_buttons()

    def refresh_buttons(self):
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    def embed(self):
        begin = self.page * PAGE_SIZE
        selected = self.rows[begin:begin + PAGE_SIZE]
        lines = [
            f"• {safe(r['name'] or '이름 없는 아이템')} ×{r['quantity']}"
            + (' [장착]' if r['equipped_slot'] else '')
            for r in selected
        ]
        embed = discord.Embed(
            title=f"{self.character_name}의 인벤토리",
            description='\n'.join(lines) if lines else '아이템이 없습니다.',
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{self.page + 1} / {self.total_pages} 페이지 · 총 {len(self.rows)}종")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('본인의 인벤토리만 조작할 수 있습니다.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='<', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='>', style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Items(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='인벤토리', description='내 캐릭터의 아이템 목록')
    async def inventory(self, interaction: discord.Interaction):
        캐릭터 = await _active_name(interaction)
        if 캐릭터 is None: return
        try:
            c, rows = await asyncio.to_thread(db.inventory, interaction.user.id, 캐릭터)
            view = InventoryPages(interaction.user.id, c['name'], rows)
            await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
            view.message = await interaction.original_response()
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(name='장비장착', description='소유한 장비를 장착합니다')
    @app_commands.describe(아이템이름='장착할 장비 이름')
    async def equip(self, interaction: discord.Interaction, 아이템이름: str):
        캐릭터 = await _active_name(interaction)
        if 캐릭터 is None: return
        try:
            name, slot = await asyncio.to_thread(db.equip, interaction.user.id, 캐릭터, 아이템이름)
            await interaction.response.send_message(f'{safe(캐릭터)}: {safe(name)} 장착 완료 ({slot})', ephemeral=True)
        except (ValueError, discord.HTTPException) as exc:
            if not interaction.response.is_done():
                await interaction.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(name='장비해제', description='캐릭터의 장비를 해제합니다')
    @app_commands.describe(슬롯='장비 슬롯')
    @app_commands.choices(슬롯=[app_commands.Choice(name=label, value=key) for key,label in db.SLOTS.items()])
    async def unequip(self, interaction: discord.Interaction, 슬롯: app_commands.Choice[str]):
        캐릭터 = await _active_name(interaction)
        if 캐릭터 is None: return
        try:
            item_name = await asyncio.to_thread(db.unequip, interaction.user.id, 캐릭터, 슬롯.value)
            await interaction.response.send_message(f'{safe(캐릭터)}: {슬롯.name} 슬롯에서 {safe(item_name)} 해제 완료', ephemeral=True)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(name='아이템정보', description='아이템의 정보와 묘사를 확인합니다')
    @app_commands.describe(아이템이름='Items 시트에 등록된 아이템 이름')
    async def item_info(self, interaction: discord.Interaction, 아이템이름: str):
        try:
            item = await asyncio.to_thread(db.item_information, 아이템이름)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=False)
            return
        categories = {'equipment': '장비', 'material': '소재', 'consumable': '소비아이템'}
        embed = discord.Embed(
            title=item['name'],
            description=item['description'] or '등록된 묘사가 없습니다.',
            color=discord.Color.blurple(),
        )
        embed.add_field(name='분류', value=categories.get(item['category'], item['category']))
        if item['category'] == 'equipment':
            embed.add_field(name='장착 부위', value=db.SLOTS.get(item['slot'], item['slot']))
            stat_names = {
                'hp_stat': '체력', 'mental_stat': '정신력',
                'agility': '민첩', 'intelligence': '지능', 'strength': '힘',
            }
            bonuses = [f"{stat_names[key]} {int(item[key]):+d}" for key in db.STATS if item[key]]
            embed.add_field(name='장비 효과', value='\n'.join(bonuses) if bonuses else '능력치 보너스 없음', inline=False)
        embed.add_field(name='최대 보유 수량', value=str(item['max_stack']))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Items(bot))
