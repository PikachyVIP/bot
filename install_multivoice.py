import discord
from discord.ext import commands


async def setup(bot: commands.Bot):
    @bot.tree.command(name="install_multivoice", description="Создает систему временных голосовых каналов")
    async def install_multivoice(interaction: discord.Interaction):
        """Создает категорию и триггер-канал для временных голосовых каналов"""
        category_name = "[🔒] Временные каналы"
        trigger_channel_name = "┏[➕]┓🔒Создать"

        existing_category = discord.utils.get(interaction.guild.categories, name=category_name)
        if existing_category:
            trigger_channel = discord.utils.get(existing_category.voice_channels, name=trigger_channel_name)
            if trigger_channel:
                await interaction.response.send_message(
                    "❌ Система уже установлена!",
                    ephemeral=True
                )
                return

        try:
            category = await interaction.guild.create_category(
                name=category_name,
                position=len(interaction.guild.categories)
            )

            await category.create_voice_channel(name=trigger_channel_name)

            await interaction.response.send_message(
                "✅ Система готова! Зайдите в канал '┏[➕]┓🔒Создать' для создания личного канала.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ У бота нет прав на управление каналами!",
                ephemeral=True
            )

    @bot.event
    async def on_voice_state_update(member, before, after):
        TRIGGER_NAME = "┏[➕]┓🔒Создать"
        CATEGORY_NAME = "[🔒] Временные каналы"
        CHANNEL_PREFIX = "🔊│"

        if after.channel and after.channel.name == TRIGGER_NAME:
            category = discord.utils.get(member.guild.categories, name=CATEGORY_NAME)
            if not category:
                return

            try:
                new_channel = await category.create_voice_channel(
                    name=f"{CHANNEL_PREFIX}{member.display_name}",
                    position=len(category.channels)
                )
                await member.move_to(new_channel)
            except discord.Forbidden:
                print(f"Не удалось создать канал для {member.display_name}")

        if before.channel:
            if (before.channel.category and
                    before.channel.category.name == CATEGORY_NAME and
                    before.channel.name != TRIGGER_NAME and
                    len(before.channel.members) == 0):

                try:
                    await before.channel.delete()
                except discord.Forbidden:
                    print(f"Не удалось удалить канал {before.channel.name}")