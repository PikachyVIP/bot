import discord
from discord import app_commands
from discord.ext import commands


async def setup(bot: commands.Bot):
    @bot.tree.command(name="install_multivoice", description="Создает категорию для временных голосовых каналов")
    async def install_multivoice(interaction: discord.Interaction):
        """Создает категорию '[🔒] Временные каналы' и канал '┏[➕]┓🔒Создать'"""
        category_name = "[🔒] Временные каналы"
        channel_name = "┏[➕]┓🔒Создать"

        # Проверка существования
        for category in interaction.guild.categories:
            if category.name == category_name:
                await interaction.response.send_message(
                    "❌ Категория и канал уже существуют!",
                    ephemeral=True
                )
                return

        try:
            # Создание категории (в конце списка)
            category = await interaction.guild.create_category(
                name=category_name,
                position=len(interaction.guild.categories)  # Последняя позиция
            )

            # Создание голосового канала
            await category.create_voice_channel(name=channel_name)

            await interaction.response.send_message(
                "✅ Категория и канал успешно созданы!",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ У бота нет прав на управление каналами!",
                ephemeral=True
            )