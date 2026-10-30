"""Independent crafting menu in user-created threads."""
from services.shop import selected as _selected_character
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services import crafting as svc


def safe(value):
    return discord.utils.escape_markdown(str(value))


async def _active_name(interaction):
    try:
        return _selected_character(interaction.guild_id, interaction.user.id)['name']
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return None


class CraftView(discord.ui.View):
    def __init__(self, owner_id, character_name):
        super().__init__(timeout=600)
        self.owner_id=owner_id
        self.character_name=character_name
        self.mode=None
        self.selected=None
        self.recipes=[]
        self.message=None
        self._menu()

    def _menu(self):
        self.clear_items()
        for label,kind,style in [('장비','equipment',discord.ButtonStyle.primary),('재료','material',discord.ButtonStyle.success)]:
            button=discord.ui.Button(label=label,style=style)
            async def choose(interaction,kind=kind):
                try:
                    _, rows=await asyncio.to_thread(svc.list_available,self.owner_id,self.character_name,kind)
                    self.mode=kind
                    self.recipes=rows
                    self.selected=None
                    self._list()
                    await interaction.response.edit_message(embed=self.embed(),view=self)
                except ValueError as exc:
                    await interaction.response.send_message(str(exc),ephemeral=True)
            button.callback=choose
            self.add_item(button)
        cancel=discord.ui.Button(label='취소',style=discord.ButtonStyle.danger)
        async def stop(interaction):
            self.stop()
            self.clear_items()
            await interaction.response.edit_message(embed=discord.Embed(title='제작 종료',description='제작 작업대를 닫았습니다.'),view=self)
        cancel.callback=stop
        self.add_item(cancel)

    def _list(self):
        self.clear_items()
        if self.recipes:
            options=[discord.SelectOption(label=r['result_name'][:100],value=r['recipe_id'],description=f"수량 {r['result_quantity']} · 성공률 {r['success_rate']}%"[:100]) for r in self.recipes[:25]]
            select=discord.ui.Select(placeholder='제작할 아이템 선택',options=options)
            async def selected(interaction):
                self.selected=next((r for r in self.recipes if r['recipe_id']==select.values[0]),None)
                self._confirm()
                await interaction.response.edit_message(embed=self.embed(),view=self)
            select.callback=selected
            self.add_item(select)
        back=discord.ui.Button(label='뒤로',style=discord.ButtonStyle.secondary)
        async def go_back(interaction):
            self.mode=None
            self.selected=None
            self._menu()
            await interaction.response.edit_message(embed=self.embed(),view=self)
        back.callback=go_back
        self.add_item(back)

    def _confirm(self):
        self._list()
        confirm=discord.ui.Button(label='제작 확인',style=discord.ButtonStyle.success)
        async def do_craft(interaction):
            if not self.selected:
                await interaction.response.send_message('레시피를 선택해 주세요.',ephemeral=True)
                return
            # Prevent a second click from crafting again while the first is running.
            if getattr(self, '_crafting', False):
                await interaction.response.send_message('이미 제작을 진행 중입니다.',ephemeral=True)
                return
            self._crafting = True
            await interaction.response.defer()
            try:
                result = await asyncio.to_thread(
                    svc.craft, self.owner_id, self.character_name, self.selected['recipe_id']
                )
            except ValueError as exc:
                self._crafting = False
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            except Exception:
                self._crafting = False
                raise

            # Keep the selected recipe embed exactly as it appeared at confirmation.
            # Remove all controls, turning the message into a permanent record.
            self.stop()
            self.clear_items()
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                # The crafting outcome must still be reported if editing fails.
                pass

            status = '⭕ 제작 성공!' if result['success'] else '❌ 제작 실패'
            gained = (f"{safe(result['gained'])} ×{result['quantity']}"
                      if result['quantity'] else '없음')
            await interaction.followup.send(
                f'## {status}\n'
                f'> 획득: {gained}\n> 제작비: {result["cost"]}G',
                ephemeral=False
            )
        confirm.callback=do_craft
        self.add_item(confirm)

    def embed(self):
        e=discord.Embed(title='⚒️ 제작 작업대',color=discord.Color.blurple())
        e.description=f'`캐릭터: {safe(self.character_name)}`\n\n'
        if self.mode is None:
            e.description+='제작할 종류를 선택해 주세요.'
        elif self.selected:
            r=self.selected
            e.description+=f'**{safe(r["result_name"])} ×{r["result_quantity"]}**\n\n'
            e.add_field(name='필요 재료',value='\n'.join(f'{safe(n)} ×{q}' for _,n,q in r['required']),inline=False)
            e.add_field(name='성공률',value=f'`{r["success_rate"]}%`',inline=True)
            e.add_field(name='제작비',value=f'`{r["cost"]}G`',inline=True)
            e.set_footer(text='제작 확인을 누르면 재료와 제작비가 차감됩니다.')
        else:
            e.add_field(name=f'**{"장비" if self.mode=="equipment" else "재료"} 제작**', 
                        value=f'제작 가능한 레시피 {len(self.recipes)}개' if self.recipes else '-# 현재 재료와 소지금으로 제작할 수 있는 항목이 없습니다.')
            if len(self.recipes)>25:
                e.set_footer(text='최대 25개만 표시됩니다. 레시피를 분류하거나 재료를 조정해 주세요.')
        return e

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('제작을 시작한 사용자만 조작할 수 있습니다.',ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.clear_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Crafting(commands.Cog):
    def __init__(self,bot):
        self.bot=bot

    @app_commands.command(name='레시피동기화',description='Google Sheets Recipes 탭 동기화')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync(self,interaction:discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            count=await asyncio.to_thread(svc.sync_recipes)
            await interaction.followup.send(f'레시피 {count}개 동기화 완료',ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f'동기화 실패: {type(exc).__name__}: {exc}',ephemeral=True)

    @app_commands.command(name='제작',description='스레드에서 장비나 재료를 제작합니다')
    @app_commands.guild_only()
    async def craft(self,interaction:discord.Interaction):
        캐릭터 = await _active_name(interaction)
        if 캐릭터 is None: return
        if not isinstance(interaction.channel,discord.Thread):
            await interaction.response.send_message('제작은 직접 만든 스레드에서 실행해 주세요.',ephemeral=True)
            return
        try:
            await asyncio.to_thread(svc.list_available,interaction.user.id,캐릭터,'equipment')
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True)
            return
        view=CraftView(interaction.user.id,캐릭터)
        await interaction.response.send_message(embed=view.embed(),view=view)
        view.message=await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Crafting(bot))
