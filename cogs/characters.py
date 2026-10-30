"""Character display commands; equipment bonuses are included in total stats."""
"""/헌터등록 /헌터목록 /헌터정보"""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services import characters as db
from services.character_stats import get_character_stats, STAT_LABELS


class Characters(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='헌터등록', description='새 헌터 헌터를 등록합니다.')
    @app_commands.describe(헌터_이름='헌터 이름 (1~40자)', 직군='직군 선택', 등급='F~S 등급',
                           체력='1~5', 정신력='1~5', 민첩='1~5', 지능='1~5', 힘='1~5')
    @app_commands.choices(
        직군=[app_commands.Choice(name=x, value=x) for x in ('사무직','현장직','제작직','연구직','간부직')],
        등급=[app_commands.Choice(name=x, value=x) for x in ('F','E','D','C','B','A','S')],
    )
    async def register(self, interaction: discord.Interaction, 헌터_이름: str,
                       직군: app_commands.Choice[str], 등급: app_commands.Choice[str],
                       체력: int, 정신력: int, 민첩: int, 지능: int, 힘: int):
        try:
            result = await asyncio.to_thread(
                db.register_character, interaction.user.id, 헌터_이름,
                직군.value, 등급.value, 체력, 정신력, 민첩, 지능, 힘)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except Exception:
            await interaction.response.send_message('등록 처리 중 오류가 발생했습니다. 관리자에게 문의해 주세요.', ephemeral=True)
            raise
        embed = discord.Embed(title='헌터 등록 완료', color=discord.Color.green())
        embed.description = (f"**{discord.utils.escape_markdown(result['name'])}**\n"
                             f"{result['job']} · {result['grade']}등급")
        embed.add_field(name='초기 능력치',
                        value=(f'체력 {체력} · 정신력 {정신력} · 민첩 {민첩} · '
                               f'지능 {지능} · 힘 {힘}\n'
                               f"합계 {result['total']} / {result['limit']}"), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='헌터목록', description='내가 등록한 헌터 목록')
    async def mine(self, interaction: discord.Interaction):
        chars = await asyncio.to_thread(db.list_characters, interaction.user.id)
        if not chars:
            await interaction.response.send_message('등록된 헌터가 없습니다. /헌터등록으로 캐릭터를 만들어 주세요.', ephemeral=True)
            return
        lines = [f"**{discord.utils.escape_markdown(c['name'])}** — {c['grade']} / {c['job']} / Lv.{c['level']}" for c in chars]
        await interaction.response.send_message('\n'.join(lines)[:1900], ephemeral=True)

    @app_commands.command(name='헌터정보', description='내 헌터 능력치(성장 및 장비 포함) 조회')
    @app_commands.describe(이름='내 헌터 이름')
    async def info(self, interaction: discord.Interaction, 이름: str):
        try:
            c, stats = await asyncio.to_thread(get_character_stats, interaction.user.id, 이름)
        except Exception:
            await interaction.response.send_message('능력치를 불러오지 못했습니다. 아이템 DB 마이그레이션 상태를 확인해 주세요.', ephemeral=True)
            raise
        if not c:
            await interaction.response.send_message('캐릭터를 찾을 수 없습니다.', ephemeral=True)
            return
        embed = discord.Embed(title=c['name'], color=discord.Color.blurple())
        embed.add_field(name='기본 정보', value=f"등급 {c['grade']} · 직군 {c['job']} · Lv.{c['level']} · 경험치 {c['exp']}", inline=False)
        lines = []
        for key, label in STAT_LABELS.items():
            s = stats[key]
            details = [f"{s['base']}"]
            if s['growth']:
                details.append(f"{s['growth']:+d}")
            if s['equipment']:
                details.append(f"{s['equipment']:+d}")
            lines.append(f"{label}: {s['total']} ({' '.join(details)})")
        hp_max = max(0, (stats['hp_stat']['base'] + stats['hp_stat']['growth']) * 20 + stats['hp_stat']['equipment'])
        mental_max = max(0, (stats['mental_stat']['base'] + stats['mental_stat']['growth']) * 10 + stats['mental_stat']['equipment'])
        lines.extend([f"\n현재 HP {c['current_hp']} / {hp_max}",
                      f"현재 정신력 {c['current_mental']} / {mental_max}"])
        embed.add_field(name='능력치 (기본 + 성장 + 장비)', value='```' + '\n'.join(lines) + '```', inline=False)
        from services.items import equipment as get_equipment, SLOTS
        _, equipped = await asyncio.to_thread(get_equipment, interaction.user.id, 이름)
        gear_lines = [f"{SLOTS.get(r['slot'], r['slot'])}: {discord.utils.escape_markdown(r['name'] or '이름 없는 아이템')}" for r in equipped]
        embed.add_field(name='장착 장비', value='```' + ('\n'.join(gear_lines) if gear_lines else '장착 장비 없음') + '```', inline=False)
        embed.add_field(name='소지금', value=str(c['gold']))
        from services.levels import status as growth_status
        growth = await asyncio.to_thread(growth_status, interaction.user.id, 이름)
        if growth['configured']:
            embed.add_field(name='성장 포인트', value=f"지급 {growth['earned']} · 사용 {growth['spent']} · 남음 {growth['available']}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=False)


async def setup(bot):
    await bot.add_cog(Characters(bot))
