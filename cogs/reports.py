"""Report commands: report, inbox and approved document collection."""
"""/보고 /보고확인 /서류모음"""
from services.shop import selected as _selected_character
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services import reports as store


def report_embed(r):
    title = '📄 ' + r['title'] + (' (반려)' if r['status'] == 'returned' else '')
    description = f"`작성자: {r['author_name']}`\n\n**보고서 내용**\n{r['content']}"
    if r['status'] == 'returned' and r['rejection_reason']:
        description += f"\n\n**반려 사유**\n{r['rejection_reason']}"
    embed = discord.Embed(title=title[:256], description=description[:4096],
                          color=discord.Color.orange() if r['status'] == 'returned' else discord.Color.blurple())
    authors = r.get('source_authors', [])
    if authors:
        footer = '참고 보고서 작성자: ' + ', '.join(authors)
        embed.set_footer(text=footer[:2048])
    return embed


async def _active_name(interaction):
    try:
        return _selected_character(interaction.guild_id, interaction.user.id)['name']
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return None


class SourceSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        current = view.page_rows
        options = [discord.SelectOption(
            label=(r['title'] + (' (반려)' if r['status'] == 'returned' else ''))[:100],
            value=str(r['report_id']), description=f"{r['author_name']} · {r['author_job']}"[:100],
            default=r['report_id'] in view.selected_ids) for r in current]
        super().__init__(placeholder='참고한 보고서를 선택하세요 (복수 선택 가능)',
                         min_values=0, max_values=len(options), options=options, row=0)

    async def callback(self, interaction):
        view = self.parent_view
        page_ids = {r['report_id'] for r in view.page_rows}
        view.selected_ids.difference_update(page_ids)
        view.selected_ids.update(int(v) for v in self.values)
        await view.refresh(interaction)


class SourceView(discord.ui.View):
    def __init__(self, user_id, character_name, title, content, rows):
        super().__init__(timeout=600)
        self.user_id=user_id;self.character_name=character_name
        self.title=title;self.content=content;self.rows=rows
        self.page=0;self.selected_ids=set();self.build()

    @property
    def page_rows(self):
        return self.rows[self.page*25:(self.page+1)*25]

    def embed(self):
        e=discord.Embed(title='참고한 보고서 선택',description=(
            '참고한 보고서를 선택해 주세요. **여러 건을 동시에 선택할 수 있습니다.**\n'
            '한 건만 있더라도 선택한 뒤 [보고 제출]을 눌러야 합니다.\n\n'
            f'작성할 보고서: **{self.title}**\n선택한 보고서: **{len(self.selected_ids)}건**'))
        return e

    def build(self):
        self.clear_items();self.add_item(SourceSelect(self))
        if len(self.rows)>25:
            prev=discord.ui.Button(label='<',style=discord.ButtonStyle.secondary,row=1,disabled=self.page==0)
            nxt=discord.ui.Button(label='>',style=discord.ButtonStyle.secondary,row=1,disabled=(self.page+1)*25>=len(self.rows))
            async def move(interaction,delta):
                self.page+=delta;await self.refresh(interaction)
            async def p(interaction):await move(interaction,-1)
            async def n(interaction):await move(interaction,1)
            prev.callback=p;nxt.callback=n;self.add_item(prev);self.add_item(nxt)
        submit=discord.ui.Button(label='보고 제출',style=discord.ButtonStyle.success,row=2)
        cancel=discord.ui.Button(label='취소',style=discord.ButtonStyle.secondary,row=2)
        submit.callback=self.submit;cancel.callback=self.cancel
        self.add_item(submit);self.add_item(cancel)

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message('보고서를 작성한 사용자만 조작할 수 있습니다.',ephemeral=True)
            return False
        return True

    async def refresh(self,interaction):
        self.build();await interaction.response.edit_message(embed=self.embed(),view=self)

    async def submit(self,interaction):
        if not self.selected_ids:
            await interaction.response.send_message('참고한 보고서를 한 건 이상 선택해 주세요.',ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        try:
            dest=await asyncio.to_thread(store.submit,self.user_id,self.character_name,self.title,self.content,sorted(self.selected_ids))
        except ValueError as ex:
            await interaction.followup.send(str(ex),ephemeral=True);return
        self.stop()
        await interaction.edit_original_response(content=f'**{self.title}** 보고서를 {dest} 보고함에 제출했습니다. (참고 보고서 {len(self.selected_ids)}건)',embed=None,view=None)

    async def cancel(self,interaction):
        self.stop();await interaction.response.edit_message(content='보고서 작성을 취소했습니다.',embed=None,view=None)


class RejectModal(discord.ui.Modal,title='보고서 반려'):
    reason = discord.ui.TextInput(label='반려 사유',style=discord.TextStyle.paragraph,required=False,max_length=500)
    def __init__(self,view):
        super().__init__();self.view_ref=view
    async def on_submit(self,interaction):
        view=self.view_ref
        try:
            await asyncio.to_thread(store.decide,interaction.user.id,view.character_name,view.selected_id,False,str(self.reason))
        except ValueError as ex:
            await interaction.response.send_message(str(ex),ephemeral=True);return
        await view.refresh(interaction,'보고서를 반려했습니다.')


class InboxSelect(discord.ui.Select):
    def __init__(self,view):
        self.parent_view=view
        options=[discord.SelectOption(label=(r['title']+(' (반려)' if r['status']=='returned' else ''))[:100],value=str(r['report_id']),description=f"{r['author_name']} · {r['author_job']}"[:100]) for r in view.page_rows]
        super().__init__(placeholder='확인할 보고서를 선택하세요',options=options,row=0)
    async def callback(self,interaction):
        self.parent_view.selected_id=int(self.values[0]);await self.parent_view.refresh(interaction)


class InboxView(discord.ui.View):
    def __init__(self,user_id,character_name,job,rows):
        super().__init__(timeout=600)
        self.user_id=user_id;self.character_name=character_name;self.job=job;self.rows=rows;self.page=0
        self.selected_id=rows[0]['report_id'];self.build()
    @property
    def page_rows(self):
        return self.rows[self.page*25:(self.page+1)*25]
    def selected(self):
        return next((r for r in self.rows if r['report_id']==self.selected_id),self.rows[0])
    def build(self):
        self.clear_items();self.add_item(InboxSelect(self))
        if len(self.rows)>25:
            prev=discord.ui.Button(label='<',style=discord.ButtonStyle.secondary,row=1,disabled=self.page==0)
            nxt=discord.ui.Button(label='>',style=discord.ButtonStyle.secondary,row=1,disabled=(self.page+1)*25>=len(self.rows))
            async def go(interaction,delta):
                self.page+=delta;self.selected_id=self.page_rows[0]['report_id'];await self.refresh(interaction)
            async def p(interaction):await go(interaction,-1)
            async def n(interaction):await go(interaction,1)
            prev.callback=p;nxt.callback=n;self.add_item(prev);self.add_item(nxt)
        if self.job=='간부직':
            approve=discord.ui.Button(label='승인',style=discord.ButtonStyle.success,row=2)
            reject=discord.ui.Button(label='반려',style=discord.ButtonStyle.danger,row=2)
            async def yes(interaction):
                try:await asyncio.to_thread(store.decide,interaction.user.id,self.character_name,self.selected_id,True)
                except ValueError as ex:await interaction.response.send_message(str(ex),ephemeral=True);return
                await self.refresh(interaction,'보고서를 승인했습니다.')
            async def no(interaction):await interaction.response.send_modal(RejectModal(self))
            approve.callback=yes;reject.callback=no;self.add_item(approve);self.add_item(reject)
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message('보고함을 연 사용자만 조작할 수 있습니다.',ephemeral=True);return False
        return True
    async def refresh(self,interaction,notice=None):
        if notice:
            _,self.rows=await asyncio.to_thread(store.inbox,self.user_id,self.character_name)
            if not self.rows:
                self.stop();await interaction.response.edit_message(content=notice+' 남은 보고서가 없습니다.',embed=None,view=None);return
            self.page=min(self.page,(len(self.rows)-1)//25)
            self.selected_id=self.page_rows[0]['report_id']
        self.build();await interaction.response.edit_message(content=notice,embed=report_embed(self.selected()),view=self)


class DocumentView(discord.ui.View):
    def __init__(self,owner,rows):
        super().__init__(timeout=600);self.owner=owner;self.rows=rows;self.index=0
        self.prev=discord.ui.Button(label='<',style=discord.ButtonStyle.secondary)
        self.next=discord.ui.Button(label='>',style=discord.ButtonStyle.secondary)
        self.prev.callback=self.move_prev;self.next.callback=self.move_next
        self.add_item(self.prev);self.add_item(self.next);self.update()
    def update(self):
        self.prev.disabled=self.index==0;self.next.disabled=self.index==len(self.rows)-1
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner:
            await interaction.response.send_message('열람을 시작한 사용자만 페이지를 넘길 수 있습니다.',ephemeral=True);return False
        return True
    async def move_prev(self,interaction):await self.move(interaction,-1)
    async def move_next(self,interaction):await self.move(interaction,1)
    async def move(self,interaction,delta):
        self.index+=delta;self.update();await interaction.response.edit_message(embed=report_embed(self.rows[self.index]),view=self)


class Reports(commands.Cog):
    def __init__(self,bot):self.bot=bot
    @app_commands.command(name='보고',description='직군별 담당 부서에 보고서를 제출합니다.')
    @app_commands.describe(보고서_이름='보고서 제목',내용='보고 내용')
    async def submit(self,interaction:discord.Interaction,보고서_이름:str,내용:str):
        캐릭터_이름 = await _active_name(interaction)
        if 캐릭터_이름 is None: return
        try:
            c,rows=await asyncio.to_thread(store.prepare,interaction.user.id,캐릭터_이름,보고서_이름,내용)
        except ValueError as ex:
            await interaction.response.send_message(str(ex),ephemeral=True);return
        if c['job']=='사무직':
            view=SourceView(interaction.user.id,캐릭터_이름,보고서_이름,내용,rows)
            await interaction.response.send_message(embed=view.embed(),view=view,ephemeral=True)
            return
        try:
            dest=await asyncio.to_thread(store.submit,interaction.user.id,캐릭터_이름,보고서_이름,내용)
        except ValueError as ex:
            await interaction.response.send_message(str(ex),ephemeral=True);return
        await interaction.response.send_message(f'**{보고서_이름}** 보고서를 {dest} 보고함에 제출했습니다.')

    @app_commands.command(name='보고확인',description='직군별 접수된 보고서와 반려 보고서를 확인합니다.')
    async def inbox(self,interaction:discord.Interaction):
        캐릭터_이름 = await _active_name(interaction)
        if 캐릭터_이름 is None: return
        try:c,rows=await asyncio.to_thread(store.inbox,interaction.user.id,캐릭터_이름)
        except ValueError as ex:await interaction.response.send_message(str(ex),ephemeral=True);return
        if not rows:await interaction.response.send_message('현재 확인할 보고서가 없습니다.',ephemeral=True);return
        view=InboxView(interaction.user.id,캐릭터_이름,c['job'],rows)
        await interaction.response.send_message(embed=report_embed(rows[0]),view=view,ephemeral=False)

    @app_commands.command(name='서류모음',description='승인된 공식 보고서를 한 장씩 열람합니다.')
    async def documents(self,interaction:discord.Interaction):
        rows=await asyncio.to_thread(store.approved)
        if not rows:await interaction.response.send_message('승인된 서류가 없습니다.',ephemeral=True);return
        view=DocumentView(interaction.user.id,rows)
        await interaction.response.send_message(embed=report_embed(rows[0]),view=view)

async def setup(bot):await bot.add_cog(Reports(bot))
