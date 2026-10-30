import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database import init_db

load_dotenv()


class StoryBot(commands.Bot):
    async def setup_hook(self):
        extensions = [
            "services.weekly_board",
            "cogs.admin",
            "cogs.game",
            "cogs.characters",
            "cogs.items",
            "cogs.item_admin",
            "cogs.consumables",
            "cogs.consumable_admin",
            "cogs.stat_admin",
            "cogs.levels",
            "cogs.story",
            "cogs.story_rules_admin",
            "cogs.transfers",
            "cogs.character_chat",
            "cogs.crafting",
            "cogs.reports",
            "cogs.shop"
        ]

        for extension in extensions:
            await self.load_extension(extension)
            print(f"[로드 완료] {extension}")

        synced = await self.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")


async def main():
    init_db()

    from services.characters import migrate as migrate_characters
    from services.items import migrate as migrate_items
    from services.consumables import migrate as migrate_consumables
    from services.story_engine import migrate as migrate_story
    from services.story_rules import migrate as migrate_story_rules

    migrate_characters()
    migrate_items()
    migrate_consumables()
    migrate_story()
    migrate_story_rules()

    print("전체 DB 초기화 및 마이그레이션 완료!")

    intents = discord.Intents.default()
    intents.message_content = True
    bot = StoryBot(command_prefix="!", intents=intents)

    async with bot:
        await bot.start(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())