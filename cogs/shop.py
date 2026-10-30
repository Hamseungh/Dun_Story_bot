"""Shop slash commands and one-shot confirmation views."""
"""/구매 /판매/ /상품목록 /상품등록"""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services import shop


def esc(value): return discord.utils.escape_markdown(str(value))

class Confirm(discord.ui.View):
    def __init__(self, guild_id, user_id, data):
        super().__init__(timeout=180)
        self.guild_id=guild_id; self.user_id=user_id; self.data=data
        self.finished=False

    async def interaction_check(self, interaction):
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message('명령어를 실행한 사용자만 거래할 수 있습니다.',ephemeral=True)
            return False
        return True

    async def finish(self, interaction, action):
        if self.finished:
            await interaction.response.send_message('이미 처리된 거래입니다.',ephemeral=True)
            return
        self.finished=True
        for child in self.children: child.disabled=True
        await interaction.response.edit_message(view=self)
        if not action:
            await interaction.followup.send('거래를 취소했습니다.',ephemeral=True)
            return
        try:
            result=await asyncio.to_thread(shop.execute,self.guild_id,self.user_id,self.data)
        except ValueError as e:
            await interaction.followup.send(f'거래 실패: {e}',ephemeral=True)
            return
        await interaction.followup.send(result,ephemeral=False)

    @discord.ui.button(label='구매/판매 확정',style=discord.ButtonStyle.success)
    async def confirm(self,interaction,button):
        await self.finish(interaction,True)

    @discord.ui.button(label='취소',style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button):
        await self.finish(interaction,False)


def embed_preview(data):
    buy=data['mode']=='buy'
    e=discord.Embed(title='아이템 구매' if buy else '아이템 판매',color=discord.Color.blurple())
    e.description=(f"`캐릭터: {esc(data['character'])}`\n\n")
    e.add_field(name=f"{'구매' if buy else '판매'} 아이템", value=f"{esc(data['name'])} x {data['quantity']:,}", inline=True)
    e.add_field(name= f'합계', value=f"{data['total']:,}G")
    e.set_footer(text=f"{'구매 후 남는 돈' if buy else '판매 후 남는 개수'}: {data['remaining']:,}{'G' if buy else '개'}")
    return e

class Shop(commands.Cog):
    def __init__(self,bot): self.bot=bot

    async def transaction(self,interaction,name,count,mode):
        try:
            data=await asyncio.to_thread(shop.preview,interaction.guild_id,interaction.user.id,name,count,mode)
        except ValueError as e:
            await interaction.response.send_message(str(e),ephemeral=True)
            return
        view=Confirm(interaction.guild_id,interaction.user.id,data)
        view.confirm.label='구매' if mode=='buy' else '판매'
        await interaction.response.send_message(embed=embed_preview(data),view=view,ephemeral=False)

    @app_commands.command(name='구매',description='현재 선택한 캐릭터로 아이템 구매')
    @app_commands.guild_only()
    @app_commands.describe(아이템_이름='구매할 상품 이름',개수='구매 개수')
    async def buy(self,interaction:discord.Interaction,아이템_이름:str,개수:app_commands.Range[int,1,999999]):
        await self.transaction(interaction,아이템_이름,개수,'buy')

    @app_commands.command(name='판매',description='현재 선택한 캐릭터의 아이템 판매')
    @app_commands.guild_only()
    @app_commands.describe(아이템_이름='판매할 아이템 이름',개수='판매 개수')
    async def sell(self,interaction:discord.Interaction,아이템_이름:str,개수:app_commands.Range[int,1,999999]):
        await self.transaction(interaction,아이템_이름,개수,'sell')

    @app_commands.command(name='상품등록',description='상품 등록/수정/삭제 (관리자)')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(아이템_이름='Items 시트에 등록된 아이템 이름',개수='1 이상: 재고 설정 / 0: 무한 / -1: 삭제',가격='개당 구매 가격',공개유무='1: 공개 / 0: 비공개')
    async def register(self,interaction:discord.Interaction,아이템_이름:str,개수:int,가격:app_commands.Range[int,0,1000000000],공개유무:app_commands.Range[int,0,1]):
        try:
            msg=await asyncio.to_thread(shop.register,아이템_이름,개수,가격,공개유무)
            await interaction.response.send_message(msg,ephemeral=True)
        except ValueError as e: await interaction.response.send_message(str(e),ephemeral=True)

    @app_commands.command(name='상품목록',description='공개된 상점 상품 확인')
    @app_commands.guild_only()
    async def catalog(self,interaction:discord.Interaction):
        rows=await asyncio.to_thread(shop.products)
        if not rows:
            await interaction.response.send_message('공개된 상품이 없습니다.',ephemeral=True)
            return
        lines=[f"**{esc(r['name'])}** · {r['price']:,}G · 재고 {'무한' if r['stock']==0 else ('품절' if r['stock']<0 else str(r['stock'])+'개')}" for r in rows]
        chunks=['\n'.join(lines[i:i+15]) for i in range(0,len(lines),15)]
        await interaction.response.send_message(embed=discord.Embed(title='상품 목록',description=chunks[0],color=discord.Color.blurple()),ephemeral=False)
        for chunk in chunks[1:]:
            await interaction.followup.send(embed=discord.Embed(title='상품 목록 (계속)',description=chunk,color=discord.Color.blurple()),ephemeral=False)

async def setup(bot): await bot.add_cog(Shop(bot))
