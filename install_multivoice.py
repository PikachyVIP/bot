import discord
from discord.ext import commands
from discord.ui import Select, View
import mysql.connector
from datetime import datetime
from mysql.connector import Error
from data import mysqlconf


MYSQL_CONFIG = mysqlconf



class VoiceSystem:
    def __init__(self):
        self.channel_prefix = "🔊│"

    async def get_db_connection(self):
        try:
            return mysql.connector.connect(**MYSQL_CONFIG)
        except Error as e:
            print(f"MySQL Error: {e}")
            return None

    async def get_voice_settings(self, guild_id):
        conn = await self.get_db_connection()
        if not conn:
            return None

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT category_id, trigger_channel_id 
                FROM voice_system 
                WHERE guild_id = %s
            """, (guild_id,))
            return cursor.fetchone()
        finally:
            if conn.is_connected():
                conn.close()

    async def save_voice_settings(self, guild_id, category_id, trigger_channel_id):
        conn = await self.get_db_connection()
        if not conn:
            return

        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO voice_system (guild_id, category_id, trigger_channel_id, created_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                category_id = VALUES(category_id),
                trigger_channel_id = VALUES(trigger_channel_id)
            """, (guild_id, category_id, trigger_channel_id, datetime.now()))
            conn.commit()
        finally:
            if conn.is_connected():
                conn.close()


async def setup(bot: commands.Bot):
    system = VoiceSystem()

    # Инициализация БД
    conn = await system.get_db_connection()
    if conn:
        try:
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
        try:
            # Создаем категорию
            category = await interaction.guild.create_category(
                name="[🔒] Временные каналы",
                position=0)

            # Создаем триггер-канал НА САМОМ ВЕРХУ (позиция 0)
            trigger_channel = await category.create_voice_channel(
                name="┏[➕]┓🔒Создать",
                position=0)

            # Сохраняем в БД
            await system.save_voice_settings(interaction.guild.id, category.id, trigger_channel.id)

            await interaction.response.send_message(
                "✅ Система готова! Зайдите в верхний канал категории для создания личного канала.",
                ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка: {e}",
                ephemeral=True)

    @bot.event
    async def on_voice_state_update(member, before, after):
        settings = await system.get_voice_settings(member.guild.id)
        if not settings:
            return

        try:
            # 1. Обработка входа в триггер-канал
            if after.channel and after.channel.id == settings['trigger_channel_id']:
                category = member.guild.get_channel(settings['category_id'])
                if not category:
                    return

                # Создаем новый канал ПОД триггером (позиция 1)
                new_channel = await category.create_voice_channel(
                    name=f"{system.channel_prefix}{member.display_name}",
                    position=1
                )
                await member.move_to(new_channel)

                # Отправляем embed в чат голосового канала (только создателю)
                embed = discord.Embed(
                    title="Управление голосовым каналом",
                    description="Используйте меню ниже для настройки вашего канала",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="Доступные действия",
                    value="• Переименовать канал\n• Установить лимит участников\n• Закрыть/открыть канал",
                    inline=False
                )

                view = ChannelControlView(new_channel, member)
                await new_channel.send(embed=embed, view=view)

            # 2. Удаление пустых каналов
            if (before.channel
                    and before.channel.category_id == settings['category_id']
                    and before.channel.id != settings['trigger_channel_id']
                    and before.channel.name.startswith(system.channel_prefix)
                    and len(before.channel.members) == 0):
                await before.channel.delete()

        except Exception as e:
            print(f"Voice System Error: {e}")


class ChannelControlView(View):
    def __init__(self, voice_channel, owner):
        super().__init__(timeout=None)
        self.voice_channel = voice_channel
        self.owner = owner

        self.select = Select(
            placeholder="Выберите действие...",
            options=[
                discord.SelectOption(label="Переименовать", value="rename", emoji="✏️"),
                discord.SelectOption(label="Лимит участников", value="limit", emoji="👥"),
                discord.SelectOption(label="Закрыть канал", value="lock", emoji="🔒"),
                discord.SelectOption(label="Открыть канал", value="unlock", emoji="🔓")
            ]
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction):
        # Проверяем, что это создатель канала
        if interaction.user != self.owner:
            await interaction.response.send_message(
                "❌ Только создатель канала может управлять им!",
                ephemeral=True
            )
            return

        value = self.select.values[0]

        if value == "rename":
            await interaction.response.send_modal(RenameModal(self.voice_channel))
        elif value == "limit":
            await interaction.response.send_modal(LimitModal(self.voice_channel))
        elif value == "lock":
            await self.voice_channel.set_permissions(
                interaction.guild.default_role,
                connect=False
            )
            await interaction.response.send_message(
                "🔒 Канал закрыт для всех участников!",
                ephemeral=True
            )
        elif value == "unlock":
            await self.voice_channel.set_permissions(
                interaction.guild.default_role,
                connect=True
            )
            await interaction.response.send_message(
                "🔓 Канал открыт для всех участников!",
                ephemeral=True
            )


class RenameModal(discord.ui.Modal):
    def __init__(self, voice_channel):
        super().__init__(title="Переименовать голосовой канал")
        self.voice_channel = voice_channel
        self.new_name = discord.ui.TextInput(
            label="Введите новое название",
            placeholder=f"Текущее: {voice_channel.name}",
            max_length=100
        )
        self.add_item(self.new_name)

    async def on_submit(self, interaction):
        await self.voice_channel.edit(name=self.new_name.value)
        await interaction.response.send_message(
            f"✅ Канал переименован в: {self.new_name.value}",
            ephemeral=True
        )


class LimitModal(discord.ui.Modal):
    def __init__(self, voice_channel):
        super().__init__(title="Установить лимит участников")
        self.voice_channel = voice_channel
        self.limit = discord.ui.TextInput(
            label="Введите число (0 = без лимита)",
            placeholder=f"Текущее: {voice_channel.user_limit or 'без лимита'}",
            max_length=2
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction):
        try:
            limit = max(0, min(99, int(self.limit.value)))
            await self.voice_channel.edit(user_limit=limit)
            await interaction.response.send_message(
                f"✅ Лимит участников установлен: {limit if limit > 0 else 'без лимита'}",
                ephemeral=True
            )
        except ValueError:
            await interaction.response.send_message(
                "❌ Пожалуйста, введите число от 0 до 99",
                ephemeral=True
            )