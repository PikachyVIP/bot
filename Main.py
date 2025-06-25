import io
import subprocess
from typing import Optional, Final
import discord
from discord.ext import commands
from discord.ui import Button, View
import datetime
from datetime import timedelta
from PIL import Image, ImageDraw, ImageFont, ImageOps
import asyncio
import mysql.connector
from mysql.connector import Error
import aiohttp
from discord import app_commands, Embed, Color
import os
from os import environ
import yt_dlp
from data import token, assettoken, mysqlconf


import Calendar
import install_multivoice
from BoostL import BoostL
from Calendar import setup
from Shop import Shop
from install_multivoice import setup
from discord.utils import setup_logging

setup_logging()  # Включаем логирование

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
    'options': '-vn -loglevel error -timeout 30000000'  # 30 секунд таймаут
}



TOKEN = token
ASSETTOKEN = assettoken
PREFIX = '!'
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)
bot.remove_command('help')

# ID канала для создания веток (замените на свой)
LS_CHANNEL_ID = 1121240277483536485

MYSQL_CONFIG = mysqlconf

# Словари для хранения данных
thread_users = {}  # {thread_id: user_id}
thread_settings = {}  # {thread_id: {"show_admin": bool}}


# Функции для работы с БД
def get_db_connection():
    try:
        return mysql.connector.connect(**MYSQL_CONFIG)
    except Error as e:
        print(f"Ошибка MySQL: {e}")
        return None


def init_db():
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS command_access (
                    user_id BIGINT NOT NULL,
                    command_name VARCHAR(50) NOT NULL,
                    PRIMARY KEY (user_id, command_name)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_levels (
                    user_id BIGINT PRIMARY KEY,
                    xp INT DEFAULT 0,
                    boost INT DEFAULT 0,
                    level INT DEFAULT 1,
                    messages_count INT DEFAULT 0,
                    last_message TIMESTAMP,
                    last_xp_update TIMESTAMP,
                    info TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS voice_system (
                    guild_id BIGINT PRIMARY KEY,
                    category_id BIGINT NOT NULL,
                    trigger_channel_id BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS save_music_url (
                    name VARCHAR(255) PRIMARY KEY,
                    url TEXT NOT NULL
                )
            """)
            # cursor.execute("""
            #      CREATE TABLE IF NOT EXISTS bot_settings (
            #         guild_id BIGINT PRIMARY KEY,
            #         vkplayer_webhook TEXT,
            #         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            #         updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            #     )
            # """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id INT AUTO_INCREMENT PRIMARY KEY,
                    event_name VARCHAR(255) NOT NULL UNIQUE,
                    description TEXT,
                    event_date DATETIME NOT NULL,
                    recipients JSON,
                    channel_id BIGINT,
                    category_id BIGINT,
                    loop_interval VARCHAR(20),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_config (
                    guild_id BIGINT PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    log_channel_id BIGINT NOT NULL,
                    category_id BIGINT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_notifications (
                    notification_id INT AUTO_INCREMENT PRIMARY KEY,
                    event_id INT NOT NULL,
                    notification_date DATETIME NOT NULL,
                    notified BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE
                );
            """)

            cursor.execute("""
                ALTER TABLE events ADD COLUMN loop_interval VARCHAR(20) DEFAULT NULL;
            """)

            conn.commit()
        except Error as e:
            print(f"Ошибка MySQL: {e}")
        finally:
            conn.close()


init_db()


@bot.tree.command(name="bd", description="Управление базой данных")
@app_commands.describe(
    action="Действие",
    table="Название таблицы"
)
@app_commands.choices(
    action=[app_commands.Choice(name="clear", value="clear")]
)
async def bd_command(
        interaction: discord.Interaction,
        action: str,
        table: str
):
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    if action == "clear":
        conn = get_db_connection()
        if not conn:
            return await interaction.response.send_message(
                "❌ Ошибка подключения к базе данных",
                ephemeral=True
            )

        try:
            cursor = conn.cursor()

            # Проверяем существование таблицы
            cursor.execute("SHOW TABLES LIKE %s", (table,))
            if not cursor.fetchone():
                return await interaction.response.send_message(
                    f"❌ Таблица {table} не существует",
                    ephemeral=True
                )

            # Очищаем таблицу
            cursor.execute(f"TRUNCATE TABLE {table}")
            conn.commit()

            await interaction.response.send_message(
                f"✅ Таблица {table} успешно очищена",
                ephemeral=True
            )

        except Error as e:
            await interaction.response.send_message(
                f"❌ Ошибка MySQL при очистке таблицы: {e}",
                ephemeral=True
            )
        finally:
            conn.close()



async def check_command_access(ctx):
    # Владелец бота имеет полный доступ
    if await ctx.bot.is_owner(ctx.author):
        return True

    # Проверка прав в БД
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM command_access 
            WHERE user_id = %s AND command_name = %s
        """, (ctx.author.id, ctx.command.name.lower()))
        return cursor.fetchone() is not None
    except Error:
        return False
    finally:
        conn.close()

async def check_command_access_app(interaction: discord.Interaction) -> bool:
    """Адаптированная функция проверки прав для слэш-команд"""
    # Владелец бота имеет полный доступ
    if await interaction.client.is_owner(interaction.user):
        return True

    # Проверка прав в БД
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM command_access 
            WHERE user_id = %s AND command_name = %s
        """, (interaction.user.id, interaction.command.name.lower()))
        return cursor.fetchone() is not None
    except Error:
        return False
    finally:
        conn.close()



# Добавляем проверку прав ко всем командам (кроме help и law)
for command in bot.commands:
    if command.name not in ['help', 'law']:
        command.add_check(check_command_access)


@bot.tree.command(
    name="say",
    description="Отправляет сообщение от имени бота"
)
@app_commands.describe(
    text="Текст сообщения",
    channel="Целевой канал (необязательно)"
)
async def say(interaction: discord.Interaction, text: str, channel: discord.TextChannel = None):
    """Отправляет оформленное сообщение от имени бота"""
    # Проверка прав через вашу систему
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    try:
        target_channel = channel or interaction.channel

        embed = discord.Embed(
            description=text,
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )

        await target_channel.send(embed=embed)

        await interaction.response.send_message(
            "✅ Сообщение успешно отправлено!",
            ephemeral=True,
            delete_after=5
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для выполнения!",
            ephemeral=True
        )
    except Exception as e:
        print(f"[ERROR] /say command: {e}")
        await interaction.response.send_message(
            "❌ Произошла ошибка при выполнении команды",
            ephemeral=True
        )


class ThreadControlView(View):
    def __init__(self, thread_id=None):
        super().__init__(timeout=None)
        self.thread_id = thread_id

        # Кнопка закрытия ветки
        self.close_btn = Button(
            label="Удалить ветку",
            style=discord.ButtonStyle.red,
            emoji="🗑️",
            custom_id=f"close_thread_{thread_id if thread_id else 'global'}"
        )
        self.close_btn.callback = self.close_thread
        self.add_item(self.close_btn)

        # Кнопка переключения показа админа
        self.toggle_btn = Button(
            label="Показ админа: ❌",
            style=discord.ButtonStyle.grey,
            emoji="👤",
            custom_id=f"toggle_admin_{thread_id if thread_id else 'global'}"
        )
        self.toggle_btn.callback = self.toggle_admin
        self.add_item(self.toggle_btn)

    async def close_thread(self, interaction: discord.Interaction):
        thread = interaction.channel

        # Отправляем сообщение о предстоящем удалении
        await interaction.response.send_message("⏳ Ветка будет удалена через 5 секунд...")

        # Удаляем из словарей
        if thread.id in thread_users:
            del thread_users[thread.id]
        if thread.id in thread_settings:
            del thread_settings[thread.id]

        await asyncio.sleep(5)

        try:
            # Получаем свежий объект ветки
            thread = await interaction.guild.fetch_channel(thread.id)
            await thread.delete(reason="Закрытие ветки администратором")
        except discord.NotFound:
            await interaction.followup.send("❌ Ветка уже была удалена", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Недостаточно прав для удаления ветки", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Ошибка при удалении ветки: {e}", ephemeral=True)

    async def toggle_admin(self, interaction: discord.Interaction):
        thread_id = interaction.channel.id
        if thread_id not in thread_settings:
            thread_settings[thread_id] = {"show_admin": False}

        thread_settings[thread_id]["show_admin"] = not thread_settings[thread_id]["show_admin"]
        new_state = "✅" if thread_settings[thread_id]["show_admin"] else "❌"

        # Обновляем кнопку
        for item in self.children:
            if item.custom_id.startswith("toggle_admin"):
                item.label = f"Показ админа: {new_state}"

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"🔄 Режим показа администратора: **{new_state}**",
            ephemeral=True
        )


@bot.tree.command(
    name="getlaw",
    description="Показывает список доступных команд для пользователя"
)
@app_commands.describe(
    member="Пользователь для проверки прав"
)
async def getlaw(interaction: discord.Interaction, member: discord.Member):
    """Показывает какие команды доступны указанному пользователю"""
    # Отправляем deferred response, если нужно время на обработку
    await interaction.response.defer(ephemeral=True)

    # Проверка прав вызывающего
    if not await check_command_access_app(interaction):
        return await interaction.followup.send(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    conn = get_db_connection()
    if not conn:
        return await interaction.followup.send(
            "❌ Ошибка подключения к базе данных",
            ephemeral=True
        )

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT command_name FROM command_access
            WHERE user_id = %s
            ORDER BY command_name
        """, (member.id,))
        db_commands = [row['command_name'] for row in cursor.fetchall()]

        all_commands = list(bot.all_commands.keys())
        all_commands += [cmd.name for cmd in bot.tree.get_commands()]

        embed = discord.Embed(
            title=f"Права доступа для {member.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )

        if db_commands:
            embed.add_field(
                name="Доступные команды",
                value="\n".join(db_commands),
                inline=False
            )
        else:
            embed.add_field(
                name="Доступные команды",
                value="Нет специальных прав",
                inline=False
            )

        embed.add_field(
            name="Все команды бота",
            value=", ".join(sorted(all_commands)),
            inline=False
        )

        embed.set_footer(
            text=f"Запрошено {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url
        )

        # Отправляем embed напрямую через followup
        await interaction.followup.send(embed=embed)

    except Error as e:
        await interaction.followup.send(
            f"❌ Ошибка базы данных: {e}",
            ephemeral=True
        )
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()



@bot.event
async def on_ready():

    print(f'Бот {bot.user} запущен и готов к работе!')
    # Регистрируем персистентное View
    await install_multivoice.setup(bot)
    await Calendar.setup(bot)
    await Shop.setup(bot)
    await BoostL.setup(bot)
    bot.add_view(ThreadControlView())
    try:
        # Синхронизируем команды с Discord
        synced = await bot.tree.sync()
        print(f"Синхронизировано {len(synced)} команд")
    except Exception as e:
        print(f"Ошибка синхронизации команд: {e}")


@bot.tree.command(
    name="law",
    description="Управление доступом к командам"
)
@app_commands.describe(
    member="Пользователь",
    action="Действие (add/rem)",
    commands="Команды через запятую или 'all'"
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Добавить права", value="add"),
        app_commands.Choice(name="Удалить права", value="rem")
    ]
)
async def law(
        interaction: discord.Interaction,
        member: discord.Member,
        action: app_commands.Choice[str],
        commands: str
):
    """Управление доступом к командам"""
    # Отправляем deferred response
    await interaction.response.defer(ephemeral=True)

    # Проверка прав вызывающего
    if not await check_command_access_app(interaction):
        return await interaction.followup.send(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    # Получаем список всех команд
    all_commands = list(bot.all_commands.keys())
    all_commands += [cmd.name for cmd in bot.tree.get_commands()]
    all_commands = [cmd.lower() for cmd in all_commands]

    # Обработка команды 'all'
    if commands.strip().lower() == 'all':
        command_list = all_commands
    else:
        command_list = [cmd.strip().lower() for cmd in commands.split(',')]

    # Проверка существования команд
    invalid_commands = [cmd for cmd in command_list if cmd not in all_commands]
    if invalid_commands:
        return await interaction.followup.send(
            f"❌ Неизвестные команды: {', '.join(invalid_commands)}",
            ephemeral=True
        )

    conn = get_db_connection()
    if not conn:
        return await interaction.followup.send(
            "❌ Ошибка подключения к базе данных",
            ephemeral=True
        )

    try:
        cursor = conn.cursor()
        changes = []
        count = 0

        for cmd in command_list:
            if action.value == 'add':
                cursor.execute("""
                    INSERT INTO command_access (user_id, command_name)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE command_name = VALUES(command_name)
                """, (member.id, cmd))
                changes.append(f"➕ {cmd}")
            else:
                cursor.execute("""
                    DELETE FROM command_access
                    WHERE user_id = %s AND command_name = %s
                """, (member.id, cmd))
                changes.append(f"➖ {cmd}")
            count += 1

        conn.commit()

        # Формируем сообщение
        message = f"Выполнено {count} изменений" if count > 5 else "\n".join(changes)

        embed = discord.Embed(
            title=f"Права обновлены для {member.display_name}",
            description=message,
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )

        cursor.execute("SELECT COUNT(*) FROM command_access WHERE user_id = %s", (member.id,))
        total_commands = cursor.fetchone()[0]

        embed.set_footer(text=f"Всего доступно команд: {total_commands}/{len(all_commands)}")

        # Отправляем embed напрямую
        await interaction.followup.send(embed=embed)

    except Error as e:
        await interaction.followup.send(
            f"❌ Ошибка базы данных: {e}",
            ephemeral=True
        )
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@bot.tree.command(
    name="help",
    description="Показывает доступные вам команды"
)
async def help(interaction: discord.Interaction):
    """Показывает список доступных команд"""
    # Получаем список всех команд (и обычных, и слэш-команд)
    all_commands = {}

    # Обычные команды (через префикс)
    for cmd in bot.commands:
        all_commands[cmd.name.lower()] = {
            "type": "prefix",
            "obj": cmd,
            "help": cmd.help or "Описание отсутствует"
        }

    # Слэш-команды
    for cmd in bot.tree.get_commands():
        all_commands[cmd.name.lower()] = {
            "type": "slash",
            "obj": cmd,
            "help": cmd.description or "Описание отсутствует"
        }

    # Для владельца бота показываем все команды
    if await bot.is_owner(interaction.user):
        available_commands = list(all_commands.values())
    else:
        conn = get_db_connection()
        if not conn:
            return await interaction.response.send_message(
                "❌ Ошибка подключения к базе данных. Попробуйте позже.",
                ephemeral=True
            )

        try:
            cursor = conn.cursor()

            # Получаем команды, доступные пользователю из БД
            cursor.execute("""
                SELECT command_name FROM command_access
                WHERE user_id = %s
            """, (interaction.user.id,))

            db_commands = [row[0].lower() for row in cursor.fetchall()]

            # Фильтруем команды, которые пользователь может выполнять
            available_commands = []
            for cmd_name, cmd_data in all_commands.items():
                # Базовые команды всегда доступны
                if cmd_name in ['help', 'profile', 'setinfo', 'ticket']:
                    available_commands.append(cmd_data)
                    continue

                # Проверяем права в БД для остальных команд
                if cmd_name in db_commands:
                    if cmd_data["type"] == "prefix":
                        try:
                            if await cmd_data["obj"].can_run(interaction):
                                available_commands.append(cmd_data)
                        except commands.MissingPermissions:
                            continue
                    else:
                        available_commands.append(cmd_data)

        except Error as e:
            print(f"Ошибка БД: {e}")
            return await interaction.response.send_message(
                "❌ Ошибка при проверке прав доступа",
                ephemeral=True
            )
        finally:
            conn.close()

    # Формируем сообщение
    if not available_commands:
        return await interaction.response.send_message(
            "❌ У вас нет доступных команд",
            ephemeral=True
        )

    # Создаем Embed
    embed = discord.Embed(
        title="Доступные команды",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )

    # Разделяем команды по типам
    prefix_cmds = []
    slash_cmds = []

    for cmd in sorted(available_commands, key=lambda x: x["obj"].name):
        if cmd["type"] == "prefix":
            prefix_cmds.append(f"`!{cmd['obj'].name}` - {cmd['help'].splitlines()[0]}")
        else:
            slash_cmds.append(f"`/{cmd['obj'].name}` - {cmd['help'].splitlines()[0]}")

    # Добавляем поля в Embed
    if prefix_cmds:
        embed.add_field(
            name="Обычные команды (через !)",
            value="\n".join(prefix_cmds),
            inline=False
        )

    if slash_cmds:
        embed.add_field(
            name="Слэш-команды (через /)",
            value="\n".join(slash_cmds),
            inline=False
        )

    embed.set_footer(
        text=f"Запрошено {interaction.user.display_name}",
        icon_url=interaction.user.display_avatar.url
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="telllc",
    description="Отправляет сообщение нескольким пользователям и/или ролям"
)
@app_commands.describe(
    targets="Пользователи и/или роли (через пробел)",
    message="Текст сообщения",
    show_sender="Показывать отправителя (по умолчанию False)"
)
async def telllc(
    interaction: discord.Interaction,
    targets: str,
    message: str,
    show_sender: bool = False
):
    """
    Отправляет сообщение пользователям и/или ролям
    Использование:
    /telllc @user1 @role1 @user2 сообщение [show_sender:True/False]
    """
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )
    # Парсим цели из строки (Discord API не поддерживает Greedy в слэш-командах)
    try:
        target_objects = []
        for mention in targets.split():
            if mention.startswith('<@') and mention.endswith('>'):
                # Это упоминание пользователя или роли
                snowflake = mention[2:-1]
                if snowflake.startswith('&'):
                    # Роль
                    role_id = int(snowflake[1:])
                    role = interaction.guild.get_role(role_id)
                    if role:
                        target_objects.append(role)
                else:
                    # Пользователь
                    user_id = int(snowflake.replace('!', ''))  # Убираем ! если есть
                    member = interaction.guild.get_member(user_id)
                    if member:
                        target_objects.append(member)
    except Exception as e:
        return await interaction.response.send_message(
            f"❌ Ошибка парсинга целей: {e}",
            ephemeral=True
        )

    if not target_objects:
        return await interaction.response.send_message(
            "❌ Не указаны получатели (пользователи или роли)",
            ephemeral=True
        )

    success = []
    failed = []
    recipients = set()

    # Собираем всех уникальных получателей
    for target in target_objects:
        if isinstance(target, discord.Member):
            recipients.add(target)
        elif isinstance(target, discord.Role):
            for member in target.members:
                if not member.bot:
                    recipients.add(member)

    if not recipients:
        return await interaction.response.send_message(
            "❌ Не найдено ни одного получателя",
            ephemeral=True
        )

    total = len(recipients)
    processed = 0
    message_content = f"Сообщение от {interaction.user.display_name}:\n{message}" if show_sender else f"Сообщение от администрации:\n{message}"

    # Отправляем начальное сообщение о прогрессе
    await interaction.response.send_message(
        f"🔄 Начинаю рассылку для {total} получателей...",
        ephemeral=True
    )
    progress_msg = await interaction.original_response()

    # Рассылка
    for recipient in recipients:
        try:
            await recipient.send(message_content)
            success.append(recipient.display_name)
            processed += 1

            # Обновляем прогресс каждые 5 отправок
            if processed % 5 == 0:
                await progress_msg.edit(
                    content=f"🔄 Рассылка: {processed}/{total} ({processed/total:.0%})"
                )
        except discord.Forbidden:
            failed.append(recipient.display_name)
            processed += 1
        except Exception as e:
            print(f"Ошибка при отправке {recipient}: {e}")
            failed.append(recipient.display_name)
            processed += 1

    # Формируем отчет
    embed = discord.Embed(
        title="📨 Результат рассылки",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )

    stats = []
    if success:
        stats.append(f"✅ Успешно: {len(success)}")
    if failed:
        stats.append(f"❌ Не удалось: {len(failed)}")

    embed.add_field(
        name="Статистика",
        value=" | ".join(stats),
        inline=False
    )

    # Форматируем списки получателей
    def format_list(items, limit=5):
        if not items:
            return "Нет"
        if len(items) > limit:
            return ", ".join(items[:limit]) + f" и ещё {len(items)-limit}"
        return ", ".join(items)

    if success:
        embed.add_field(
            name="Получили сообщение",
            value=format_list(success),
            inline=False
        )

    if failed:
        embed.add_field(
            name="Не получили сообщение",
            value=format_list(failed),
            inline=False
        )

    embed.set_footer(
        text=f"Всего получателей: {total}",
        icon_url=interaction.user.display_avatar.url
    )

    # Отправляем финальный отчет
    await progress_msg.delete()
    await interaction.followup.send(embed=embed)


# Модерационные команды
@bot.tree.command(name="kick", description="Кикает пользователя с сервера")
@app_commands.describe(
    member="Пользователь для кика",
    reason="Причина кика"
)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Не указана"):
    """Кикает пользователя с сервера"""
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    try:
        await member.kick(reason=f"{reason} | Модератор: {interaction.user}")
        embed = discord.Embed(
            title="✅ Пользователь кикнут",
            description=f"Пользователь: {member.mention}\nПричина: {reason}",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Модератор: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для кика",
            ephemeral=True
        )


@bot.tree.command(name="ban", description="Банит пользователя на сервере")
@app_commands.describe(
    member="Пользователь для бана",
    reason="Причина бана"
)
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "Не указана"):
    """Банит пользователя на сервере"""
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    try:
        await member.ban(reason=f"{reason} | Модератор: {interaction.user}")
        embed = discord.Embed(
            title="✅ Пользователь забанен",
            description=f"Пользователь: {member.mention}\nПричина: {reason}",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Модератор: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для бана",
            ephemeral=True
        )


@bot.tree.command(name="timeout", description="Выдает тайм-аут пользователю")
@app_commands.describe(
    member="Пользователь для тайм-аута",
    duration="Длительность в минутах",
    reason="Причина тайм-аута"
)
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, member: discord.Member, duration: app_commands.Range[int, 1, 40320],
                  reason: str = "Не указана"):
    """Выдает тайм-аут пользователю (от 1 минуты до 28 дней)"""
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    try:
        duration_minutes = duration
        duration_timedelta = datetime.timedelta(minutes=duration_minutes)
        await member.timeout(duration_timedelta, reason=f"{reason} | Модератор: {interaction.user}")

        embed = discord.Embed(
            title="✅ Тайм-аут выдан",
            description=f"Пользователь: {member.mention}\nДлительность: {duration_minutes} сек.\nПричина: {reason}",
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"Модератор: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для тайм-аута",
            ephemeral=True
        )


@bot.tree.command(name="untimeout", description="Снимает тайм-аут с пользователя")
@app_commands.describe(
    member="Пользователь для снятия тайм-аута"
)
@app_commands.checks.has_permissions(moderate_members=True)
async def untimeout(interaction: discord.Interaction, member: discord.Member):
    """Снимает тайм-аут с пользователя"""
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    try:
        await member.timeout(None)
        embed = discord.Embed(
            title="✅ Тайм-аут снят",
            description=f"Пользователь: {member.mention}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Модератор: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для снятия тайм-аута",
            ephemeral=True
        )


@bot.tree.command(name="gban", description="Выдает роль 'Забаненный'")
@app_commands.describe(
    member="Пользователь для выдачи роли",
    reason="Причина выдачи"
)
@app_commands.checks.has_permissions(manage_roles=True)
async def gban(interaction: discord.Interaction, member: discord.Member, reason: str = "Не указана"):
    """Выдает роль 'Забаненный'"""
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    role = discord.utils.get(interaction.guild.roles, name="Забаненный")
    if not role:
        return await interaction.response.send_message(
            "❌ Роль 'Забаненный' не найдена",
            ephemeral=True
        )

    try:
        await member.add_roles(role, reason=f"{reason} | Модератор: {interaction.user}")
        embed = discord.Embed(
            title="✅ Роль выдана",
            description=f"Пользователь: {member.mention}\nРоль: Забаненный\nПричина: {reason}",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Модератор: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для выдачи роли",
            ephemeral=True
        )


@bot.tree.command(name="ungban", description="Снимает роль 'Забаненный'")
@app_commands.describe(
    member="Пользователь для снятия роли",
    reason="Причина снятия"
)
@app_commands.checks.has_permissions(manage_roles=True)
async def ungban(interaction: discord.Interaction, member: discord.Member, reason: str = "Не указана"):
    """Снимает роль 'Забаненный'"""
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    role = discord.utils.get(interaction.guild.roles, name="Забаненный")
    if not role:
        return await interaction.response.send_message(
            "❌ Роль 'Забаненный' не найдена",
            ephemeral=True
        )

    try:
        await member.remove_roles(role, reason=f"{reason} | Модератор: {interaction.user}")
        embed = discord.Embed(
            title="✅ Роль снята",
            description=f"Пользователь: {member.mention}\nРоль: Забаненный\nПричина: {reason}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Модератор: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для снятия роли",
            ephemeral=True
        )


@bot.tree.command(
    name="saylc",
    description="Создаёт ветку для общения с пользователем через ЛС бота"
)
@app_commands.describe(
    member="Пользователь для создания диалога"
)
@app_commands.checks.has_permissions(manage_threads=True)
async def saylc(interaction: discord.Interaction, member: discord.Member):
    """Создать ветку для общения с пользователем через ЛС"""
    # Проверка прав через кастомную систему
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    # Получаем канал для ЛС
    ls_channel = bot.get_channel(LS_CHANNEL_ID)
    if not ls_channel:
        return await interaction.response.send_message(
            "❌ Канал для ЛС не найден",
            ephemeral=True
        )

    # Проверяем существующую активную ветку
    existing_thread = next(
        (t for t in ls_channel.threads
         if t.name.endswith(f"{member.id}") and not t.archived),
        None
    )

    if existing_thread:
        return await interaction.response.send_message(
            f"❌ Уже есть активная ветка для {member.mention}: {existing_thread.mention}",
            ephemeral=True
        )

    # Создаем новую ветку
    thread_name = f"ЛС-{member.name}-{member.id}"
    try:
        thread = await ls_channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.public_thread,
            reason=f"Диалог с {member} (инициатор: {interaction.user})"
        )

        # Сохраняем связь ветка -> пользователь
        thread_users[thread.id] = member.id
        thread_settings[thread.id] = {"show_admin": False}

        # Создаем View с кнопками
        view = ThreadControlView(thread.id)

        # Отправляем приветственное сообщение
        embed = discord.Embed(
            title=f"Диалог с {member}",
            description=(
                f"ID: {member.id}\nСоздано: <t:{int(datetime.datetime.now().timestamp())}:R>\n\n"
                "Отправьте любое сообщение в эту ветку, чтобы оно было переслано пользователю в ЛС."
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Инициатор: {interaction.user.display_name}")
        await thread.send(embed=embed, view=view)

        # Пытаемся добавить пользователя в ветку
        try:
            await thread.add_user(member)
        except discord.Forbidden:
            print(f"Не удалось добавить пользователя {member} в ветку")

        # Отправляем подтверждение
        embed = discord.Embed(
            title="✅ Ветка создана",
            description=f"Создана ветка для общения с {member.mention}: {thread.mention}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для создания ветки",
            ephemeral=True
        )
    except Exception as e:
        print(f"Ошибка при создании ветки: {e}")
        await interaction.response.send_message(
            "❌ Произошла ошибка при создании ветки",
            ephemeral=True
        )


@bot.event
async def on_message(message):
    # Игнорируем сообщения от бота
    if message.author.bot or message.content.startswith(PREFIX):
        await bot.process_commands(message)
        return

    # Обновляем статистику только на серверах
    if message.guild:
        new_level = await update_user_stats(message.author.id, message.guild)
        if new_level:
            role_name = LEVELS_CONFIG[new_level]["role"]
            await message.channel.send(
                f"🎉 {message.author.mention} достиг {new_level} уровня и получил роль **{role_name}**!",
                delete_after=15
            )

    # Обработка сообщений в ветках
    if isinstance(message.channel, discord.Thread) and message.channel.parent_id == LS_CHANNEL_ID:
        # Игнорируем сообщения с кнопками
        if message.components:
            return

        # Проверяем, связана ли ветка с пользователем
        if message.channel.id in thread_users:
            user_id = thread_users[message.channel.id]
            try:
                user = await bot.fetch_user(user_id)
                show_admin = thread_settings.get(message.channel.id, {}).get("show_admin", False)

                # Формируем сообщение
                if show_admin:
                    msg_content = f"Сообщение от {message.author.display_name}:\n{message.content}"
                else:
                    msg_content = f"Сообщение от администрации:\n{message.content}"

                # Отправляем сообщение пользователю
                await user.send(msg_content)

                # Подтверждаем отправку в ветке
                embed = discord.Embed(
                    description="✅ Сообщение отправлено пользователю",
                    color=discord.Color.green()
                )
                await message.channel.send(embed=embed)

                # Пересылаем вложения
                if message.attachments:
                    for attachment in message.attachments:
                        await user.send(attachment.url)
                        await message.channel.send(f"📎 Вложение отправлено: {attachment.filename}")

            except discord.Forbidden:
                await message.channel.send("❌ Не удалось отправить сообщение (пользователь закрыл ЛС)")
            except discord.NotFound:
                await message.channel.send("❌ Пользователь не найден")
                if message.channel.id in thread_users:
                    del thread_users[message.channel.id]

    # Обработка ЛС от пользователей
    elif isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        ls_channel = bot.get_channel(LS_CHANNEL_ID)
        if not ls_channel:
            return

        # Ищем существующую ветку для этого пользователя
        existing_thread = None
        for thread in ls_channel.threads:
            if thread.name.endswith(f"{message.author.id}") and not thread.archived:
                existing_thread = thread
                break

        if not existing_thread:
            # Создаем новую ветку, если нет активной
            thread_name = f"ЛС-{message.author.name}-{message.author.id}"
            thread = await ls_channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.public_thread,
                reason=f"Новый диалог с {message.author}"
            )
            thread_users[thread.id] = message.author.id
            thread_settings[thread.id] = {"show_admin": False}

            # Создаем View с кнопками
            view = ThreadControlView(thread.id)

            # Отправляем приветственное сообщение с кнопками
            embed = discord.Embed(
                title=f"Новое сообщение от {message.author}",
                description=f"ID: {message.author.id}",
                color=discord.Color.blue()
            )
            await thread.send(embed=embed, view=view)
        else:
            thread = existing_thread

        # Отправляем сообщение в ветку
        embed = discord.Embed(
            description=message.content,
            color=discord.Color.blue(),
            timestamp=message.created_at
        )
        embed.set_author(name=f"{message.author} (ЛС)", icon_url=message.author.avatar.url if message.author.avatar else None)

        await thread.send(embed=embed)

        if message.attachments:
            for attachment in message.attachments:
                await thread.send(f"📎 Вложение:\n{attachment.url}")

    await bot.process_commands(message)


# Очищаем словарь при архивации ветки
@bot.event
async def on_thread_update(before, after):
    if before.id in thread_users and after.archived:
        del thread_users[before.id]


@bot.event
async def on_member_join(member):
    channel = discord.utils.get(member.guild.text_channels, name='┏⭐│основной')
    if channel:
        await channel.send(f'Добро пожаловать на сервер, {member.mention}!')


# Добавьте в конфигурацию уровней соответствующие роли
LEVELS_CONFIG = {
    1: {"xp": 0, "role": "🔹 Капелька Опыта"},
    2: {"xp": 100, "role": "🌱 Росток Знаний"},
    3: {"xp": 250, "role": "🌀 Вихрь Начинаний"},
    4: {"xp": 450, "role": "🌠 Метеор Усердия"},
    5: {"xp": 700, "role": "🛡️ Страж Мудрости"},
    6: {"xp": 1000, "role": "⚔️ Рыцарь Дискуссий"},
    7: {"xp": 1350, "role": "🌌 Хранитель Традиций"},
    8: {"xp": 1750, "role": "🏰 Архитектор Сообщества"},
    9: {"xp": 2200, "role": "🔮 Маг Контента"},
    10: {"xp": 2700, "role": "🐉 Легенда Чата"},
    11: {"xp": 3250, "role": "🌋 Повелитель Активности"},
    12: {"xp": 3850, "role": "⚡ Император Диалогов"},
    13: {"xp": 4500, "role": "🌟 Создатель Реальности"}
}


def calculate_level(xp):
    """Вычисляет текущий уровень на основе XP"""
    for level, data in sorted(LEVELS_CONFIG.items(), reverse=True):
        if xp >= data["xp"]:
            return level
    return 1


async def update_roles(member, new_level):
    """Обновляет роли пользователя при повышении уровня"""
    try:
        # Получаем все роли уровней
        level_roles = [data["role"] for data in LEVELS_CONFIG.values()]

        # Удаляем все старые роли уровней
        for role in member.roles:
            if role.name in level_roles:
                await member.remove_roles(role)

        # Добавляем новую роль уровня
        new_role_name = LEVELS_CONFIG[new_level]["role"]
        new_role = discord.utils.get(member.guild.roles, name=new_role_name)

        if not new_role:
            # Если роли нет - создаем ее
            new_role = await member.guild.create_role(
                name=new_role_name,
                color=discord.Color.random(),
                hoist=True
            )
            # Перемещаем новую роль выше всех ботов
            bot_role = discord.utils.get(member.guild.roles, name="Bots")
            if bot_role:
                await new_role.edit(position=bot_role.position - 1)

        await member.add_roles(new_role)
        return True
    except Exception as e:
        print(f"Ошибка при обновлении ролей: {e}")
        return False


async def update_user_stats(user_id, guild):
    """Обновляет статистику пользователя и роли при необходимости"""
    try:
        member = guild.get_member(user_id)
        if not member:
            return None

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                current_time = datetime.datetime.now()

                # Всегда увеличиваем счетчик сообщений
                cursor.execute("""
                    INSERT INTO user_levels (user_id, messages_count, last_message)
                    VALUES (%s, 1, %s)
                    ON DUPLICATE KEY UPDATE 
                        messages_count = messages_count + 1,
                        last_message = VALUES(last_message)
                """, (user_id, current_time))

                # Проверяем временной интервал для XP
                cursor.execute("""
                    SELECT xp, level, last_xp_update FROM user_levels
                    WHERE user_id = %s
                """, (user_id,))
                xp, old_level, last_update = cursor.fetchone() or (0, 1, None)

                xp_updated = False
                if not last_update or (current_time - last_update).seconds > 60:
                    new_xp = xp + 1
                    cursor.execute("""
                        UPDATE user_levels SET
                            xp = %s,
                            last_xp_update = %s
                        WHERE user_id = %s
                    """, (new_xp, current_time, user_id))
                    xp_updated = True
                else:
                    new_xp = xp

                # Проверяем повышение уровня
                new_level = calculate_level(new_xp)
                if new_level > old_level:
                    cursor.execute("""
                        UPDATE user_levels SET level = %s
                        WHERE user_id = %s
                    """, (new_level, user_id))
                    # Обновляем роли
                    await update_roles(member, new_level)
                    conn.commit()
                    return new_level

                conn.commit()
                return None

    except Exception as e:
        print(f"Ошибка обновления статистики: {e}")
        return None


async def get_avatar_bytes(user):
    async with aiohttp.ClientSession() as session:
        async with session.get(str(user.avatar.with_size(256).url)) as resp:
            return await resp.read()


@bot.tree.command(
    name="profile",
    description="Показывает профиль пользователя с прогрессом уровня"
)
@app_commands.describe(
    member="Пользователь (оставьте пустым для своего профиля)"
)
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    """Показывает профиль пользователя с прогрессом уровня"""
    target = member or interaction.user

    try:
        # Получаем данные из базы
        conn = get_db_connection()
        if not conn:
            return await interaction.response.send_message(
                "❌ Ошибка подключения к базе данных",
                ephemeral=True
            )

        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("""
                SELECT xp, level, info FROM user_levels
                WHERE user_id = %s
            """, (target.id,))
            data = cursor.fetchone()

            if not data:
                cursor.execute("""
                    INSERT INTO user_levels (user_id, xp, level, info)
                    VALUES (%s, 0, 1, 'Пусто')
                """, (target.id,))
                conn.commit()
                xp, level, info = 0, 1, "Пусто"
            else:
                xp, level, info = data['xp'], data['level'], data['info']

        # Рассчитываем прогресс уровня
        current_level = min(level, 13)
        current_level_xp = LEVELS_CONFIG[current_level]["xp"]
        next_level_xp = LEVELS_CONFIG.get(current_level + 1, LEVELS_CONFIG[13])["xp"]
        xp_progress = max(0, xp - current_level_xp)
        xp_needed = max(1, next_level_xp - current_level_xp)
        progress = min(xp_progress / xp_needed, 1.0)

        # Получаем и очищаем название роли (удаляем emoji)
        current_role_name = LEVELS_CONFIG[current_level]["role"]
        import re
        clean_role_name = re.sub(r'[^\w\s]', '', current_role_name).strip()

        # Рассчитываем время на сервере
        join_date = target.joined_at
        now = datetime.datetime.now(datetime.timezone.utc)
        time_on_server = now - join_date
        years = time_on_server.days // 365
        months = (time_on_server.days % 365) // 30
        days = (time_on_server.days % 365) % 30
        time_text = f"На сервере: {years} г. {months} мес. {days} дн."

        # Создаём изображение
        img = Image.new('RGB', (700, 400), color='black')
        draw = ImageDraw.Draw(img)

        # Градиентный фон
        for y in range(400):
            r = int(150 * (y / 400))
            draw.line([(0, y), (700, y)], fill=(r, 0, 0))

        # Аватарка
        avatar_bytes = await target.display_avatar.read()
        avatar = Image.open(io.BytesIO(avatar_bytes)).resize((200, 200))

        # Круглая маска для аватарки
        mask = Image.new('L', (200, 200), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 200, 200), fill=255)
        img.paste(avatar, (480, 30), mask)
        draw.ellipse([(476, 26), (684, 234)], outline="white", width=4)

        # Шрифты
        try:
            font_large = ImageFont.load_default(size=45)
            font_small = ImageFont.load_default(size=30)
            font_info = ImageFont.load_default(size=25)
            font_time = ImageFont.load_default(size=20)

            try:
                font_large = ImageFont.truetype("DejaVuSans.ttf", 45)
                font_small = ImageFont.truetype("DejaVuSans.ttf", 30)
                font_info = ImageFont.truetype("DejaVuSans.ttf", 25)
                font_time = ImageFont.truetype("DejaVuSans.ttf", 20)
            except:
                pass
        except Exception as e:
            print(f"Ошибка загрузки шрифтов: {e}")
            return await interaction.response.send_message(
                "❌ Ошибка инициализации шрифтов",
                ephemeral=True
            )

        # Никнейм
        draw.text((50, 50), target.display_name, font=font_large, fill="white")

        # Информация с переносами
        info_display = info if info else "Пусто"

        def add_line_breaks(text, chars_per_line=22):
            lines = []
            for i in range(0, len(text), chars_per_line):
                lines.append(text[i:i + chars_per_line])
            return "\n".join(lines)

        formatted_info = add_line_breaks(info_display)
        draw.text((50, 120), f"О себе:\n{formatted_info}", font=font_info, fill="#CCCCCC", spacing=4)

        # Полоска прогресса
        bar_x, bar_y, bar_width = 50, 290, 600
        draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + 30], radius=15, fill="#333333")
        draw.rounded_rectangle([bar_x, bar_y, bar_x + int(bar_width * progress), bar_y + 30], radius=15, fill="#4CAF50")

        # Текст уровня и XP
        level_text = f"Ур. {level}"
        xp_text = f"{xp}/{next_level_xp} XP"
        draw.text((bar_x, bar_y - 50), level_text, font=font_small, fill="white")

        xp_width = draw.textlength(xp_text, font=font_small)
        draw.text((bar_x + bar_width - xp_width, bar_y - 50), xp_text, font=font_small, fill="white")

        # Роль
        role_text = f"Роль: {clean_role_name}"
        role_width = draw.textlength(role_text, font=font_small)
        role_x = bar_x + (bar_width - role_width) // 2
        draw.text((role_x, bar_y + 40), role_text, font=font_small, fill="white")

        # Время на сервере
        time_width = draw.textlength(time_text, font=font_time)
        draw.text((700 - time_width - 20, 400 - 30), time_text, font=font_time, fill="#AAAAAA")

        try:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT IFNULL(boost, 0) as boost FROM user_levels WHERE user_id = %s", (target.id,))
                boost_data = cursor.fetchone()
                boost_count = boost_data['boost'] if boost_data else 0

            boost_text = f"Бусты: {boost_count}"
            boost_font = font_small  # Используем уже загруженный шрифт
            boost_width = draw.textlength(boost_text, font=boost_font)
            boost_x = bar_x + (bar_width - boost_width) // 2
            draw.text((boost_x, bar_y - 80), boost_text, font=boost_font, fill="#FFD700")
        except Exception as e:
            print(f"Ошибка при отображении бустов: {e}")

        # Сохраняем и отправляем
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        # Отправляем результат
        await interaction.response.send_message(
            file=discord.File(buffer, f"profile_{target.id}.png")
        )

    except Exception as e:
        print(f"Ошибка в команде profile: {e}")
        await interaction.response.send_message(
            "❌ Произошла ошибка при создании профиля",
            ephemeral=True
        )


@bot.tree.command(
    name="setinfo",
    description="Добавляет информацию в ваш профиль"
)
@app_commands.describe(
    text="Текст информации (макс. 65 символов)"
)
async def set_info(interaction: discord.Interaction, text: str):
    """Добавляет информацию в профиль пользователя"""
    # 1. Валидация ввода
    if len(text) > 65:
        return await interaction.response.send_message(
            "❌ Текст слишком длинный (максимум 65 символов)",
            ephemeral=True
        )

    # 2. Санитизация текста
    cleaned_text = "".join(c for c in text if c.isprintable() and c not in "'\"\\;")

    # 3. Обновление информации в базе данных
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO user_levels (user_id, info)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE info = %s
                """, (interaction.user.id, cleaned_text, cleaned_text))
                conn.commit()

        # Отправляем подтверждение
        embed = discord.Embed(
            title="✅ Информация обновлена",
            description=f"```{cleaned_text[:65]}```",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"ID пользователя: {interaction.user.id}")

        await interaction.response.send_message(embed=embed)

    except Exception as e:
        print(f"Ошибка setinfo: {e}")
        await interaction.response.send_message(
            "❌ Ошибка при сохранении информации",
            ephemeral=True
        )


@bot.tree.command(
    name="admprofile",
    description="Управление XP и уровнем пользователя (только для админов)"
)
@app_commands.describe(
    member="Пользователь",
    xp="Количество XP для установки"
)
@app_commands.checks.has_permissions(administrator=True)
async def admprofile(interaction: discord.Interaction, member: discord.Member, xp: int):
    """Устанавливает XP пользователю и обновляет его уровень"""
    # Дополнительная проверка прав через кастомную систему
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 1. Обновляем XP и вычисляем новый уровень
                new_level = calculate_level(xp)
                cursor.execute("""
                    INSERT INTO user_levels (user_id, xp, level)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                        xp = VALUES(xp),
                        level = VALUES(level)
                """, (member.id, xp, new_level))
                conn.commit()

                # 2. Обновляем роли
                await update_roles(member, new_level)

                # 3. Получаем информацию для ответа
                cursor.execute("""
                    SELECT level FROM user_levels
                    WHERE user_id = %s
                """, (member.id,))
                current_level = cursor.fetchone()[0]
                role_name = LEVELS_CONFIG.get(current_level, {}).get("role", "Неизвестная роль")

                # Формируем красивый ответ
                embed = discord.Embed(
                    title="✅ XP успешно обновлены",
                    color=discord.Color.green(),
                    timestamp=datetime.datetime.now()
                )
                embed.add_field(name="Пользователь", value=member.mention, inline=True)
                embed.add_field(name="XP", value=str(xp), inline=True)
                embed.add_field(name="Уровень", value=f"Уровень {current_level}", inline=True)
                embed.add_field(name="Роль", value=role_name, inline=False)
                embed.set_footer(
                    text=f"Изменено администратором: {interaction.user.display_name}",
                    icon_url=interaction.user.display_avatar.url
                )

                await interaction.response.send_message(embed=embed)

    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Не удалось обновить XP: {str(e)}",
            color=discord.Color.red()
        )
        await interaction.response.send_message(
            embed=error_embed,
            ephemeral=True
        )

# Настройки команды !ticket
TICKET_CHANNEL_ID = 1360642815301914735  # Замените на ID канала, где можно создавать тикеты

@bot.tree.command(
    name="ticket",
    description="Создаёт приватный тикет (видимый только вам)"
)
async def ticket(interaction: discord.Interaction):
    """Создаёт приватный тикет для пользователя"""
    # Проверяем, что команда вызвана в нужном канале
    if interaction.channel_id != TICKET_CHANNEL_ID:
        return await interaction.response.send_message(
            f"❌ Используйте эту команду в канале <#{TICKET_CHANNEL_ID}>",
            ephemeral=True
        )

    try:
        # Получаем канал для тикетов
        channel = interaction.guild.get_channel(TICKET_CHANNEL_ID)
        if not channel:
            return await interaction.response.send_message(
                "❌ Канал для тикетов не найден",
                ephemeral=True
            )

        # Создаём приватную ветку
        thread_name = f"ticket-{interaction.user.display_name}"
        thread = await channel.create_thread(
            name=thread_name,
            auto_archive_duration=1440,
            type=discord.ChannelType.private_thread,
            reason=f"Тикет для {interaction.user}"
        )

        # Настраиваем видимость ветки
        await thread.edit(invitable=False)
        await thread.add_user(interaction.user)

        # Отправляем приветственное сообщение
        embed = discord.Embed(
            title="🔒 Ваш тикет создан",
            description="Опишите вашу проблему здесь. Администраторы скоро ответят.",
            color=0x5865F2
        )
        embed.set_footer(text=f"ID пользователя: {interaction.user.id}")
        await thread.send(embed=embed)

        # Отправляем подтверждение пользователю
        await interaction.response.send_message(
            f"✅ Тикет создан: {thread.mention}",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав для создания тикета",
            ephemeral=True
        )
    except Exception as e:
        print(f"Ошибка при создании тикета: {e}")
        await interaction.response.send_message(
            "❌ Произошла ошибка при создании тикета",
            ephemeral=True
        )


# Константы
SOUNDS_DIR = "sounds"
VOLUME_REDUCTION = 0.2
VOLUME_REDUCTION_MULTIPLIER = 0.005
UPDATE_INTERVAL = 2
MESSAGE_DELETE_DELAY = 5

# Глобальные переменные
music_queues = {}
current_tracks = {}
now_playing_messages = {}
track_progress = {}
update_progress_tasks = {}
temp_messages = []
loop_states = {}
played_history = {}

# Вспомогательные функции
def get_sound_files():
    sounds = []
    for file in os.listdir(SOUNDS_DIR):
        if file.endswith(('.mp3', '.wav', '.ogg')):
            sounds.append(file.split('.')[0])
    return sounds

def get_name_preset():
    names = []
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT name FROM save_music_url")

            rows = cursor.fetchall()

            for row in rows:
                names.append(row[0])

            return names


def create_embed(title, description, color):
    return Embed(
        title=title,
        description=description,
        color=color
    )


async def send_and_delete(interaction, embed, delay=MESSAGE_DELETE_DELAY):
    message = await interaction.followup.send(embed=embed)
    temp_messages.append(message)
    asyncio.create_task(delete_message_later(message, delay))
    return message


async def delete_message_later(message, delay):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except:
        pass
    if message in temp_messages:
        temp_messages.remove(message)


# Автодополнения
async def audio_type_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    types = ["vkplayer", "sound", "url", "preset"]
    return [
        app_commands.Choice(name=type_name, value=type_name)
        for type_name in types if current.lower() in type_name.lower()
    ]

async def preset_type_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    types = ["save", "start", "remove"]
    return [
        app_commands.Choice(name=type_name, value=type_name)
        for type_name in types if current.lower() in type_name.lower()
    ]


async def vk_action_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    actions = ["play", "stop", "skip", "queue", "volume"]
    return [
        app_commands.Choice(name=action, value=action)
        for action in actions if current.lower() in action.lower()
    ]


async def sound_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    sounds = get_sound_files()
    return [
               app_commands.Choice(name=sound, value=sound)
               for sound in sounds if current.lower() in sound.lower()
           ][:25]

async def name_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    sounds = get_name_preset()
    return [
               app_commands.Choice(name=sound, value=sound)
               for sound in sounds if current.lower() in sound.lower()
           ][:25]




class MusicControlsView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.paused = False
        self.pause_time = 0
        self.pause_position = 0
        self.progress_task = None  # Добавляем ссылку на задачу обновления прогресса

        # Создаем кнопки для первого ряда
        self.back_button = discord.ui.Button(style=discord.ButtonStyle.grey, emoji="⏮", row=0)
        self.pause_button = discord.ui.Button(style=discord.ButtonStyle.green, emoji="⏯", row=0)
        self.stop_button = discord.ui.Button(style=discord.ButtonStyle.red, emoji="⏹", row=0)
        self.loop_button = discord.ui.Button(style=discord.ButtonStyle.blurple, emoji="🔁", row=0)
        self.skip_button = discord.ui.Button(style=discord.ButtonStyle.grey, emoji="⏭", row=0)

        # Создаем кнопки для второго ряда
        self.vol_down_button = discord.ui.Button(style=discord.ButtonStyle.grey, emoji="🔉", row=1)
        self.vol_up_button = discord.ui.Button(style=discord.ButtonStyle.grey, emoji="🔊", row=1)

        # Назначаем обработчики
        self.back_button.callback = self.back_callback
        self.pause_button.callback = self.pause_callback
        self.stop_button.callback = self.stop_callback
        self.loop_button.callback = self.loop_callback
        self.skip_button.callback = self.skip_callback
        self.vol_down_button.callback = self.vol_down_callback
        self.vol_up_button.callback = self.vol_up_callback

        # Добавляем кнопки в первый ряд (5 кнопок)
        self.add_item(self.back_button)
        self.add_item(self.pause_button)
        self.add_item(self.stop_button)
        self.add_item(self.loop_button)
        self.add_item(self.skip_button)

        # Добавляем кнопки во второй ряд (2 кнопки по краям)
        self.add_item(self.vol_down_button)
        # Добавляем 3 пустых места с помощью невидимых кнопок
        for _ in range(3):
            btn = discord.ui.Button(style=discord.ButtonStyle.gray, label="\u200b", row=1, disabled=True)
            self.add_item(btn)
        self.add_item(self.vol_up_button)

    async def back_callback(self, interaction):
        guild_id = self.guild_id
        vc = interaction.guild.voice_client

        if guild_id in music_queues and guild_id in current_tracks:
            # Инициализируем историю воспроизведения если её нет
            if guild_id not in played_history:
                played_history[guild_id] = []

            # Если есть предыдущие треки в истории
            if played_history[guild_id]:
                # Достаем последний трек из истории
                previous_track = played_history[guild_id].pop()

                # Текущий трек возвращаем в начало очереди
                music_queues[guild_id].insert(0, current_tracks[guild_id])

                # Воспроизводим предыдущий трек
                current_tracks[guild_id] = previous_track
                music_queues[guild_id].insert(0, previous_track)

                # Останавливаем текущее воспроизведение
                if vc and vc.is_playing():
                    vc.stop()

                # Обновляем сообщение
                if guild_id in now_playing_messages:
                    try:
                        duration_str = str(timedelta(seconds=previous_track['duration']))[2:7]
                        embed = create_embed(
                            "⏮ Возврат к предыдущему треку",
                            f"**{previous_track['title']}**\n"
                            f"⏳ 0:00 / {duration_str}\n"
                            f"🔊 {int(previous_track['volume'] * 100 / VOLUME_REDUCTION)}%",
                            Color.blue()
                        )
                        await now_playing_messages[guild_id].edit(embed=embed)
                    except Exception as e:
                        print(f"Ошибка обновления сообщения: {e}")

            await interaction.response.defer()

    async def pause_callback(self, interaction):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.defer()
            return

        try:
            if vc.is_playing() and not self.paused:
                # Пауза
                self.paused = True
                self.pause_time = asyncio.get_event_loop().time()
                self.pause_position = self.pause_time - track_progress[self.guild_id]['start_time']
                vc.pause()

                # Обновляем сообщение один раз
                if self.guild_id in now_playing_messages and self.guild_id in current_tracks:
                    try:
                        track = current_tracks[self.guild_id]
                        current_str = str(timedelta(seconds=int(self.pause_position)))[2:7]
                        duration_str = str(timedelta(seconds=track['duration']))[2:7]

                        embed = create_embed(
                            "⏸ Пауза",
                            f"**{track['title']}**\n"
                            f"⏳ {current_str} / {duration_str}\n"
                            f"🔊 {int(track['volume'] * 100 / VOLUME_REDUCTION)}%",
                            Color.orange()
                        )
                        # Сохраняем состояние паузы в текущем треке
                        current_tracks[self.guild_id]['paused'] = True
                        await now_playing_messages[self.guild_id].edit(embed=embed)
                    except Exception as e:
                        print(f"Ошибка обновления сообщения при паузе: {e}")

            elif vc.is_paused() and self.paused:
                # Возобновление
                self.paused = False
                pause_duration = asyncio.get_event_loop().time() - self.pause_time
                track_progress[self.guild_id]['start_time'] += pause_duration
                vc.resume()

                # Обновляем состояние паузы в текущем треке
                if self.guild_id in current_tracks:
                    current_tracks[self.guild_id]['paused'] = False

                # Перезапускаем обновление прогресса
                if self.guild_id in update_progress_tasks:
                    update_progress_tasks[self.guild_id].cancel()
                update_progress_tasks[self.guild_id] = asyncio.create_task(update_progress(self.guild_id))

        except Exception as e:
            print(f"Ошибка в обработчике паузы: {e}")
        finally:
            await interaction.response.defer()

    async def stop_callback(self, interaction):
        guild_id = self.guild_id
        vc = interaction.guild.voice_client

        if vc and vc.is_connected():
            music_queues.pop(guild_id, None)
            current_tracks.pop(guild_id, None)
            track_progress.pop(guild_id, None)
            loop_states.pop(guild_id, None)

            if guild_id in now_playing_messages:
                try:
                    await now_playing_messages[guild_id].delete()
                except:
                    pass
                now_playing_messages.pop(guild_id, None)

            await vc.disconnect()
        await interaction.response.defer()

    async def loop_callback(self, interaction):
        guild_id = self.guild_id
        if guild_id not in loop_states:
            loop_states[guild_id] = False

        loop_states[guild_id] = not loop_states[guild_id]

        # Обновляем сообщение
        if guild_id in now_playing_messages and guild_id in current_tracks:
            try:
                track = current_tracks[guild_id]
                progress = track_progress.get(guild_id, {'start_time': 0, 'duration': 0})
                current_time = asyncio.get_event_loop().time() - progress['start_time']
                current_str = str(timedelta(seconds=int(current_time)))[2:7]
                duration_str = str(timedelta(seconds=track['duration']))[2:7]
                is_looping = loop_states.get(self.guild_id, False)

                loop_status = "✅" if loop_states[guild_id] else "❌"
                embed = create_embed(
                    "🎶 Сейчас играет",
                    f"**{track['title']}**\n"
                    f"⏳ {current_str} / {duration_str}\n"
                    f"🔊 {int(track['volume'] * 100 / VOLUME_REDUCTION)}%\n"
                    f"🔁 : {loop_status}",
                    Color.green()
                )
                await now_playing_messages[guild_id].edit(embed=embed)
            except Exception as e:
                print(f"Ошибка обновления сообщения: {e}")

        await interaction.response.defer()

    async def skip_callback(self, interaction):
        guild_id = self.guild_id
        vc = interaction.guild.voice_client

        if vc and vc.is_playing():
            vc.stop()
        await interaction.response.defer()

    async def vol_down_callback(self, interaction):
        guild_id = self.guild_id
        vc = interaction.guild.voice_client

        if vc and vc.is_playing() and guild_id in current_tracks:
            current_vol = current_tracks[guild_id]['volume']
            new_vol = max(0.0, current_vol - (10 * VOLUME_REDUCTION / 100))
            current_tracks[guild_id]['volume'] = new_vol

            if isinstance(vc.source, discord.PCMVolumeTransformer):
                vc.source.volume = new_vol

            if guild_id in now_playing_messages and guild_id in current_tracks:
                try:
                    track = current_tracks[guild_id]
                    progress = track_progress.get(guild_id, {'start_time': 0, 'duration': 0})
                    current_time = asyncio.get_event_loop().time() - progress['start_time']
                    current_str = str(timedelta(seconds=int(current_time)))[2:7]
                    duration_str = str(timedelta(seconds=track['duration']))[2:7]
                    is_looping = loop_states.get(self.guild_id, False)
                    loop_status = "✅" if is_looping else "❌"

                    embed = create_embed(
                        "🎶 Сейчас играет",
                        f"**{track['title']}**\n"
                        f"⏳ {current_str} / {duration_str}\n"
                        f"🔊 {int(new_vol * 100 / VOLUME_REDUCTION)}%",
                        Color.green()
                    )
                    await now_playing_messages[guild_id].edit(embed=embed)
                except Exception as e:
                    print(f"Ошибка обновления сообщения о громкости: {e}")
        await interaction.response.defer()

    async def vol_up_callback(self, interaction):
        guild_id = self.guild_id
        vc = interaction.guild.voice_client

        if vc and vc.is_playing() and guild_id in current_tracks:
            current_vol = current_tracks[guild_id]['volume']
            new_vol = min(3.0 * VOLUME_REDUCTION, current_vol + (10 * VOLUME_REDUCTION / 100))
            current_tracks[guild_id]['volume'] = new_vol

            if isinstance(vc.source, discord.PCMVolumeTransformer):
                vc.source.volume = new_vol

            if guild_id in now_playing_messages and guild_id in current_tracks:
                try:
                    track = current_tracks[guild_id]
                    progress = track_progress.get(guild_id, {'start_time': 0, 'duration': 0})
                    current_time = asyncio.get_event_loop().time() - progress['start_time']
                    current_str = str(timedelta(seconds=int(current_time)))[2:7]
                    duration_str = str(timedelta(seconds=track['duration']))[2:7]
                    is_looping = loop_states.get(self.guild_id, False)
                    loop_status = "✅" if is_looping else "❌"

                    embed = create_embed(
                        "🎶 Сейчас играет",
                        f"**{track['title']}**\n"
                        f"⏳ {current_str} / {duration_str}\n"
                        f"🔊 {int(new_vol * 100 / VOLUME_REDUCTION)}%",
                        Color.green()
                    )
                    await now_playing_messages[guild_id].edit(embed=embed)
                except Exception as e:
                    print(f"Ошибка обновления сообщения о громкости: {e}")
        await interaction.response.defer()


async def handle_play(interaction, url, voice_channel, volume, guild_id, vc):
    is_playlist = 'playlist' in url.lower()
    async with aiohttp.ClientSession() as session:
        if is_playlist:
            await process_playlist(interaction, url, session, voice_channel, volume, guild_id, vc)
        else:
            await process_single_track(interaction, url, session, voice_channel, volume, guild_id, vc)


async def process_playlist(interaction, url, session, voice_channel, volume, guild_id, vc):
    try:
        playlist_id = url.split('playlist/')[1].split('_')[1]
        owner_id = url.split('playlist/')[1].split('_')[0]

        async with session.get(
                "https://api.vk.com/method/audio.get",
                params={
                    'access_token': f"{ASSETTOKEN}",
                    'owner_id': owner_id,
                    'playlist_id': playlist_id,
                    'v': "5.131",
                    'count': 100
                }
        ) as resp:
            data = await resp.json()
            if 'response' not in data:
                raise Exception("Не удалось получить плейлист")

            tracks = data['response']['items']
            if not tracks:
                raise Exception("Плейлист пуст")

            if not vc:
                vc = await voice_channel.connect()
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)

            if guild_id not in music_queues:
                music_queues[guild_id] = []

            effective_volume = (volume / 100) * VOLUME_REDUCTION
            for track in tracks:
                music_queues[guild_id].append({
                    'url': track['url'],
                    'title': f"{track['artist']} - {track['title']}",
                    'duration': track.get('duration', 0),
                    'volume': effective_volume
                })

            embed = create_embed(
                "🎵 Плейлист добавлен",
                f"Добавлено {len(tracks)} треков в очередь\n"
                f"🔊 Громкость: {volume}%",
                Color.green()
            )
            await send_and_delete(interaction, embed)

            if not vc.is_playing():
                await play_next(interaction, guild_id, volume=volume)

    except Exception as e:
        raise Exception(f"Ошибка обработки плейлиста: {str(e)}")


async def process_single_track(interaction, url, session, voice_channel, volume, guild_id, vc):
    try:
        if "audio" not in url:
            raise Exception("Неверный формат ссылки VK!")

        parts = url.split("audio")[1].split("_")
        if len(parts) < 2:
            raise Exception("Не удалось распознать ID трека")

        owner_id, audio_id = parts[0], parts[1]

        async with session.get(
                "https://api.vk.com/method/audio.getById",
                params={
                    'access_token': f"{ASSETTOKEN}",
                    'audios': f"{owner_id}_{audio_id}",
                    'v': "5.131"
                }
        ) as resp:
            data = await resp.json()
            if 'response' not in data:
                raise Exception("Не удалось получить трек")

            track = data['response'][0]
            if not track.get('url'):
                raise Exception("Трек недоступен")

            if not vc:
                vc = await voice_channel.connect()
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)

            if guild_id not in music_queues:
                music_queues[guild_id] = []

            effective_volume = (volume / 100) * VOLUME_REDUCTION
            duration = track.get('duration', 0)
            track_info = {
                'url': track['url'],
                'title': f"{track.get('artist', '')} - {track.get('title', 'Без названия')}",
                'duration': duration,
                'volume': effective_volume
            }

            music_queues[guild_id].append(track_info)
            current_tracks[guild_id] = track_info

            duration_str = str(timedelta(seconds=duration))[2:7]
            embed = create_embed(
                "🎵 Трек добавлен",
                f"**{track_info['title']}**\n"
                f"⏳ Длительность: {duration_str}\n"
                f"🔊 Громкость: {volume}%",
                Color.green()
            )
            await send_and_delete(interaction, embed)

            if not vc.is_playing():
                await play_next(interaction, guild_id)

    except Exception as e:
        raise Exception(f"Ошибка обработки трека: {str(e)}")


async def handle_stop(interaction, guild_id, vc):
    if vc and vc.is_connected():
        music_queues.pop(guild_id, None)
        current_tracks.pop(guild_id, None)
        track_progress.pop(guild_id, None)
        loop_states.pop(guild_id, None)

        if guild_id in now_playing_messages:
            try:
                await now_playing_messages[guild_id].delete()
            except:
                pass
            now_playing_messages.pop(guild_id, None)

        await vc.disconnect()

        embed = create_embed("⏹ Воспроизведение остановлено", "", Color.blue())
        await send_and_delete(interaction, embed)
    else:
        embed = create_embed("❌ Ошибка", "Бот не подключен к голосовому каналу", Color.red())
        await send_and_delete(interaction, embed)


async def handle_skip(interaction, guild_id, vc):
    if vc and vc.is_playing():
        vc.stop()
        embed = create_embed("⏭ Трек пропущен", "", Color.blue())
        await send_and_delete(interaction, embed)
    else:
        embed = create_embed("❌ Ошибка", "Сейчас ничего не играет", Color.red())
        await send_and_delete(interaction, embed)


async def handle_queue(interaction, guild_id):
    if guild_id in music_queues and music_queues[guild_id]:
        now_playing = current_tracks.get(guild_id, {}).get('title', "Ничего")
        queue_list = []

        for i, track in enumerate(music_queues[guild_id][:10], 1):
            duration = str(timedelta(seconds=track.get('duration', 0)))[2:7]
            queue_list.append(f"{i}. **{track['title']}** `[{duration}]`")

        embed = create_embed(
            "📜 Очередь воспроизведения",
            f"**Сейчас играет:** {now_playing}\n\n" + "\n".join(queue_list),
            Color.gold()
        )

        if len(music_queues[guild_id]) > 10:
            embed.set_footer(text=f"И ещё {len(music_queues[guild_id]) - 10} треков...")

        await send_and_delete(interaction, embed)
    else:
        embed = create_embed("📜 Очередь воспроизведения", "Очередь пуста", Color.gold())
        await send_and_delete(interaction, embed)


async def handle_volume(interaction, volume, guild_id, vc):
    if not vc or not vc.is_playing():
        embed = create_embed("❌ Ошибка", "Сейчас ничего не играет", Color.red())
        await send_and_delete(interaction, embed)
        return

    effective_volume = (volume / 100) * VOLUME_REDUCTION
    if isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = effective_volume

    if guild_id in current_tracks:
        current_tracks[guild_id]['volume'] = effective_volume

    embed = create_embed("🔊 Громкость изменена", f"Установлена громкость: {volume}%", Color.blue())
    await send_and_delete(interaction, embed)

    if guild_id in now_playing_messages and guild_id in current_tracks:
        try:
            track = current_tracks[guild_id]
            progress = track_progress.get(guild_id, {'start_time': 0, 'duration': 0})
            current_time = asyncio.get_event_loop().time() - progress['start_time']
            current_str = str(timedelta(seconds=int(current_time)))[2:7]
            duration_str = str(timedelta(seconds=track['duration']))[2:7]

            embed = create_embed(
                "🎶 Сейчас играет",
                f"**{track['title']}**\n"
                f"⏳ {current_str} / {duration_str}\n"
                f"🔊 {volume}%",
                Color.green()
            )
            await now_playing_messages[guild_id].edit(embed=embed)
        except Exception as e:
            print(f"Ошибка обновления сообщения о громкости: {e}")


async def play_next(interaction, guild_id, volume=30):
    vc = interaction.guild.voice_client

    if not (guild_id in music_queues and music_queues[guild_id]):
        if vc and vc.is_connected():
            await vc.disconnect()
        return

    try:
        if guild_id in now_playing_messages:
            try:
                await now_playing_messages[guild_id].delete()
            except:
                pass

        current_volume = (volume / 100) * VOLUME_REDUCTION if volume is not None else (VOLUME_REDUCTION if guild_id not in current_tracks else current_tracks[guild_id]['volume'])

        if guild_id in loop_states and loop_states[guild_id] and guild_id in current_tracks:
            track = current_tracks[guild_id]
        else:
            track = music_queues[guild_id].pop(0)

        track['volume'] = current_volume
        current_tracks[guild_id] = track

        track_progress[guild_id] = {
            'start_time': asyncio.get_event_loop().time(),
            'duration': track['duration'],
            'last_update': 0
        }

        source = discord.FFmpegPCMAudio(
            track['url'],
            before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            options='-vn'
        )
        source = discord.PCMVolumeTransformer(source, volume=track['volume'])

        def after_playing(error):
            if error:
                print(f"Playback error: {error}")

            coro = vc.disconnect()
            fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
            try:
                fut.result(timeout=5)
            except:
                pass

            if guild_id in loop_states and loop_states[guild_id] and guild_id in current_tracks:
                music_queues[guild_id].insert(0, current_tracks[guild_id])

            asyncio.run_coroutine_threadsafe(
                play_next(interaction, guild_id),
                bot.loop
            )

        vc.play(source, after=after_playing)

        duration_str = str(timedelta(seconds=track['duration']))[2:7]
        loop_status = "✅" if loop_states.get(guild_id, False) else "❌"

        embed = create_embed(
            "🎶 Сейчас играет",
            f"**{track['title']}**\n"
            f"⏳ 0:00 / {duration_str}\n"
            f"🔊 {int(track['volume'] * 100 / VOLUME_REDUCTION)}%\n"
            f"🔁 : {loop_status}",
            Color.green()
        )

        view = MusicControlsView(guild_id)
        now_playing_messages[guild_id] = await interaction.followup.send(
            embed=embed,
            view=view
        )

        if guild_id not in update_progress_tasks or update_progress_tasks[guild_id].done():
            update_progress_tasks[guild_id] = asyncio.create_task(
                update_progress(guild_id)
            )

    except Exception as e:
        print(f"Error in play_next: {e}")
        if guild_id in music_queues and music_queues[guild_id]:
            await asyncio.sleep(1)
            await play_next(interaction, guild_id)


async def update_progress(guild_id):
    while guild_id in track_progress:
        # Проверяем состояние паузы через текущий трек
        is_paused = False
        if guild_id in current_tracks and 'paused' in current_tracks[guild_id]:
            is_paused = current_tracks[guild_id]['paused']

        if is_paused:
            await asyncio.sleep(1)
            continue

        progress = track_progress[guild_id]
        current_time = asyncio.get_event_loop().time() - progress['start_time']

        if current_time - progress['last_update'] >= UPDATE_INTERVAL:
            progress['last_update'] = current_time

            if guild_id in now_playing_messages and guild_id in current_tracks:
                try:
                    track = current_tracks[guild_id]
                    current_str = str(timedelta(seconds=int(current_time)))[2:7]
                    duration_str = str(timedelta(seconds=track['duration']))[2:7]

                    loop_status = "✅" if loop_states.get(guild_id, False) else "❌"
                    embed = create_embed(
                        "🎶 Сейчас играет",
                        f"**{track['title']}**\n"
                        f"⏳ {current_str} / {duration_str}\n"
                        f"🔊 {int(track['volume'] * 100 / VOLUME_REDUCTION)}%\n"
                        f"🔁 : {loop_status}",
                        Color.green()
                    )
                    await now_playing_messages[guild_id].edit(embed=embed)
                except Exception as e:
                    print(f"Ошибка обновления прогресса: {e}")

        await asyncio.sleep(1)

    # Очистка при завершении
    if guild_id in now_playing_messages:
        try:
            await now_playing_messages[guild_id].delete()
        except:
            pass
        now_playing_messages.pop(guild_id, None)


# Класс для управления воспроизведением URL
class URLControls(discord.ui.View):
    def __init__(self, voice_client, initial_volume, title, duration, interaction):
        super().__init__(timeout=None)
        self.voice_client = voice_client
        self.volume = initial_volume
        self.title = title
        self.duration = duration
        self.duration_str = str(timedelta(seconds=duration)).split('.')[0] if duration else '0:00:00'
        self.message = None
        self._deleted = False
        self.interaction = interaction
        self.start_time = asyncio.get_event_loop().time()
        self.is_paused = False
        self.pause_time = 0
        self.pause_duration = 0
        self._keep_alive_task = None
        self._is_stopped = False

    def after_playing(self, error):
        """Обработчик завершения воспроизведения"""
        if error:
            print(f"Playback finished with error: {error}")

        coro = self.cleanup()
        asyncio.run_coroutine_threadsafe(coro, self.interaction.client.loop)

    async def ensure_voice_keepalive(self):
        """Фоновая задача для поддержания соединения"""
        while not self._is_stopped and self.voice_client and self.voice_client.is_connected():
            try:
                await asyncio.sleep(20)
                if self.voice_client.is_playing() and not self.voice_client.is_paused():
                    await self.update_controls()
            except:
                break

    async def on_timeout(self):
        """Обработчик таймаута теперь не отключает бота"""
        self._is_stopped = True
        if self._keep_alive_task:
            self._keep_alive_task.cancel()
        await self.cleanup()

    async def cleanup(self):
        """Безопасное завершение всех ресурсов"""
        if self._deleted:
            return

        self._deleted = True
        try:
            if self.voice_client:
                if self.voice_client.is_playing():
                    self.voice_client.stop()
                await asyncio.sleep(1)
                if self.voice_client.is_connected():
                    await self.voice_client.disconnect(force=True)
        except Exception as e:
            print(f"Cleanup error: {e}")

        if self.message:
            try:
                await self.message.delete()
            except:
                pass

    def create_embed(self):
        """Создает embed для отображения информации о воспроизведении"""
        current_time = asyncio.get_event_loop().time() - self.start_time - self.pause_duration
        current_str = str(timedelta(seconds=int(current_time))).split('.')[0]

        embed = discord.Embed(
            title="🎶 Воспроизведение URL",
            description=f"**{self.title}**",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Прогресс",
            value=f"{current_str} / {self.duration_str}",
            inline=False
        )
        embed.add_field(
            name="Громкость",
            value=f"{self.volume}%",
            inline=True
        )
        embed.add_field(
            name="Состояние",
            value="⏸ Пауза" if self.is_paused else "▶ Воспроизведение",
            inline=True
        )
        return embed

    async def update_controls(self):
        """Обновляет сообщение с текущим состоянием воспроизведения"""
        if self._deleted or not self.message:
            return

        try:
            embed = self.create_embed()
            await self.message.edit(embed=embed, view=self)
        except discord.NotFound:
            self._deleted = True
        except Exception as e:
            print(f"Update controls error: {e}")

    @discord.ui.button(label="⏯", style=discord.ButtonStyle.blurple)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._deleted or not self.voice_client:
            return

        try:
            if self.voice_client.is_playing():
                self.voice_client.pause()
                self.is_paused = True
                self.pause_time = asyncio.get_event_loop().time()
                button.label = "▶"
            elif self.voice_client.is_paused():
                self.voice_client.resume()
                self.is_paused = False
                self.pause_duration += asyncio.get_event_loop().time() - self.pause_time
                button.label = "⏸"

            await interaction.response.defer()
            await self.update_controls()
        except Exception as e:
            print(f"Pause error: {e}")
            await interaction.response.defer()

    @discord.ui.button(label="⏹", style=discord.ButtonStyle.red)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Обработчик кнопки остановки"""
        await interaction.response.defer()
        self._is_stopped = True
        await self.cleanup()

    @discord.ui.button(label="🔉", style=discord.ButtonStyle.grey)
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._deleted or not self.voice_client:
            return

        try:
            self.volume = max(0, self.volume - 10)
            new_volume = (self.volume / 100) * 0.5
            if hasattr(self.voice_client.source, 'volume'):
                self.voice_client.source.volume = new_volume

            await interaction.response.defer()
            await self.update_controls()
        except Exception as e:
            print(f"Volume down error: {e}")
            await interaction.response.defer()

    @discord.ui.button(label="🔊", style=discord.ButtonStyle.grey)
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._deleted or not self.voice_client:
            return

        try:
            self.volume = min(100, self.volume + 10)
            new_volume = (self.volume / 100) * 0.5
            if hasattr(self.voice_client.source, 'volume'):
                self.voice_client.source.volume = new_volume

            await interaction.response.defer()
            await self.update_controls()
        except Exception as e:
            print(f"Volume up error: {e}")
            await interaction.response.defer()


async def handle_url_playback(interaction, url, channel, volume):
    vol = volume if volume else 50
    await interaction.response.defer()

    try:
        # Проверяем существующее подключение
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect(force=True)
            await asyncio.sleep(1)

        # Получаем информацию о треке
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'extract_flat': True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'url' not in info:
                if 'entries' in info:
                    info = info['entries'][0]
                else:
                    raise Exception("Не удалось получить аудио URL")

            audio_url = info['url']
            title = info.get('title', 'Неизвестный трек')
            duration = info.get('duration', 0)

        # Подключаемся к голосовому каналу
        voice_client = await channel.connect(timeout=30.0)

        # Настройки FFmpeg (используем PCM)
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
            'options': '-vn -acodec pcm_s16le -f s16le -ar 48000 -ac 2 -threads 1'
        }

        # Создаем аудио источник (PCM)
        audio_source = discord.FFmpegPCMAudio(
            audio_url,
            **ffmpeg_options
        )

        # Применяем регулятор громкости
        audio_source = discord.PCMVolumeTransformer(audio_source)
        audio_source.volume = vol / 100  # Громкость от 0.0 до 1.0

        # Создаем контролы
        controls = URLControls(voice_client, vol, title, duration, interaction)
        controls._keep_alive_task = interaction.client.loop.create_task(controls.ensure_voice_keepalive())

        # Запускаем воспроизведение
        voice_client.play(audio_source, after=controls.after_playing)

        # Отправляем сообщение с контролами
        message = await interaction.followup.send(
            embed=controls.create_embed(),
            view=controls
        )
        controls.message = message

    except Exception as e:
        print(f"Playback error: {e}")
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect(force=True)
        await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)

# Основная команда (упрощенная версия для URL)
@bot.tree.command(name="audio", description="Управление аудио (VK, звуки, URL)")
@app_commands.describe(
    type="Тип аудио (vkplayer/sound/url)",
    preset="сохранить url чтоб потом запустить",
    action="Действие (только для vkplayer)",
    url="Ссылка (для vkplayer или url)",
    name="название пресета для preset",
    channel="Голосовой канал",
    sound="Звук из библиотеки (только для sound)",
    volume="Громкость (1-100% для sound/url, 1-300% для vkplayer)"
)
@app_commands.autocomplete(
    type=audio_type_autocomplete,
    action=vk_action_autocomplete,
    sound=sound_autocomplete,
    preset=preset_type_autocomplete,
    name=name_autocomplete
)
async def audio_command(
        interaction: discord.Interaction,
        type: str,
        preset: Optional[str] = None,
        name: Optional[str] = None,
        action: Optional[str] = None,
        url: Optional[str] = None,
        channel: Optional[discord.VoiceChannel] = None,
        sound: Optional[str] = None,
        volume: Optional[app_commands.Range[int, 1, 300]] = None
):
    if not await check_command_access_app(interaction):
        return await interaction.response.send_message(
            "❌ Недостаточно прав",
            ephemeral=True
        )

    # Обработка звуков
    if type == "sound":
        if not sound:
            return await interaction.response.send_message("❌ Укажите звук!", ephemeral=True)
        if not channel:
            return await interaction.response.send_message("❌ Укажите канал!", ephemeral=True)

        sound_path = os.path.join(SOUNDS_DIR, f"{sound}.mp3")
        if not os.path.exists(sound_path):
            return await interaction.response.send_message("❌ Звук не найден!", ephemeral=True)

        vol = volume if volume else 50
        final_volume = (vol / 100) * VOLUME_REDUCTION_MULTIPLIER

        await interaction.response.send_message(
            f"🔊 Воспроизвожу **{sound}** в **{channel.name}**\n",
            ephemeral=True
        )

        try:
            vc = await channel.connect()
            source = discord.FFmpegPCMAudio(
                executable="ffmpeg",
                source=sound_path,
                options=f"-filter:a volume={final_volume}"
            )
            vc.play(source)

            while vc.is_playing():
                await asyncio.sleep(0.1)

        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка: {e}", ephemeral=True)
        finally:
            if interaction.guild.voice_client:
                await interaction.guild.voice_client.disconnect()



    elif type == "preset":
        if preset == "save":
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        # Проверяем существование записи
                        cursor.execute("SELECT 1 FROM save_music_url WHERE name = %s", (name,))
                        if cursor.fetchone():
                            # Обновляем URL, если имя уже существует
                            cursor.execute("""
                                UPDATE save_music_url 
                                SET url = %s 
                                WHERE name = %s
                            """, (url, name))
                        else:
                            # Вставляем новую запись
                            cursor.execute("""
                                INSERT INTO save_music_url (name, url)
                                VALUES (%s, %s)
                            """, (name, url))
                        conn.commit()

                        success_embed = discord.Embed(
                            title="✅ Успешно",
                            description=f"Сохранено: {name} → {url}",
                            color=discord.Color.green()
                        )
                        await interaction.response.send_message(
                            embed=success_embed,
                            ephemeral=True
                        )
            except Exception as e:
                error_embed = discord.Embed(
                    title="❌ Ошибка",
                    description=f"Не удалось сохранить: {str(e)}",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(
                    embed=error_embed,
                    ephemeral=True
                )
        if preset =="remove":
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("DELETE FROM save_music_url WHERE name = %s", (name,))
                        conn.commit()

                        success_embed = discord.Embed(
                            title="✅ Успешно",
                            description=f"Удалена запись с именем: {name}",
                            color=discord.Color.green()
                        )
                        await interaction.response.send_message(
                            embed=success_embed,
                            ephemeral=True  # Сообщение видно только пользователю
                        )

            except Exception as e:
                error_embed = discord.Embed(
                    title="❌ Ошибка",
                    description=f"Ошибка при удалении записи: {str(e)}",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(
                    embed=error_embed,
                    ephemeral=True
                )
        if preset == "start":
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT url FROM save_music_url WHERE name = %s", (name,))
                        result = cursor.fetchone()  # Получаем одну запись (или None, если ничего не найдено)

                        if result:
                            url = result[0]  # Извлекаем URL из кортежа
                            await handle_url_playback(interaction, url, channel, volume)
                        else:
                            not_found_embed = discord.Embed(
                                title="🤔 Ничего не найдено",
                                description=f"Запись с именем '{name}' не найдена.",
                                color=discord.Color.orange()
                            )
                            await interaction.response.send_message(
                                embed=not_found_embed,
                                ephemeral=True
                            )

            except Exception as e:
                error_embed = discord.Embed(
                    title="❌ Ошибка",
                    description=f"Ошибка при получении URL: {str(e)}",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(
                    embed=error_embed,
                    ephemeral=True
                )



    elif type == "url":
        if not url:
            return await interaction.response.send_message("❌ Укажите URL!", ephemeral=True)
        if not channel:
            return await interaction.response.send_message("❌ Укажите канал!", ephemeral=True)

        await handle_url_playback(interaction, url, channel, volume)

    # Обработка VK Player
    elif type == "vkplayer":
        await interaction.response.defer()
        try:
            guild_id = interaction.guild.id
            voice_channel = channel or interaction.user.voice.channel if interaction.user.voice else None

            if action not in ["queue", "volume"] and not voice_channel:
                embed = create_embed("❌ Ошибка", "Вы не в голосовом канале!", Color.red())
                await send_and_delete(interaction, embed)
                return

            vc = interaction.guild.voice_client
            vol = volume if volume else 30

            if action == "play":
                if not url:
                    embed = create_embed("❌ Ошибка", "Укажите ссылку на трек или плейлист VK!", Color.red())
                    await send_and_delete(interaction, embed)
                    return
                await handle_play(interaction, url, voice_channel, vol, guild_id, vc)
            elif action == "stop":
                await handle_stop(interaction, guild_id, vc)
            elif action == "skip":
                await handle_skip(interaction, guild_id, vc)
            elif action == "queue":
                await handle_queue(interaction, guild_id)
            elif action == "volume":
                await handle_volume(interaction, vol, guild_id, vc)
            else:
                embed = create_embed("❌ Ошибка", "Укажите действие для VK Player", Color.red())
                await send_and_delete(interaction, embed)

        except Exception as e:
            embed = create_embed("❌ Ошибка", str(e), Color.red())
            await send_and_delete(interaction, embed)
            print(f"Ошибка VKPlayer: {e}")


# def get_vkplayer_webhook(guild_id):
#     conn = get_db_connection()
#     if not conn:
#         return None
#
#     try:
#         cursor = conn.cursor(dictionary=True)
#         cursor.execute("""
#             SELECT vkplayer_webhook FROM bot_settings
#             WHERE guild_id = %s
#         """, (guild_id,))
#         result = cursor.fetchone()
#         return result['vkplayer_webhook'] if result else None
#     except Error:
#         return None
#     finally:
#         conn.close()
#
#
# @bot.tree.command(
#     name="settings",
#     description="Настройки бота"
# )
# @app_commands.describe(
#     action="Действие",
#     webhook_url="URL вебхука для VK Player"
# )
# @app_commands.choices(
#     action=[
#         app_commands.Choice(name="Установить вебхук", value="install"),
#         app_commands.Choice(name="Показать настройки", value="show")
#     ]
# )
# async def settings(
#     interaction: discord.Interaction,
#     action: app_commands.Choice[str],
#     webhook_url: str = None
# ):
#     """Управление настройками бота"""
#     if not await check_command_access_app(interaction):
#         return await interaction.response.send_message(
#             "❌ Недостаточно прав",
#             ephemeral=True
#         )
#
#     if action.value == "install":
#         if not webhook_url:
#             return await interaction.response.send_message(
#                 "❌ Укажите URL вебхука",
#                 ephemeral=True
#             )
#
#         # Сохраняем в базу данных
#         conn = get_db_connection()
#         if not conn:
#             return await interaction.response.send_message(
#                 "❌ Ошибка подключения к БД",
#                 ephemeral=True
#             )
#
#         try:
#             cursor = conn.cursor()
#             cursor.execute("""
#                 INSERT INTO bot_settings (guild_id, vkplayer_webhook)
#                 VALUES (%s, %s)
#                 ON DUPLICATE KEY UPDATE vkplayer_webhook = VALUES(vkplayer_webhook)
#             """, (interaction.guild.id, webhook_url))
#             conn.commit()
#
#             await interaction.response.send_message(
#                 f"✅ Вебхук VK Player успешно установлен: `{webhook_url[:30]}...`",
#                 ephemeral=True
#             )
#         except Error as e:
#             await interaction.response.send_message(
#                 f"❌ Ошибка БД: {e}",
#                 ephemeral=True
#             )
#         finally:
#             conn.close()
#
#     elif action.value == "show":
#         conn = get_db_connection()
#         if not conn:
#             return await interaction.response.send_message(
#                 "❌ Ошибка подключения к БД",
#                 ephemeral=True
#             )
#
#         try:
#             cursor = conn.cursor(dictionary=True)
#             cursor.execute("""
#                 SELECT vkplayer_webhook FROM bot_settings
#                 WHERE guild_id = %s
#             """, (interaction.guild.id,))
#             settings = cursor.fetchone()
#
#             embed = discord.Embed(
#                 title="Текущие настройки",
#                 color=discord.Color.blue()
#             )
#             embed.add_field(
#                 name="VK Player Webhook",
#                 value=settings['vkplayer_webhook'] if settings and settings['vkplayer_webhook'] else "Не установлен",
#                 inline=False
#             )
#
#             await interaction.response.send_message(embed=embed, ephemeral=True)
#         except Error as e:
#             await interaction.response.send_message(
#                 f"❌ Ошибка БД: {e}",
#                 ephemeral=True
#             )
#         finally:
#             conn.close()

bot.run(TOKEN)