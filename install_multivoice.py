import discord
from discord.ext import commands
from discord.ui import Select, View
import mysql.connector
from mysql.connector import Error
from datetime import datetime
from data import mysqlconf

# Конфиг MySQL (возьмите из основного файла)
MYSQL_CONFIG = mysqlconf



class ChannelSettingsMenu(View):
    def __init__(self, voice_channel):
        super().__init__(timeout=None)
        self.voice_channel = voice_channel

        options = [
            discord.SelectOption(label="Изменить название", value="rename", emoji="✏️"),
            discord.SelectOption(label="Лимит участников", value="limit", emoji="👥"),
            discord.SelectOption(label="Закрыть канал", value="lock", emoji="🔒"),
            discord.SelectOption(label="Открыть канал", value="unlock", emoji="🔓"),
        ]

        self.select = Select(
            placeholder="Выберите настройку...",
            options=options
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction):
        if interaction.user not in self.voice_channel.members:
            await interaction.response.send_message("Вы должны быть в голосовом канале!", ephemeral=True)
            return

        value = self.select.values[0]
        if value == "rename":
            modal = RenameModal(self.voice_channel)
            await interaction.response.send_modal(modal)
        elif value == "limit":
            modal = LimitModal(self.voice_channel)
            await interaction.response.send_modal(modal)
        elif value == "lock":
            await self.voice_channel.set_permissions(interaction.guild.default_role, connect=False)
            await interaction.response.send_message("🔒 Канал закрыт!", ephemeral=True)
        elif value == "unlock":
            await self.voice_channel.set_permissions(interaction.guild.default_role, connect=True)
            await interaction.response.send_message("🔓 Канал открыт!", ephemeral=True)


class RenameModal(discord.ui.Modal):
    def __init__(self, voice_channel):
        super().__init__(title="Изменение названия")
        self.voice_channel = voice_channel
        self.new_name = discord.ui.TextInput(
            label="Новое название",
            placeholder=f"Текущее: {voice_channel.name}",
            max_length=100
        )
        self.add_item(self.new_name)

    async def on_submit(self, interaction):
        await self.voice_channel.edit(name=self.new_name.value)
        await interaction.response.send_message(f"✅ Название изменено на: {self.new_name.value}", ephemeral=True)


class LimitModal(discord.ui.Modal):
    def __init__(self, voice_channel):
        super().__init__(title="Лимит участников")
        self.voice_channel = voice_channel
        self.limit = discord.ui.TextInput(
            label="Количество (0 = без лимита)",
            placeholder=f"Текущее: {voice_channel.user_limit or 'без лимита'}",
            max_length=2
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction):
        try:
            limit = int(self.limit.value)
            await self.voice_channel.edit(user_limit=max(0, min(99, limit)))
            msg = "✅ Лимит участников: " + (f"{limit}" if limit > 0 else "без лимита")
            await interaction.response.send_message(msg, ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Введите число от 0 до 99", ephemeral=True)


class VoiceSystem:
    def __init__(self):
        self.channel_prefix = "🔊│"

    async def get_voice_settings(self, guild_id):
        """Получаем настройки голосовой системы для сервера"""
        try:
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT category_id, trigger_channel_id 
                FROM voice_system 
                WHERE guild_id = %s
            """, (guild_id,))
            return cursor.fetchone()
        except Error as e:
            print(f"MySQL Error: {e}")
            return None
        finally:
            if conn.is_connected():
                conn.close()

    async def save_voice_settings(self, guild_id, category_id, trigger_channel_id):
        """Сохраняем настройки голосовой системы"""
        try:
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO voice_system (guild_id, category_id, trigger_channel_id, created_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                category_id = VALUES(category_id),
                trigger_channel_id = VALUES(trigger_channel_id)
            """, (guild_id, category_id, trigger_channel_id, datetime.now()))
            conn.commit()
        except Error as e:
            print(f"MySQL Error: {e}")
        finally:
            if conn.is_connected():
                conn.close()


async def setup(bot: commands.Bot):
    system = VoiceSystem()

    # Создаём таблицу при запуске
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS voice_system (
                guild_id BIGINT PRIMARY KEY,
                category_id BIGINT NOT NULL,
                trigger_channel_id BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except Error as e:
        print(f"MySQL Error: {e}")
    finally:
        if conn.is_connected():
            conn.close()

    @bot.tree.command(name="install_multivoice", description="Установка системы временных голосовых каналов")
    async def install_multivoice(interaction: discord.Interaction):
        """Установка системы с сохранением ID в БД"""
        try:
            # Создаём категорию
            category = await interaction.guild.create_category(
                name="[🔒] Временные каналы",
                position=0)

            # Создаём триггер-канал (ВНИЗУ категории)
            trigger_channel = await category.create_voice_channel(
                name="┏[➕]┓🔒Создать",
                position=1)

            # Сохраняем в БД
            await system.save_voice_settings(interaction.guild.id, category.id, trigger_channel.id)

            await interaction.response.send_message(
                "✅ Система готова! Зайдите в нижний канал категории для создания личного канала.",
                ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка: {e}",
                ephemeral=True)

    @bot.event
    async def on_voice_state_update(member, before, after):
        # Получаем настройки из БД
        settings = await system.get_voice_settings(member.guild.id)
        if not settings:
            return

        try:
            # 1. Обработка входа в триггер-канал
            if after.channel and after.channel.id == settings['trigger_channel_id']:
                category = member.guild.get_channel(settings['category_id'])
                if not category:
                    return

                # Создаём новый канал ПОД триггером
                new_channel = await category.create_voice_channel(
                    name=f"{system.channel_prefix}{member.display_name}",
                    position=len(category.channels)
                )
                await member.move_to(new_channel)

                # Отправляем меню управления в текстовый чат
                text_channel = discord.utils.get(member.guild.text_channels, name="общий")  # Или другой канал
                if text_channel:
                    embed = discord.Embed(
                        title=f"Управление каналом {new_channel.name}",
                        description="Используйте меню ниже для настройки вашего голосового канала",
                        color=discord.Color.blue()
                    )
                    await text_channel.send(
                        content=f"{member.mention}, вот панель управления вашим каналом:",
                        embed=embed,
                        view=ChannelSettingsMenu(new_channel)
                    )

            # 2. Удаление пустых каналов
            if (before.channel
                    and before.channel.category_id == settings['category_id']
                    and before.channel.id != settings['trigger_channel_id']
                    and before.channel.name.startswith(system.channel_prefix)
                    and len(before.channel.members) == 0):
                await before.channel.delete()

        except Exception as e:
            print(f"Voice System Error: {e}")