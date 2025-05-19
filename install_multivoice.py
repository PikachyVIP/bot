import discord
from discord.ext import commands
from discord.ui import Select, View
import mysql.connector
from datetime import datetime
from mysql.connector import Error
from data import mysqlconf

import Main

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

        if not await Main.check_command_access_app(interaction):
            return await interaction.response.send_message(
                "❌ Недостаточно прав",
                ephemeral=True
            )

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

                # Получаем текущую позицию триггер-канала
                trigger_channel = member.guild.get_channel(settings['trigger_channel_id'])
                trigger_position = trigger_channel.position

                # Создаем новый канал ПОД триггером (позиция триггера + 1)
                new_channel = await category.create_voice_channel(
                    name=f"{system.channel_prefix}{member.display_name}",
                    position=trigger_position + 1  # Позиция сразу под триггером
                )
                await member.move_to(new_channel)

                # Отправляем embed в чат голосового канала
                embed = discord.Embed(
                    title="Управление голосовым каналом",
                    description="Используйте меню ниже для настройки",
                    color=discord.Color.blue()
                )
                view = ChannelControlView(new_channel, member)
                await new_channel.send(embed=embed, view=view)

            # 2. Удаление пустых каналов (исправленная проверка)
            if (before.channel
                    and before.channel.category_id == settings['category_id']
                    and before.channel.id != settings['trigger_channel_id']
                    and len(before.channel.members) == 0):

                try:
                    await before.channel.delete()
                except:
                    pass

        except Exception as e:
            print(f"Voice System Error: {e}")


class ChannelControlView(View):
    def __init__(self, voice_channel, owner):
        super().__init__(timeout=None)
        self.voice_channel = voice_channel
        self.owner = owner

        # Обновленные опции меню
        options = [
            discord.SelectOption(label="Переименовать", value="rename", emoji="✏️"),
            discord.SelectOption(label="Лимит участников", value="limit", emoji="👥"),
          #  discord.SelectOption(label="Закрыть канал", value="lock", emoji="🔒"),
          #  discord.SelectOption(label="Открыть канал", value="unlock", emoji="🔓"),
          #  discord.SelectOption(label="Призрачный режим", value="ghost", emoji="👻"),
          #  discord.SelectOption(label="Видимый режим", value="unghost", emoji="👀"),
           # discord.SelectOption(label="Пригласить", value="invite", emoji="✉️"),
          #  discord.SelectOption(label="Установить статус", value="status", emoji="📝")
        ]

        self.select = Select(
            placeholder="Выберите действие...",
            options=options
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction):
        if interaction.user != self.owner:
            await interaction.response.send_message("❌ Только создатель канала может управлять им!", ephemeral=True)
            return

        value = self.select.values[0]

        if value == "rename":
            await interaction.response.send_modal(RenameModal(self.voice_channel))

        elif value == "limit":
            await interaction.response.send_modal(LimitModal(self.voice_channel))

        elif value == "lock":
            # Закрываем для всех, кроме админов
            await self.voice_channel.set_permissions(
                interaction.guild.default_role,
                connect=False,
                view_channel=True  # Канал остается видимым
            )
            await interaction.response.send_message("🔒 Канал закрыт для всех, кроме администраторов!", ephemeral=True)

        elif value == "unlock":
            await self.voice_channel.set_permissions(
                interaction.guild.default_role,
                connect=True
            )
            await interaction.response.send_message("🔓 Канал открыт для всех участников!", ephemeral=True)

        elif value == "ghost":
            # Включаем призрачный режим (видят только админы)
            await self.voice_channel.set_permissions(
                interaction.guild.default_role,
                view_channel=False
            )
            await interaction.response.send_message("👻 Канал теперь виден только администраторам!", ephemeral=True)

        elif value == "unghost":
            await self.voice_channel.set_permissions(
                interaction.guild.default_role,
                view_channel=True
            )
            await interaction.response.send_message("👀 Канал теперь виден всем!", ephemeral=True)

        elif value == "invite":
            # Создаем приглашение
            invite = await self.voice_channel.create_invite(max_uses=1)
            try:
                await self.owner.send(f"🎫 Приглашение в канал: {invite.url}")
                await interaction.response.send_message("✅ Приглашение отправлено в ЛС!", ephemeral=True)
            except:
                await interaction.response.send_message("❌ Не удалось отправить ЛС. Проверьте настройки приватности!",
                                                        ephemeral=True)

        elif value == "status":
            await interaction.response.send_modal(StatusModal(self.voice_channel))


class StatusModal(discord.ui.Modal):
    def __init__(self, voice_channel):
        super().__init__(title="Установить статус канала")
        self.voice_channel = voice_channel
        self.status = discord.ui.TextInput(
            label="Введите статус (до 100 символов)",
            placeholder="Например: Играем в Valorant",
            max_length=100
        )
        self.add_item(self.status)

    async def on_submit(self, interaction):
        # Устанавливаем статус в название канала
        original_name = self.voice_channel.name.split('|')[0].strip()
        await self.voice_channel.edit(name=f"{original_name} | {self.status.value}")
        await interaction.response.send_message(f"📝 Статус установлен: {self.status.value}", ephemeral=True)

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