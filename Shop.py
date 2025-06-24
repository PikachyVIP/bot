import discord
from discord.ext import commands
from discord import app_commands
import mysql.connector
from data import mysqlconf


class EventCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_connection = mysql.connector.connect(
            host=mysqlconf.host,
            user=mysqlconf.user,
            password=mysqlconf.password,
            database=mysqlconf.database
        )

        # Словарь с доступными ролями и их стоимостью (можно расширять)
        self.shop_roles = {
            "✔": {"price": 1000, "description": "TESTROLE"}
        }

    async def get_user_xp(self, user_id):
        cursor = self.db_connection.cursor(dictionary=True)
        cursor.execute("SELECT xp FROM user_levels WHERE user_id = %s", (user_id,))
        result = cursor.fetchone()
        return result['xp'] if result else 0

    async def update_user_xp(self, user_id, amount):
        cursor = self.db_connection.cursor()
        cursor.execute(
            "UPDATE user_levels SET xp = xp - %s WHERE user_id = %s",
            (amount, user_id)
        )
        self.db_connection.commit()

    @app_commands.command(name="shop", description="Магазин ролей за XP")
    async def shop_command(self, interaction: discord.Interaction):
        # Удаляем оригинальный ответ (сообщение с командой)
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.delete_original_response()
        except:
            pass

        # Получаем текущий XP пользователя
        user_xp = await self.get_user_xp(interaction.user.id)

        # Создаем Embed с информацией о магазине
        embed = discord.Embed(
            title="🏪 Магазин ролей",
            description=f"Ваш баланс: **{user_xp} XP**\n\nВыберите роль для покупки:",
            color=discord.Color.gold()
        )

        # Добавляем информацию о каждой роли в Embed
        for role_name, data in self.shop_roles.items():
            embed.add_field(
                name=f"{role_name} - {data['price']} XP",
                value=data['description'],
                inline=False
            )

        # Создаем View с селектором ролей
        view = discord.ui.View()

        # Выпадающий список с ролями
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

            # Проверяем наличие роли на сервере
            target_role = discord.utils.get(interaction.guild.roles, name=selected_role_name)
            if not target_role:
                return await interaction.response.send_message(
                    f"Роль {selected_role_name} не найдена на сервере!",
                    ephemeral=True
                )

            # Проверяем баланс
            if user_xp < role_data['price']:
                return await interaction.response.send_message(
                    f"Недостаточно XP! Нужно {role_data['price']} XP, у вас {user_xp} XP.",
                    ephemeral=True
                )

            # Проверяем, есть ли уже эта роль
            if target_role in interaction.user.roles:
                return await interaction.response.send_message(
                    f"У вас уже есть роль {target_role.mention}!",
                    ephemeral=True
                )

            # Подтверждение покупки
            confirm_embed = discord.Embed(
                title="Подтверждение покупки",
                description=f"Вы уверены, что хотите купить роль {target_role.mention} за {role_data['price']} XP?",
                color=discord.Color.blue()
            )

            confirm_view = discord.ui.View()

            # Кнопка подтверждения
            confirm_button = discord.ui.Button(
                label="Подтвердить",
                style=discord.ButtonStyle.green
            )

            async def confirm_callback(interaction: discord.Interaction):
                # Списываем XP
                await self.update_user_xp(interaction.user.id, role_data['price'])

                # Выдаем роль
                await interaction.user.add_roles(target_role)

                # Отправляем подтверждение
                success_embed = discord.Embed(
                    title="Покупка успешна!",
                    description=f"Вы получили роль {target_role.mention} за {role_data['price']} XP!",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(embed=success_embed, view=None)

            confirm_button.callback = confirm_callback
            confirm_view.add_item(confirm_button)

            # Кнопка отмены
            cancel_button = discord.ui.Button(
                label="Отмена",
                style=discord.ButtonStyle.red
            )

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

        # Отправляем основное меню магазина
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    def cog_unload(self):
        self.db_connection.close()


async def setup(bot):
    await bot.add_cog(EventCommands(bot))