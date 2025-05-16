import discord
from discord.ext import commands


class VoiceSystem:
    def __init__(self):
        self.trigger_prefix = "┏[➕]┓"  # Идентификатор триггера
        self.category_prefix = "[🔒]"  # Идентификатор категории
        self.channel_prefix = "🔊│"


async def setup(bot: commands.Bot):
    system = VoiceSystem()

    # Установка системы
    @bot.tree.command(name="install_multivoice", description="Установка системы временных голосовых каналов")
    async def install_multivoice(interaction: discord.Interaction):
        try:
            # Создаём категорию
            category = await interaction.guild.create_category(
                name=f"{system.category_prefix} Временные каналы",
                position=0)  # В самом верху списка

            # Создаём триггер-канал (в позиции 0 - самый верхний в категории)
            await category.create_voice_channel(
                name=f"{system.trigger_prefix}🔒Создать",
                position=0)

            await interaction.response.send_message(
                "✅ Система готова! Зайдите в верхний канал категории для создания личного канала.",
                ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка: {e}",
                ephemeral=True)

    # Обработка голосовых событий
    @bot.event
    async def on_voice_state_update(member, before, after):
        try:
            # 1. Определяем триггер-канал (верхний в своей категории)
            if after.channel and after.channel.category:
                category = after.channel.category
                if category.channels and after.channel == category.channels[0]:  # Самый верхний канал
                    # Создаём новый канал под триггером (позиция 1)
                    new_channel = await category.create_voice_channel(
                        name=f"{system.channel_prefix}{member.display_name}",
                        position=1)
                    await member.move_to(new_channel)

            # 2. Удаляем пустые каналы (кроме триггера)
            if before.channel and before.channel.category:
                category = before.channel.category
                if (len(before.channel.members) == 0
                        and before.channel != category.channels[0]  # Не триггер
                        and before.channel.name.startswith(system.channel_prefix)):
                    await before.channel.delete()
        except Exception as e:
            print(f"Voice System Error: {e}")