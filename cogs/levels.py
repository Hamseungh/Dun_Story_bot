"""Level synchronization, point allocation and confirmed admin reset."""
"""/레벨동기화 /능력치분배 /성장초기화"""
from services.shop import selected as _selected_character
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services import levels


async def _active_name(interaction):
    try:
        return _selected_character(interaction.guild_id, interaction.user.id)['name']
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return None


class ConfirmReset(discord.ui.View):
    def __init__(self, owner_id, name, admin_id):
        super().__init__(timeout=90)
        self.owner_id=owner_id; self.name=name; self.admin_id=admin_id

    @discord.ui.button(label='성장 능력치 초기화 확인',style=discord.ButtonStyle.danger)
    async def confirm(self, interaction:discord.Interaction, button:discord.ui.Button):
        if interaction.user.id!=self.admin_id:
            await interaction.response.send_message('요청한 운영자만 확인할 수 있습니다.',ephemeral=True);return
        try: result=await asyncio.to_thread(levels.reset,self.owner_id,self.name)
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True);return
        for item in self.children: item.disabled=True
        await interaction.response.edit_message(content=f"{result['name']}의 성장 능력치를 초기화했습니다. 사용 포인트 {result['refunded']}개 반환.",view=self)
        self.stop()

    @discord.ui.button(label='취소',style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.user.id!=self.admin_id:
            await interaction.response.send_message('요청한 운영자만 취소할 수 있습니다.',ephemeral=True);return
        for item in self.children: item.disabled=True
        await interaction.response.edit_message(content='초기화를 취소했습니다.',view=self)
        self.stop()


class Levels(commands.Cog):
    def __init__(self,bot): self.bot=bot

    @app_commands.command(name='레벨동기화',description='운영자: Levels 시트의 누적 경험치와 성장 포인트 기준 갱신')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync(self,interaction:discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            count,characters,overs=await asyncio.to_thread(levels.sync_levels)
            await interaction.followup.send(f'레벨 {count}개 동기화, 헌터 {characters}명 레벨 재계산 완료. 초과 분배 헌터: {overs}명',ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f'레벨 동기화 실패: {exc}',ephemeral=True)
            raise

    @app_commands.command(name='능력치분배',description='내 헌터의 성장 포인트를 능력치에 1점 분배')
    @app_commands.choices(능력치=[app_commands.Choice(name=k,value=k) for k in levels.STATS])
    async def allocate(self,interaction:discord.Interaction,능력치:app_commands.Choice[str]):
        캐릭터 = await _active_name(interaction)
        if 캐릭터 is None: return
        try: result=await asyncio.to_thread(levels.allocate,interaction.user.id,캐릭터,능력치.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True);return
        await interaction.response.send_message(f"{result['name']} · {result['stat']} 성장 +1 (성장 {result['after']}) · 남은 포인트 {result['remaining']}개",ephemeral=True)

    @app_commands.command(name='성장초기화',description='운영자: 특정 헌터의 성장 능력치를 초기화')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset(self,interaction:discord.Interaction,소유자:discord.Member,헌터:str):
        try: r=await asyncio.to_thread(levels.status,소유자.id,헌터)
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True);return
        detail=' · '.join(f'{k} +{v}' for k,v in r['growth'].items())
        await interaction.response.send_message(f"**{r['name']}**의 성장 능력치를 초기화할까요?\n{detail}\n사용 포인트 {r['spent']}개 반환. 기본 능력치·경험치·장비는 유지됩니다.",view=ConfirmReset(소유자.id,헌터,interaction.user.id),ephemeral=True)

async def setup(bot): await bot.add_cog(Levels(bot))
