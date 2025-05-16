import discord
from discord.ext import commands
import mysql.connector
from mysql.connector import Error
from datetime import datetime
from data import mysqlconf

# Конфиг MySQL (возьмите из основного файла)
MYSQL_CONFIG = mysqlconf


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
                position=1)  # Позиция 1 (после возможных других каналов)

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
                    position=len(category.channels)  # В самый низ
                )
                await member.move_to(new_channel)

            # 2. Удаление пустых каналов (исправленное условие)
            if (before.channel
                    and before.channel.category_id == settings['category_id']
                    and before.channel.id != settings['trigger_channel_id']
                    and before.channel.name.startswith(system.channel_prefix)
                    and len(before.channel.members) == 0):
                await before.channel.delete()

        except Exception as e:
            print(f"Voice System Error: {e}")