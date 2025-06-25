import discord
from discord.ext import commands
from discord import app_commands
import mysql.connector
from mysql.connector import Error
from data import mysqlconf


class BoostL(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.MYSQL_CONFIG = mysqlconf

    def get_db_connection(self):
        """Создает подключение к базе данных"""
        try:
            return mysql.connector.connect(**self.MYSQL_CONFIG)
        except Error as e:
            print(f"Ошибка подключения к MySQL: {e}")
            return None

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Обработчик события буста сервера"""
        if before.premium_since is None and after.premium_since is not None:
            await self.process_boost(after)

    async def process_boost(self, member: discord.Member):
        """Обрабатывает буст сервера"""
        conn = None
        try:
            conn = self.get_db_connection()
            if not conn:
                return

            cursor = conn.cursor()

            # Обновляем boost +1
            cursor.execute("""
                INSERT INTO user_levels (user_id, boost, xp, level)
                VALUES (%s, 1, 0, 1)
                ON DUPLICATE KEY UPDATE 
                    boost = boost + 1
            """, (member.id,))

            conn.commit()

            # Получаем обновленное значение boost
            cursor.execute("SELECT boost FROM user_levels WHERE user_id = %s", (member.id,))

        except Exception as e:
            print(f"Ошибка при обработке буста: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn and conn.is_connected():
                conn.close()


    @classmethod
    async def setup(cls, bot):
        cog = cls(bot)
        await bot.add_cog(cog)