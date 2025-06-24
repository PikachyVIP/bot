import discord
from discord.ext import commands
from discord import app_commands
import mysql.connector
from mysql.connector import Error
from data import mysqlconf



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


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.MYSQL_CONFIG = mysqlconf

        self.shop_roles = {
            "✔": {"price": 1000, "description": "тестроль"}
        }

    def get_db_connection(self):
        """Создает подключение к базе данных"""
        try:
            return mysql.connector.connect(**self.MYSQL_CONFIG)
        except Error as e:
            print(f"Ошибка подключения к MySQL: {e}")
            return None

    async def get_user_xp(self, user_id):
        """Получает XP пользователя"""
        conn = self.get_db_connection()
        if not conn:
            return 0

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT xp FROM user_levels WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            return result['xp'] if result else 0
        except Error as e:
            print(f"Ошибка при получении XP: {e}")
            return 0
        finally:
            if conn and conn.is_connected():
                conn.close()

    async def update_user_xp(self, user_id, amount):
        """Обновляет XP пользователя"""
        conn = self.get_db_connection()
        if not conn:
            return False

        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE user_levels SET xp = xp - %s WHERE user_id = %s",
                (amount, user_id)
            )
            conn.commit()
            return True
        except Error as e:
            print(f"Ошибка при обновлении XP: {e}")
            conn.rollback()
            return False
        finally:
            if conn and conn.is_connected():
                conn.close()

    @app_commands.command(name="shop", description="Магазин ролей за XP")
    async def shop_command(self, interaction: discord.Interaction):
        """Обработчик команды /shop"""
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.delete_original_response()
        except:
            pass

        user_xp = await self.get_user_xp(interaction.user.id)

        def calculate_level(xp: int) -> int:
            """Вычисляет уровень пользователя на основе его XP"""
            for level, data in sorted(LEVELS_CONFIG.items(), reverse=True):
                if xp >= data["xp"]:
                    return level
            return 1

        # Проверяем текущий уровень пользователя
        current_level = calculate_level(user_xp)

        embed = discord.Embed(
            title="🏪 Магазин ролей",
            description=f"Ваш баланс: **{user_xp} XP** (Уровень {current_level})\n\nВыберите роль для покупки:",
            color=discord.Color.gold()
        )

        for role_name, data in self.shop_roles.items():
            embed.add_field(
                name=f"{role_name} - {data['price']} XP",
                value=data['description'],
                inline=False
            )

        view = discord.ui.View(timeout=None)
        role_select = discord.ui.Select(
            placeholder="Выберите роль для покупки",
            options=[
                discord.SelectOption(
                    label=f"{role_name} ({data['price']} XP)",
                    value=role_name,
                    description=data['description']
                ) for role_name, data in self.shop_roles.items()
            ]
        )

        async def update_roles(member: discord.Member, new_level: int):
            """Обновляет роли пользователя в соответствии с его уровнем"""
            # Удаляем все старые роли уровней
            for level_data in LEVELS_CONFIG.values():
                role = discord.utils.get(member.guild.roles, name=level_data["role"])
                if role and role in member.roles:
                    await member.remove_roles(role)

            # Добавляем новую роль уровня
            new_role_data = LEVELS_CONFIG.get(new_level)
            if new_role_data:
                new_role = discord.utils.get(member.guild.roles, name=new_role_data["role"])
                if new_role:
                    await member.add_roles(new_role)

        async def select_callback(interaction: discord.Interaction):
            selected_role_name = role_select.values[0]
            role_data = self.shop_roles[selected_role_name]

            target_role = discord.utils.get(interaction.guild.roles, name=selected_role_name)
            if not target_role:
                return await interaction.response.send_message(
                    f"Роль {selected_role_name} не найдена на сервере!",
                    ephemeral=True
                )

            if user_xp < role_data['price']:
                return await interaction.response.send_message(
                    f"Недостаточно XP! Нужно {role_data['price']} XP, у вас {user_xp} XP.",
                    ephemeral=True
                )

            if target_role in interaction.user.roles:
                return await interaction.response.send_message(
                    f"У вас уже есть роль {target_role.mention}!",
                    ephemeral=True
                )

            confirm_embed = discord.Embed(
                title="Подтверждение покупки",
                description=f"Вы уверены, что хотите купить роль {target_role.mention} за {role_data['price']} XP?",
                color=discord.Color.blue()
            )

            confirm_view = discord.ui.View()

            confirm_button = discord.ui.Button(label="Подтвердить", style=discord.ButtonStyle.green)
            cancel_button = discord.ui.Button(label="Отмена", style=discord.ButtonStyle.red)

            async def confirm_callback(interaction: discord.Interaction):
                try:
                    if await self.update_user_xp(interaction.user.id, role_data['price']):
                        # Выдаем купленную роль
                        await interaction.user.add_roles(target_role)

                        # Обновляем уровень и роли
                        new_xp = user_xp - role_data['price']
                        new_level = calculate_level(new_xp)

                        # Обновляем уровень в базе данных
                        conn = self.get_db_connection()
                        if conn:
                            try:
                                cursor = conn.cursor()
                                cursor.execute(
                                    "UPDATE user_levels SET level = %s WHERE user_id = %s",
                                    (new_level, interaction.user.id)
                                )
                                conn.commit()
                            finally:
                                if conn.is_connected():
                                    conn.close()

                        # Обновляем роли уровня
                        await update_roles(interaction.user, new_level)

                        # Получаем название новой роли уровня
                        new_role_name = LEVELS_CONFIG.get(new_level, {}).get("role", "Неизвестная роль")

                        success_embed = discord.Embed(
                            title="Покупка успешна!",
                            description=(
                                f"Вы получили роль {target_role.mention} за {role_data['price']} XP!\n"
                                f"Ваш новый баланс: **{new_xp} XP** (Уровень {new_level})\n"
                                f"Новая роль уровня: **{new_role_name}**"
                            ),
                            color=discord.Color.green()
                        )
                        await interaction.response.edit_message(embed=success_embed, view=None)
                    else:
                        await interaction.response.edit_message(
                            content="Произошла ошибка при обновлении XP. Пожалуйста, попробуйте позже.",
                            embed=None,
                            view=None
                        )
                except Exception as e:
                    print(f"Ошибка при обработке покупки: {e}")
                    await interaction.response.edit_message(
                        content="Произошла ошибка при обработке вашего запроса.",
                        embed=None,
                        view=None
                    )

            confirm_button.callback = confirm_callback
            confirm_view.add_item(confirm_button)

            async def cancel_callback(interaction: discord.Interaction):
                await interaction.response.edit_message(
                    content="Покупка отменена.",
                    embed=None,
                    view=None
                )

            cancel_button.callback = cancel_callback
            confirm_view.add_item(cancel_button)

            await interaction.response.send_message(
                embed=confirm_embed,
                view=confirm_view,
                ephemeral=True
            )

        role_select.callback = select_callback
        view.add_item(role_select)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @classmethod
    async def setup(cls, bot):
        await bot.add_cog(cls(bot))