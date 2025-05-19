import re
from enum import Enum

import discord
from discord.ext import commands, tasks
from discord import app_commands
import mysql.connector
from datetime import datetime, timedelta
import asyncio
import json
from typing import Optional, Literal, Union
from mysql.connector import Error
from data import mysqlconf

class LoopInterval(Enum):
    NONE = "без повтора"
    WEEKLY = "каждую неделю"
    MONTHLY = "каждый месяц"
    YEARLY = "каждый год"



def calculate_next_date(self, original_date: datetime, interval: str) -> datetime:
    """Вычисляет следующую дату события"""
    if interval == LoopInterval.WEEKLY.name:
        return original_date + timedelta(weeks=1)
    elif interval == LoopInterval.MONTHLY.name:
        # Безопасное добавление месяца
        next_month = original_date.month + 1
        next_year = original_date.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        return original_date.replace(year=next_year, month=next_month)
    elif interval == LoopInterval.YEARLY.name:
        return original_date.replace(year=original_date.year + 1)
    return original_date


class EventCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_timers.start()
        self.active_messages = {}
        self.MYSQL_CONFIG = mysqlconf

    def get_db_connection(self):
        try:
            connection = mysql.connector.connect(**self.MYSQL_CONFIG)
            return connection
        except Error as e:
            print(f"Error connecting to MySQL: {e}")
            return None

    async def event_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str
    ) -> list[app_commands.Choice[str]]:
        connection = self.get_db_connection()
        if not connection:
            return []

        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT event_name FROM events WHERE event_name LIKE %s", (f"%{current}%",))
        events = cursor.fetchall()
        connection.close()

        return [
            app_commands.Choice(name=event['event_name'], value=event['event_name'])
            for event in events
        ]


    async def loop_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=interval.value, value=interval.name)
            for interval in LoopInterval
            if current.lower() in interval.value.lower()
        ]

    @app_commands.command(name="event", description="Управление событиями")
    @app_commands.describe(
        action="Выберите действие",
        name="Название события",
        date="Дата в формате DD:MM:YYYY",
        time="Время в формате HH:MM",
        loop="Повторение события",
        recipients="Укажите получателей (@user, @role или all)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="install", value="install"),
        app_commands.Choice(name="create", value="create"),
        app_commands.Choice(name="list", value="list"),
        app_commands.Choice(name="remove", value="remove")
    ])
    @app_commands.autocomplete(name=event_autocomplete, loop=loop_autocomplete)
    async def event_command(
            self,
            interaction: discord.Interaction,
            action: Literal["install", "create", "list", "remove"],
            name: Optional[str] = None,
            date: Optional[str] = None,
            time: Optional[str] = None,
            loop: Optional[str] = None,
            recipients: Optional[str] = None
    ):
        if action == "install":
            await self.install_event_system(interaction)
        elif action == "create":
            await self.create_event(interaction, name, date, time, loop, recipients)
        elif action == "list":
            await self.list_events(interaction)
        elif action == "remove":
            await self.remove_event(interaction, name)

    async def install_event_system(self, interaction: discord.Interaction):
        """Создает категорию и каналы для событий"""
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Требуются права администратора", ephemeral=True)

        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True)
        }

        try:
            category = await guild.create_category("📅 События")
            main_channel = await guild.create_text_channel(
                "события",
                category=category,
                overwrites=overwrites
            )
            log_channel = await guild.create_text_channel(
                "логи-событий",
                category=category,
                overwrites=overwrites
            )

            connection = self.get_db_connection()
            if connection:
                cursor = connection.cursor()

                # Обновляем структуру таблицы (добавляем log_channel_id если его нет)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS event_config (
                        guild_id BIGINT PRIMARY KEY,
                        channel_id BIGINT NOT NULL,
                        log_channel_id BIGINT NOT NULL,
                        category_id BIGINT NOT NULL
                    )
                """)

                # Вставляем данные (теперь с log_channel_id)
                cursor.execute("""
                    INSERT INTO event_config (guild_id, channel_id, log_channel_id, category_id)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                    channel_id = VALUES(channel_id),
                    log_channel_id = VALUES(log_channel_id),
                    category_id = VALUES(category_id)
                """, (guild.id, main_channel.id, log_channel.id, category.id))

                connection.commit()
                connection.close()

            await interaction.response.send_message(
                f"Система событий установлена!\n"
                f"Категория: {category.mention}\n"
                f"Основной канал: {main_channel.mention}\n"
                f"Лог-канал: {log_channel.mention}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Ошибка при создании системы: {str(e)}",
                ephemeral=True
            )

    # В методе send_notification (используем log_channel_id):
    async def send_notification(self, event):
        """Отправляет уведомление о наступлении события"""
        connection = self.get_db_connection()
        if not connection:
            return

        try:
            cursor = connection.cursor(dictionary=True)

            # Получаем лог-канал через channel_id из события
            cursor.execute("""
                SELECT log_channel_id FROM event_config 
                WHERE guild_id = (
                    SELECT guild_id FROM event_config 
                    WHERE channel_id = %s
                )
            """, (event['channel_id'],))
            config = cursor.fetchone()

            if config and config['log_channel_id']:
                log_channel = self.bot.get_channel(config['log_channel_id'])
                if log_channel:
                    recipients = json.loads(event['recipients'])
                    mentions = []
                    for r in recipients:
                        if r == "all":
                            mentions.append("@everyone")
                        elif r.startswith("role:"):
                            role_id = int(r.split(":")[1])
                            role = log_channel.guild.get_role(role_id)
                            if role:
                                mentions.append(role.mention)
                        elif r.startswith("user:"):
                            user_id = int(r.split(":")[1])
                            user = log_channel.guild.get_member(user_id)
                            if user:
                                mentions.append(user.mention)

                    loop_text = LoopInterval[event['loop_interval']].value if event['loop_interval'] else "без повтора"

                    embed = discord.Embed(
                        title="🔔 Событие началось!",
                        description=(
                            f"**Название:** {event['event_name']}\n"
                            f"**Тип:** {loop_text}\n"
                            f"**Для:** {' '.join(mentions) if mentions else 'всех участников'}"
                        ),
                        color=discord.Color.green()
                    )

                    await log_channel.send(
                        content=' '.join(mentions) if mentions else None,
                        embed=embed
                    )
        except Exception as e:
            print(f"Ошибка отправки уведомления: {e}")
        finally:
            if connection.is_connected():
                connection.close()

    async def create_event(
            self,
            interaction: discord.Interaction,
            name: str,
            date: str,
            time: str,
            loop: str,
            recipients: str
    ):
        """Создание циклического события"""
        try:
            # Проверка формата времени
            if not re.match(r'^\d{2}:\d{2}$', time):
                return await interaction.response.send_message(
                    "⏰ Неверный формат времени. Используйте HH:MM (например 15:30)",
                    ephemeral=True
                )

            # Парсим дату и время
            event_datetime = datetime.strptime(f"{date} {time}", "%d:%m:%Y %H:%M")

            connection = self.get_db_connection()
            if not connection:
                return await interaction.response.send_message(
                    "⚠️ Ошибка подключения к БД",
                    ephemeral=True
                )

            cursor = connection.cursor(dictionary=True)

            # Проверяем конфигурацию сервера
            cursor.execute(
                "SELECT channel_id FROM event_config WHERE guild_id = %s",
                (interaction.guild.id,)
            )
            config = cursor.fetchone()

            if not config:
                return await interaction.response.send_message(
                    "🔧 Сначала выполните /event install",
                    ephemeral=True
                )

            # Обработка получателей
            recipient_data = []
            if recipients.lower() == "all":
                recipient_data = ["all"]
            else:
                for mention in recipients.split():
                    if mention.startswith(('<@&', '<@')):
                        try:
                            entity_id = int(mention.strip('<@&>'))
                            if mention.startswith('<@&'):
                                recipient_data.append(f"role:{entity_id}")
                            else:
                                recipient_data.append(f"user:{entity_id}")
                        except ValueError:
                            continue

            if not recipient_data:
                return await interaction.response.send_message(
                    "👥 Не указаны получатели или указаны некорректно",
                    ephemeral=True
                )

            # Сохраняем событие в БД
            cursor.execute("""
                INSERT INTO events (
                    event_name,
                    event_date,
                    recipients,
                    channel_id,
                    loop_interval
                ) VALUES (%s, %s, %s, %s, %s)
            """, (
                name,
                event_datetime,
                json.dumps(recipient_data),
                config['channel_id'],
                loop
            ))
            connection.commit()
            event_id = cursor.lastrowid

            # Создаем сообщение с таймером
            event_channel = self.bot.get_channel(config['channel_id'])
            if event_channel:
                time_left = event_datetime - datetime.now()
                days = time_left.days
                hours, remainder = divmod(time_left.seconds, 3600)
                minutes, _ = divmod(remainder, 60)

                loop_text = LoopInterval[loop].value if loop else "без повтора"

                embed = discord.Embed(
                    title=f"🔔 Событие: {name}",
                    description=(
                        f"**🗓 Дата:** {event_datetime.strftime('%d.%m.%Y %H:%M')}\n"
                        f"**⏳ Осталось:** {days} дней, {hours} часов, {minutes} минут\n"
                        f"**🔄 Повтор:** {loop_text}"
                    ),
                    color=discord.Color.gold()
                )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    label="🔄 Обновить таймер",
                    custom_id=f"update_timer_{event_id}"
                ))
                view.add_item(discord.ui.Button(
                    style=discord.ButtonStyle.secondary,
                    label="📋 Показать список",
                    custom_id=f"show_list_{event_id}"
                ))

                message = await event_channel.send(embed=embed, view=view)
                self.active_messages[event_id] = message.id

                await interaction.response.send_message(
                    f"✅ Событие '{name}' создано!\n"
                    f"Следующее выполнение: {event_datetime.strftime('%d.%m.%Y %H:%M')}\n"
                    f"Повтор: {loop_text}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Канал событий не найден",
                    ephemeral=True
                )

        except Exception as e:
            await interaction.response.send_message(
                f"⚠️ Ошибка: {str(e)}",
                ephemeral=True
            )
        finally:
            if connection and connection.is_connected():
                connection.close()

    @tasks.loop(minutes=1)
    async def update_timers(self):
        """Обновление циклических событий"""
        connection = self.get_db_connection()
        if not connection:
            return

        cursor = connection.cursor(dictionary=True)

        try:
            # Получаем все активные события
            cursor.execute("SELECT * FROM events")
            events = cursor.fetchall()

            for event in events:
                event_datetime = event['event_date']
                now = datetime.now()

                # Если событие наступило
                if event_datetime <= now:
                    # Отправляем уведомление
                    await self.send_notification(event)

                    # Обновляем дату для повторяющихся событий
                    if event['loop_interval'] and event['loop_interval'] != 'NONE':
                        new_date = calculate_next_date(
                            event_datetime,
                            event['loop_interval']
                        )

                        cursor.execute("""
                            UPDATE events SET event_date = %s
                            WHERE event_id = %s
                        """, (new_date, event['event_id']))
                        connection.commit()

                        # Обновляем сообщение с таймером
                        if event['event_id'] in self.active_messages:
                            try:
                                channel = self.bot.get_channel(event['channel_id'])
                                if channel:
                                    message = await channel.fetch_message(
                                        self.active_messages[event['event_id']]
                                    )

                                    time_left = new_date - datetime.now()
                                    days = time_left.days
                                    hours, remainder = divmod(time_left.seconds, 3600)
                                    minutes, _ = divmod(remainder, 60)

                                    loop_text = LoopInterval[event['loop_interval']].value if event[
                                        'loop_interval'] else "без повтора"

                                    embed = discord.Embed(
                                        title=f"🔔 Событие: {event['event_name']}",
                                        description=(
                                            f"**🗓 Дата:** {new_date.strftime('%d.%m.%Y %H:%M')}\n"
                                            f"**⏳ Осталось:** {days} дней, {hours} часов, {minutes} минут\n"
                                            f"**🔄 Повтор:** {loop_text}"
                                        ),
                                        color=discord.Color.gold()
                                    )

                                    view = discord.ui.View()
                                    view.add_item(discord.ui.Button(
                                        style=discord.ButtonStyle.primary,
                                        label="🔄 Обновить таймер",
                                        custom_id=f"update_timer_{event['event_id']}"
                                    ))
                                    view.add_item(discord.ui.Button(
                                        style=discord.ButtonStyle.secondary,
                                        label="📋 Показать список",
                                        custom_id=f"show_list_{event['event_id']}"
                                    ))

                                    await message.edit(embed=embed, view=view)
                            except Exception as e:
                                print(f"Ошибка обновления сообщения: {e}")
                    else:
                        # Для неповторяющихся событий удаляем сообщение
                        if event['event_id'] in self.active_messages:
                            try:
                                channel = self.bot.get_channel(event['channel_id'])
                                if channel:
                                    message = await channel.fetch_message(
                                        self.active_messages[event['event_id']]
                                    )
                                    await message.delete()
                            except:
                                pass
                            finally:
                                del self.active_messages[event['event_id']]
        finally:
            if connection.is_connected():
                connection.close()

    async def list_events(self, interaction: discord.Interaction):
        """Показывает список всех событий"""
        connection = self.get_db_connection()
        if not connection:
            return await interaction.response.send_message(
                "Ошибка подключения к БД",
                ephemeral=True
            )

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM events 
            WHERE event_date > NOW() 
            ORDER BY event_date
        """)
        events = cursor.fetchall()
        connection.close()

        if not events:
            return await interaction.response.send_message(
                "Нет активных событий",
                ephemeral=True
            )

        embed = discord.Embed(
            title="Список событий",
            color=discord.Color.green()
        )

        for event in events:
            event_date = event['event_date']
            time_left = event_date - datetime.now()
            days = time_left.days
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)

            recipients = json.loads(event['recipients'])
            recipient_text = []
            for r in recipients:
                if r == "all":
                    recipient_text.append("Все участники")
                elif r.startswith("role:"):
                    role_id = int(r.split(":")[1])
                    role = interaction.guild.get_role(role_id)
                    recipient_text.append(f"Роль: {role.mention if role else 'Не найдена'}")
                elif r.startswith("user:"):
                    user_id = int(r.split(":")[1])
                    user = interaction.guild.get_member(user_id)
                    recipient_text.append(f"Пользователь: {user.mention if user else 'Не найден'}")

            embed.add_field(
                name=event['event_name'],
                value=f"**Дата:** {event_date.strftime('%d.%m.%Y %H:%M')}\n"
                      f"**Осталось:** {days}д {hours}ч {minutes}м\n"
                      f"**Для:** {', '.join(recipient_text)}\n"
                      f"**ID:** {event['event_id']}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def remove_event(self, interaction: discord.Interaction, name: str):
        """Удаляет событие и связанные данные"""
        connection = self.get_db_connection()
        if not connection:
            return await interaction.response.send_message(
                "Ошибка подключения к БД",
                ephemeral=True
            )

        cursor = connection.cursor(dictionary=True)

        try:
            # Начинаем транзакцию
            connection.start_transaction()

            # 1. Находим событие
            cursor.execute(
                "SELECT event_id FROM events WHERE event_name = %s",
                (name,)
            )
            event = cursor.fetchone()

            if not event:
                await interaction.response.send_message(
                    f"Событие '{name}' не найдено",
                    ephemeral=True
                )
                return

            # 2. Удаляем связанные уведомления первыми
            cursor.execute(
                "DELETE FROM event_notifications WHERE event_id = %s",
                (event['event_id'],)
            )

            # 3. Удаляем сообщение с таймером, если оно существует
            if event['event_id'] in self.active_messages:
                try:
                    cursor.execute(
                        "SELECT channel_id FROM events WHERE event_id = %s",
                        (event['event_id'],)
                    )
                    event_data = cursor.fetchone()
                    if event_data:
                        channel = self.bot.get_channel(event_data['channel_id'])
                        if channel:
                            try:
                                message = await channel.fetch_message(
                                    self.active_messages[event['event_id']]
                                )
                                await message.delete()
                            except:
                                pass
                except:
                    pass
                finally:
                    del self.active_messages[event['event_id']]

            # 4. Удаляем само событие
            cursor.execute(
                "DELETE FROM events WHERE event_name = %s",
                (name,)
            )

            # Подтверждаем транзакцию
            connection.commit()

            await interaction.response.send_message(
                f"✅ Событие '{name}' успешно удалено!",
                ephemeral=True
            )

        except Exception as e:
            # Откатываем транзакцию при ошибке
            connection.rollback()
            await interaction.response.send_message(
                f"❌ Не удалось удалить событие '{name}': {str(e)}",
                ephemeral=True
            )
        finally:
            if connection.is_connected():
                connection.close()

    @tasks.loop(minutes=1)
    async def update_timers(self):
        """Обновляет таймеры событий и проверяет наступление событий"""
        connection = self.get_db_connection()
        if not connection:
            return

        cursor = connection.cursor(dictionary=True)

        try:
            # Получаем все события
            cursor.execute("SELECT * FROM events")
            all_events = cursor.fetchall()

            for event in all_events:
                event_datetime = event['event_date']
                now = datetime.now()

                # Если событие еще не наступило или уже перенесено на следующий период
                if event_datetime > now or (event['loop_interval'] and event['loop_interval'] != 'NONE'):
                    time_left = event_datetime - now
                    days = time_left.days
                    hours, remainder = divmod(time_left.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)

                    # Обновляем сообщение с таймером
                    if event['event_id'] in self.active_messages:
                        try:
                            channel = self.bot.get_channel(event['channel_id'])
                            if channel:
                                message = await channel.fetch_message(
                                    self.active_messages[event['event_id']]
                                )

                                # Единый стиль embed
                                embed = discord.Embed(
                                    title=f"🔔 Событие: {event['event_name']}",
                                    description=(
                                        f"**Дата:** {event_datetime.strftime('%d.%m.%Y %H:%M')}\n"
                                        f"**Осталось:** {days} дней, {hours} часов, {minutes} минут\n"
                                        f"**Тип:** {LoopInterval[event['loop_interval']].value if event['loop_interval'] else 'Одноразовое'}"
                                    ),
                                    color=discord.Color.gold()
                                )

                                view = discord.ui.View()
                                view.add_item(discord.ui.Button(
                                    style=discord.ButtonStyle.primary,
                                    label="🔄 Обновить",
                                    custom_id=f"update_timer_{event['event_id']}"
                                ))
                                view.add_item(discord.ui.Button(
                                    style=discord.ButtonStyle.secondary,
                                    label="📋 Список",
                                    custom_id=f"show_list_{event['event_id']}"
                                ))

                                await message.edit(embed=embed, view=view)
                        except Exception as e:
                            print(f"Ошибка обновления таймера: {e}")

                # Если событие наступило
                if event_datetime <= now:
                    # Проверяем, было ли уже уведомление
                    cursor.execute("""
                        SELECT 1 FROM event_notifications 
                        WHERE event_id = %s AND notified = 1
                    """, (event['event_id'],))
                    already_notified = cursor.fetchone()

                    if not already_notified:
                        # Отправляем уведомление
                        await self.send_notification(event)

                        # Помечаем как уведомленное
                        cursor.execute("""
                            INSERT INTO event_notifications (event_id, notified)
                            VALUES (%s, 1)
                            ON DUPLICATE KEY UPDATE notified = 1
                        """, (event['event_id'],))
                        connection.commit()

                    # Для повторяющихся событий обновляем дату
                    if event['loop_interval'] and event['loop_interval'] != 'NONE':
                        new_date = calculate_next_date(event_datetime, event['loop_interval'])

                        cursor.execute("""
                            UPDATE events SET event_date = %s
                            WHERE event_id = %s
                        """, (new_date, event['event_id']))
                        connection.commit()

                        # Обновляем ID сообщения, если оно было пересоздано
                        if event['event_id'] not in self.active_messages:
                            try:
                                channel = self.bot.get_channel(event['channel_id'])
                                if channel:
                                    async for message in channel.history(limit=100):
                                        if message.embeds and f"Событие: {event['event_name']}" in message.embeds[
                                            0].title:
                                            self.active_messages[event['event_id']] = message.id
                                            break
                            except:
                                pass
                    else:
                        # Для одноразовых событий удаляем сообщение
                        if event['event_id'] in self.active_messages:
                            try:
                                channel = self.bot.get_channel(event['channel_id'])
                                if channel:
                                    message = await channel.fetch_message(
                                        self.active_messages[event['event_id']]
                                    )
                                    await message.delete()
                            except:
                                pass
                            finally:
                                del self.active_messages[event['event_id']]
        finally:
            if connection.is_connected():
                connection.close()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Обрабатывает нажатия кнопок"""
        if not interaction.type == discord.InteractionType.component:
            return

        custom_id = interaction.data.get('custom_id', '')

        # Обработка кнопки "Обновить таймер"
        if custom_id.startswith('update_timer_'):
            event_id = int(custom_id.split('_')[2])
            await self.update_single_timer(interaction, event_id)

        # Обработка кнопки "Показать список"
        elif custom_id.startswith('show_list_'):
            event_id = int(custom_id.split('_')[2])
            await self.show_event_list(interaction, event_id)

    async def update_single_timer(self, interaction: discord.Interaction, event_id: int):
        """Обновляет таймер для конкретного события"""
        await interaction.response.defer(ephemeral=True)

        connection = self.get_db_connection()
        if not connection:
            return await interaction.followup.send(
                "⚠️ Ошибка подключения к БД",
                ephemeral=True
            )

        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM events WHERE event_id = %s",
                (event_id,)
            )
            event = cursor.fetchone()

            if not event:
                return await interaction.followup.send(
                    "❌ Событие не найдено",
                    ephemeral=True
                )

            event_date = event['event_date']
            now = datetime.now()

            # Если событие уже прошло, переносим на следующий год
            if event_date < now:
                event_date = event_date.replace(year=event_date.year + 1)
                cursor.execute(
                    "UPDATE events SET event_date = %s WHERE event_id = %s",
                    (event_date, event_id)
                )
                connection.commit()

            time_left = event_date - now
            days = time_left.days
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)

            # Обновляем сообщение
            channel = self.bot.get_channel(event['channel_id'])
            if channel:
                try:
                    message = await channel.fetch_message(
                        self.active_messages[event_id]
                    )

                    embed = discord.Embed(
                        title=f"🔔 Событие: {event['event_name']}",
                        description=(
                            f"**🗓 Дата:** {event_date.strftime('%d.%m.%Y %H:%M')}\n"
                            f"**⏳ Осталось:** {days} дней, {hours} часов, {minutes} минут\n"
                            f"**🔄 Автоповтор:** Каждый год"
                        ),
                        color=discord.Color.gold()
                    )

                    view = discord.ui.View()
                    view.add_item(discord.ui.Button(
                        style=discord.ButtonStyle.primary,
                        label="🔄 Обновить таймер",
                        custom_id=f"update_timer_{event_id}"
                    ))
                    view.add_item(discord.ui.Button(
                        style=discord.ButtonStyle.secondary,
                        label="📋 Показать список",
                        custom_id=f"show_list_{event_id}"
                    ))

                    await message.edit(embed=embed, view=view)

                except Exception as e:
                    await interaction.followup.send(
                        f"⚠️ Ошибка обновления: {str(e)}",
                        ephemeral=True
                    )
            else:
                await interaction.followup.send(
                    "❌ Канал событий не найден",
                    ephemeral=True
                )

        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Ошибка: {str(e)}",
                ephemeral=True
            )
        finally:
            if connection.is_connected():
                connection.close()

    async def list_events(self, interaction: discord.Interaction):
        """Показывает список всех событий с датой и временем"""
        connection = self.get_db_connection()
        if not connection:
            return await interaction.response.send_message(
                "Ошибка подключения к БД",
                ephemeral=True
            )

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM events 
            WHERE event_date > NOW() 
            ORDER BY event_date
        """)
        events = cursor.fetchall()
        connection.close()

        if not events:
            return await interaction.response.send_message(
                "Нет активных событий",
                ephemeral=True
            )

        embed = discord.Embed(
            title="📅 Список событий",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )

        for event in events:
            event_date = event['event_date']
            time_left = event_date - datetime.now()
            days = time_left.days
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)

            # Обработка получателей
            recipients = json.loads(event['recipients'])
            recipient_text = []
            for r in recipients:
                if r == "all":
                    recipient_text.append("Все участники")
                elif r.startswith("role:"):
                    role_id = int(r.split(":")[1])
                    role = interaction.guild.get_role(role_id)
                    recipient_text.append(f"Роль: {role.mention if role else 'Не найдена'}")
                elif r.startswith("user:"):
                    user_id = int(r.split(":")[1])
                    user = interaction.guild.get_member(user_id)
                    recipient_text.append(f"Пользователь: {user.mention if user else 'Не найден'}")

            # Добавление поля с информацией о событии
            embed.add_field(
                name=f"🔹 {event['event_name']}",
                value=(
                    f"**🗓️ Дата:** {event_date.strftime('%d.%m.%Y %H:%M')}\n"
                    f"**⏳ Осталось:** {days}д {hours}ч {minutes}м\n"
                    f"**👥 Для:** {', '.join(recipient_text)}\n"
                    f"**🆔 ID:** {event['event_id']}"
                ),
                inline=False
            )

        embed.set_footer(text=f"Всего событий: {len(events)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(EventCommands(bot))