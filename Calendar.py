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

    @app_commands.command(name="event", description="Управление событиями")
    @app_commands.describe(
        action="Выберите действие",
        name="Название события",
        date="Дата события в формате DD:MM:YYYY",
        time="Время события в формате HH:MM",
        recipients="Укажите пользователей, роли или all для всех"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="install", value="install"),
        app_commands.Choice(name="create", value="create"),
        app_commands.Choice(name="list", value="list"),
        app_commands.Choice(name="remove", value="remove")
    ])
    @app_commands.autocomplete(name=event_autocomplete)
    async def event_command(
            self,
            interaction: discord.Interaction,
            action: Literal["install", "create", "list", "remove"],
            name: Optional[str] = None,
            date: Optional[str] = None,
            recipients: Optional[str] = None
    ):
        if action == "install":
            await self.install_event_system(interaction)
        elif action == "create":
            await self.create_event(interaction, name, date, recipients)
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
                cursor.execute("""
                    INSERT INTO event_config (guild_id, channel_id, category_id)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                    channel_id = VALUES(channel_id),
                    category_id = VALUES(category_id)
                """, (guild.id, main_channel.id, category.id))
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

    async def create_event(self, interaction: discord.Interaction, name: str, date: str, time: str, recipients: str):
        """Создает новое событие с указанием времени"""
        try:
            # Парсим дату и время
            event_datetime = datetime.strptime(f"{date} {time}", "%d:%m:%Y %H:%M")

            if event_datetime < datetime.now():
                return await interaction.response.send_message(
                    "Дата и время события не могут быть в прошлом",
                    ephemeral=True
                )
        except ValueError:
            return await interaction.response.send_message(
                "Неверный формат даты/времени. Используйте:\n"
                "Дата: DD:MM:YYYY (например 25:12:2024)\n"
                "Время: HH:MM (например 15:30)",
                ephemeral=True
            )

        # Получаем ID канала событий из event_config
        connection = self.get_db_connection()
        if not connection:
            return await interaction.response.send_message(
                "Ошибка подключения к БД",
                ephemeral=True
            )

        cursor = connection.cursor(dictionary=True)

        try:
            # Получаем конфигурацию сервера
            cursor.execute(
                "SELECT channel_id FROM event_config WHERE guild_id = %s",
                (interaction.guild.id,)
            )
            config = cursor.fetchone()

            if not config:
                return await interaction.response.send_message(
                    "Сначала выполните /event install",
                    ephemeral=True
                )

            channel_id = config['channel_id']

            # Обрабатываем получателей
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
                    "Не указаны получатели или указаны некорректно",
                    ephemeral=True
                )

            # Сохраняем событие в БД
            cursor.execute("""
                INSERT INTO events (
                    event_name,
                    event_date,
                    recipients,
                    channel_id
                ) VALUES (%s, %s, %s, %s)
            """, (
                name,
                event_datetime,
                json.dumps(recipient_data),
                channel_id
            ))
            connection.commit()
            event_id = cursor.lastrowid

            # Создаем сообщение с таймером
            time_left = event_datetime - datetime.now()
            days = time_left.days
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)

            embed = discord.Embed(
                title=f"Событие: {name}",
                description=f"Дата: {event_datetime.strftime('%d.%m.%Y %H:%M')}\n"
                            f"Осталось: {days} дней, {hours} часов, {minutes} минут",
                color=discord.Color.blue()
            )

            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label="Обновить таймер",
                custom_id=f"update_timer_{event_id}"
            ))
            view.add_item(discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="Показать список",
                custom_id=f"show_list_{event_id}"
            ))

            event_channel = self.bot.get_channel(channel_id)
            if event_channel:
                message = await event_channel.send(embed=embed, view=view)
                self.active_messages[event_id] = message.id
                await interaction.response.send_message(
                    f"Событие '{name}' успешно создано на {event_datetime.strftime('%d.%m.%Y %H:%M')}!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Канал событий не найден",
                    ephemeral=True
                )

        except mysql.connector.Error as e:
            await interaction.response.send_message(
                f"Ошибка при создании события: {str(e)}",
                ephemeral=True
            )
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
        """Удаляет событие"""
        connection = self.get_db_connection()
        if not connection:
            return await interaction.response.send_message(
                "Ошибка подключения к БД",
                ephemeral=True
            )

        cursor = connection.cursor(dictionary=True)

        # Проверяем существование события
        cursor.execute(
            "SELECT event_id FROM events WHERE event_name = %s",
            (name,)
        )
        event = cursor.fetchone()

        if not event:
            connection.close()
            return await interaction.response.send_message(
                f"Событие '{name}' не найдено",
                ephemeral=True
            )

        # Удаляем сообщение с таймером, если оно существует
        if event['event_id'] in self.active_messages:
            try:
                channel_id = None
                cursor.execute(
                    "SELECT channel_id FROM events WHERE event_id = %s",
                    (event['event_id'],)
                )
                event_data = cursor.fetchone()
                if event_data:
                    channel_id = event_data['channel_id']
                    channel = self.bot.get_channel(channel_id)
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

        # Удаляем событие из БД
        cursor.execute(
            "DELETE FROM events WHERE event_name = %s",
            (name,)
        )
        connection.commit()
        affected = cursor.rowcount
        connection.close()

        if affected:
            await interaction.response.send_message(
                f"Событие '{name}' успешно удалено!",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Не удалось удалить событие '{name}'",
                ephemeral=True
            )

    @tasks.loop(minutes=1)
    async def update_timers(self):
        """Обновляет таймеры событий и проверяет наступление событий"""
        connection = self.get_db_connection()
        if not connection:
            return

        cursor = connection.cursor(dictionary=True)

        # Обновляем активные таймеры
        cursor.execute("""
            SELECT * FROM events 
            WHERE event_date > NOW()
        """)
        active_events = cursor.fetchall()

        for event in active_events:
            if event['event_id'] not in self.active_messages:
                continue

            try:
                channel = self.bot.get_channel(event['channel_id'])
                if not channel:
                    continue

                message = await channel.fetch_message(
                    self.active_messages[event['event_id']]
                )

                event_date = event['event_date']
                time_left = event_date - datetime.now()
                days = time_left.days
                hours, remainder = divmod(time_left.seconds, 3600)
                minutes, _ = divmod(remainder, 60)

                embed = discord.Embed(
                    title=f"Событие: {event['event_name']}",
                    description=f"Дата: {event_date.strftime('%d.%m.%Y')}\n"
                                f"Осталось: {days} дней, {hours} часов, {minutes} минут",
                    color=discord.Color.blue()
                )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    label="Обновить таймер",
                    custom_id=f"update_timer_{event['event_id']}"
                ))
                view.add_item(discord.ui.Button(
                    style=discord.ButtonStyle.secondary,
                    label="Показать список",
                    custom_id=f"show_list_{event['event_id']}"
                ))

                await message.edit(embed=embed, view=view)
            except:
                continue

        # Проверяем наступившие события
        cursor.execute("""
            SELECT * FROM events 
            WHERE event_date <= NOW() + INTERVAL 1 MINUTE
            AND event_date > NOW() - INTERVAL 1 MINUTE
        """)
        triggered_events = cursor.fetchall()

        for event in triggered_events:
            try:
                # Получаем канал для логов
                cursor.execute("""
                    SELECT channel_id FROM event_config 
                    WHERE guild_id = (SELECT guild_id FROM channels WHERE channel_id = %s)
                """, (event['channel_id'],))
                config = cursor.fetchone()

                if not config:
                    continue

                log_channel = self.bot.get_channel(config['channel_id'])
                if not log_channel:
                    continue

                # Формируем сообщение о наступлении события
                recipients = json.loads(event['recipients'])
                mentions = []
                for r in recipients:
                    if r == "all":
                        mentions.append("@everyone")
                    elif r.startswith("role:"):
                        role_id = int(r.split(":")[1])
                        mentions.append(f"<@&{role_id}>")
                    elif r.startswith("user:"):
                        user_id = int(r.split(":")[1])
                        mentions.append(f"<@{user_id}>")

                await log_channel.send(
                    f"**Событие наступило!**\n"
                    f"Название: {event['event_name']}\n"
                    f"Для: {' '.join(mentions)}"
                )

                # Удаляем сообщение с таймером
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

                # Удаляем событие из БД
                cursor.execute(
                    "DELETE FROM events WHERE event_id = %s",
                    (event['event_id'],)
                )
                connection.commit()
            except:
                continue

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
                "Ошибка подключения к БД",
                ephemeral=True
            )

        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM events WHERE event_id = %s",
            (event_id,)
        )
        event = cursor.fetchone()
        connection.close()

        if not event:
            return await interaction.followup.send(
                "Событие не найдено",
                ephemeral=True
            )

        event_date = event['event_date']
        time_left = event_date - datetime.now()
        days = time_left.days
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        await interaction.followup.send(
            f"Таймер обновлен!\n"
            f"Событие: {event['event_name']}\n"
            f"Осталось: {days} дней, {hours} часов, {minutes} минут",
            ephemeral=True
        )

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