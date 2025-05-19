import discord
from discord import app_commands
from discord.ext import commands, tasks
import mysql.connector
from datetime import datetime
import json
import re
import asyncio
from data import mysqlconf
from mysql.connector import Error

MYSQL_CONFIG = mysqlconf



def get_db_connection():
    try:
        return mysql.connector.connect(**MYSQL_CONFIG)
    except Error as e:
        print(f"Ошибка MySQL: {e}")
        return None


def init_event_tables():
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id INT AUTO_INCREMENT PRIMARY KEY,
                    event_name VARCHAR(255) NOT NULL UNIQUE,
                    description TEXT,
                    event_date TIMESTAMP NOT NULL,
                    recipients JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_config (
                    guild_id BIGINT PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    category_id BIGINT NOT NULL
                )
            """)
            conn.commit()
        except Error as e:
            print(f"Ошибка MySQL: {e}")
        finally:
            conn.close()


init_event_tables()


async def update_event_config(guild_id: int, channel_id: int, category_id: int):
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE event_config 
                SET channel_id = %s,
                    category_id = %s
                WHERE guild_id = %s
            """, (channel_id, category_id, guild_id))
            conn.commit()
            return True
        except Error as e:
            print(f"Ошибка обновления конфига: {e}")
            return False
        finally:
            conn.close()
    return False


async def setup_event_commands(bot):
    async def get_event_config(guild_id: int):
        conn = get_db_connection()
        if conn:
            try:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT * FROM event_config WHERE guild_id = %s", (guild_id,))
                return cursor.fetchone()
            except Error as e:
                print(f"Error: {e}")
            finally:
                conn.close()
        return None

    @bot.tree.command(name="event", description="Управление событиями")
    @app_commands.describe(
        action="Действие: list, create, remove, install, edit",
        name="Название события",
        date="Дата в формате DD.MM.YYYY",
        description="Описание события",
        recipients="Упоминания пользователей/ролей через пробел"
    )
    async def event_command(
            interaction: discord.Interaction,
            action: str,
            name: str = None,
            date: str = None,
            description: str = None,
            recipients: str = None
    ):
        try:
            if action == "install":
                if not interaction.channel or not interaction.guild:
                    return await interaction.response.send_message("Ошибка контекста!", ephemeral=True)

                conn = get_db_connection()
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO event_config (guild_id, channel_id, category_id)
                            VALUES (%s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                            channel_id = VALUES(channel_id),
                            category_id = VALUES(category_id)
                        """, (interaction.guild.id, interaction.channel.id, interaction.channel.category_id))
                        conn.commit()
                        await interaction.response.send_message(
                            f"✅ Канал {interaction.channel.mention} и категория настроены!",
                            ephemeral=True
                        )
                    except Error as e:
                        print(f"Error: {e}")
                        await interaction.response.send_message("Ошибка БД!", ephemeral=True)
                    finally:
                        conn.close()

            elif action == "create":
                if not all([name, date]):
                    return await interaction.response.send_message(
                        "❌ Необходимо указать название и дату!",
                        ephemeral=True
                    )

                try:
                    event_date = datetime.strptime(date, "%d.%m.%Y")
                    if event_date < datetime.now():
                        return await interaction.response.send_message(
                            "❌ Дата должна быть в будущем!",
                            ephemeral=True
                        )
                except ValueError:
                    return await interaction.response.send_message(
                        "❌ Неверный формат даты! Используйте DD.MM.YYYY",
                        ephemeral=True
                    )

                parsed_recipients = []
                if recipients and recipients.lower() != "none":
                    pattern = r'<@!?(\d+)>|<@&(\d+)>'
                    matches = re.findall(pattern, recipients)
                    for match in matches:
                        if match[0]:  # User
                            parsed_recipients.append(('user', match[0]))
                        elif match[1]:  # Role
                            parsed_recipients.append(('role', match[1]))

                conn = get_db_connection()
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO events (event_name, description, event_date, recipients)
                            VALUES (%s, %s, %s, %s)
                        """, (name, description, event_date, json.dumps(parsed_recipients)))
                        conn.commit()
                        await interaction.response.send_message(
                            f"✅ Событие '{name}' создано на {date}!",
                            ephemeral=True
                        )
                    except mysql.connector.IntegrityError:
                        await interaction.response.send_message(
                            "❌ Событие с таким именем уже существует!",
                            ephemeral=True
                        )
                    except Error as e:
                        print(f"Error: {e}")
                        await interaction.response.send_message("Ошибка БД!", ephemeral=True)
                    finally:
                        conn.close()

            elif action == "list":
                conn = get_db_connection()
                if conn:
                    try:
                        cursor = conn.cursor(dictionary=True)
                        cursor.execute("SELECT * FROM events ORDER BY event_date DESC")
                        events = cursor.fetchall()

                        if not events:
                            return await interaction.response.send_message(
                                "ℹ️ Нет активных событий.",
                                ephemeral=True
                            )

                        embed = discord.Embed(title="Список событий", color=0x00ff00)
                        for event in events:
                            recipients = json.loads(event['recipients'])
                            recipient_text = '\n'.join(
                                [f"<@{r[1]}>" if r[0] == 'user' else f"<@&{r[1]}>" for r in recipients])
                            embed.add_field(
                                name=f"{event['event_name']} (ID: {event['event_id']})",
                                value=f"**Дата:** {event['event_date'].strftime('%d.%m.%Y %H:%M')}\n"
                                      f"**Описание:** {event['description']}\n"
                                      f"**Получатели:**\n{recipient_text}",
                                inline=False
                            )
                        await interaction.response.send_message(embed=embed)
                    except Error as e:
                        print(f"Error: {e}")
                        await interaction.response.send_message("Ошибка БД!", ephemeral=True)
                    finally:
                        conn.close()

            elif action == "remove":
                if not name:
                    return await interaction.response.send_message(
                        "❌ Укажите название события!",
                        ephemeral=True
                    )

                conn = get_db_connection()
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM events WHERE event_name = %s", (name,))
                        conn.commit()
                        if cursor.rowcount == 0:
                            await interaction.response.send_message(
                                "❌ Событие не найдено!",
                                ephemeral=True
                            )
                        else:
                            await interaction.response.send_message(
                                f"✅ Событие '{name}' удалено!",
                                ephemeral=True
                            )
                    except Error as e:
                        print(f"Error: {e}")
                        await interaction.response.send_message("Ошибка БД!", ephemeral=True)
                    finally:
                        conn.close()

            elif action == "edit":
                await interaction.response.send_message(
                    "🛠 Редактирование в разработке!",
                    ephemeral=True
                )

        except Exception as e:
            print(f"Critical error: {e}")
            await interaction.response.send_message(
                "⚠️ Произошла критическая ошибка!",
                ephemeral=True
            )

    @tasks.loop(hours=1)
    async def event_notifier():
        conn = None
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("""
                    SELECT e.*, ec.channel_id, ec.guild_id 
                    FROM events e
                    JOIN event_config ec ON e.event_id IS NOT NULL
                    WHERE e.event_date BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 1 DAY)
                """)
                events = cursor.fetchall()

                for event in events:
                    channel = bot.get_channel(event['channel_id'])
                    if channel:
                        try:
                            recipients = json.loads(event['recipients'])
                            mentions = []
                            for r in recipients:
                                if r[0] == 'user':
                                    mentions.append(f"<@{r[1]}>")
                                elif r[0] == 'role':
                                    mentions.append(f"<@&{r[1]}>")

                            days_left = (event['event_date'] - datetime.now()).days
                            msg = await channel.send(
                                f"## 🚨 Напоминание: {event['event_name']}\n"
                                f"**Дата:** {event['event_date'].strftime('%d.%m.%Y')}\n"
                                f"**Осталось дней:** {days_left}\n"
                                f"**Описание:** {event['description']}\n"
                                f"**Участники:** {' '.join(mentions)}"
                            )
                            await msg.add_reaction('🔔')
                        except discord.errors.HTTPException as e:
                            if "Invalid Webhook Token" in str(e):
                                guild_id = event['guild_id']
                                await channel.delete()
                                new_channel = await channel.clone()
                                success = await update_event_config(
                                    guild_id,
                                    new_channel.id,
                                    new_channel.category_id
                                )
                                if success:
                                    await new_channel.send("✅ Канал и конфиг успешно обновлены!")
                                else:
                                    await new_channel.send("⚠️ Канал обновлен, но конфиг не сохранен!")
        except Exception as e:
            print(f"Notification error: {e}")
        finally:
            if conn:
                conn.close()

    @event_notifier.before_loop
    async def before_notifier():
        await bot.wait_until_ready()

    event_notifier.start()