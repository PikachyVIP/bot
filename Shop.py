import discord
from discord.ext import commands
from discord import app_commands
import mysql.connector
from mysql.connector import Error
from data import mysqlconf


class EventCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.MYSQL_CONFIG = mysqlconf  # Используем конфиг напрямую из mysqlconf

        # Словарь с доступными ролями и их стоимостью
        self.shop_roles = {
            "✔": {"price": 1000, "description": "testrole"}
        }

    def get_db_connection(self):
        """Создает и возвращает подключение к MySQL"""
        try:
            connection = mysql.connector.connect(**self.MYSQL_CONFIG)
            return connection
        except Error as e:
            print(f"Ошибка подключения к MySQL: {e}")
            return None

    async def get_user_xp(self, user_id):
        """Получает количество XP пользователя"""
        connection = self.get_db_connection()
        if not connection:
            return 0

        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT xp FROM user_levels WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            return result['xp'] if result else 0
        except Error as e:
            print(f"Ошибка при получении XP: {e}")
            return 0
        finally:
            if connection.is_connected():
                connection.close()

    async def update_user_xp(self, user_id, amount):
        """Обновляет количество XP пользователя"""
        connection = self.get_db_connection()
        if not connection:
            return False

        try:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE user_levels SET xp = xp - %s WHERE user_id = %s",
                (amount, user_id)
            )
            connection.commit()
            return True
        except Error as e:
            print(f"Ошибка при обновлении XP: {e}")
            connection.rollback()
            return False
        finally:
            if connection.is_connected():
                connection.close()

    @app_commands.command(name="shop", description="Магазин ролей за XP")
    async def shop_command(self, interaction: discord.Interaction):
        """Команда магазина ролей"""
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.delete_original_response()
        except:
            pass

        user_xp = await self.get_user_xp(interaction.user.id)

        embed = discord.Embed(
            title="🏪 Магазин ролей",
            description=f"Ваш баланс: **{user_xp} XP**\n\nВыберите роль для покупки:",
            color=discord.Color.gold()
        )

        for role_name, data in self.shop_roles.items():
            embed.add_field(
                name=f"{role_name} - {data['price']} XP",
                value=data['description'],
                inline=False
            )

        view = discord.ui.View()
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
                if await self.update_user_xp(interaction.user.id, role_data['price']):
                    await interaction.user.add_roles(target_role)
                    success_embed = discord.Embed(
                        title="Покупка успешна!",
                        description=f"Вы получили роль {target_role.mention} за {role_data['price']} XP!",
                        color=discord.Color.green()
                    )
                    await interaction.response.edit_message(embed=success_embed, view=None)
                else:
                    await interaction.response.send_message(
                        "Произошла ошибка при обновлении XP. Пожалуйста, попробуйте позже.",
                        ephemeral=True
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


async def setup(bot):
    await bot.add_cog(EventCommands(bot))