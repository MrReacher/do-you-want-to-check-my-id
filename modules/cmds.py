import discord
from discord.ext import commands

import re
import asyncio

# edit this right away
from modules.events import LOG_FORMAT, REGEX_FORMAT

class DiscordID(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            m = await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                member_id = int(argument, base=10)
            except ValueError:
                raise commands.BadArgument('ID not valid')
        else:
            member_id = m.id

        return member_id

class AuditLogCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        return ctx.guild and (ctx.author.guild_permissions.ban_members or
                ctx.author.guild_permissions.kick_members)

    @commands.group()
    @commands.has_permissions(manage_guild=True)
    async def settings(self, ctx):
        """Manages the server settings."""

        if ctx.invoked_subcommand is None:
            query = 'SELECT channel_id FROM settings WHERE guild_id = ?'
            async with ctx.bot.db.execute(query, (ctx.guild.id,)) as cursor:
                response = await cursor.fetchone()

            if response is not None:
                response = ctx.guild.get_channel(response[0])

            channel_format = (f'{response.mention} ({response.id})' 
                                if response else 'not set')

            content = 'Are you sure you\'re 18, I\'m getting really worried now.\n' \
                        'Be careful with these settings man!\n\n' \
                        'Logs channel: {channel}'
            await ctx.send(content.format(channel=channel_format))

    @settings.command()
    async def logs(self, ctx, channel: discord.TextChannel):
        """Sets the logs channel where the new records should be stored."""

        query = '''
            INSERT OR IGNORE INTO `settings` (guild_id, channel_id) 
            VALUES ({guild_id}, {channel_id});

            UPDATE `settings` SET channel_id = {channel_id}
            WHERE guild_id = {guild_id};
        '''
        await ctx.bot.db.executescript(
            query.format(channel_id=channel.id, guild_id=ctx.guild.id)
        )
        await ctx.bot.db.commit()

        self.bot._cache[ctx.guild.id]['channel'] = channel
        await ctx.send(':ok_hand:')

    @commands.command()
    async def last(self, ctx, entries: int=10):
        """Shows last N records from this server.

        You can list up to 20 records. Defaults to 10.
        """

        entries = min(entries, 20)

        query = '''
            SELECT case_id, action, target_id
            FROM `{guild_id}`
            ORDER BY case_id 
            DESC LIMIT ?
        '''
        cursor = await ctx.bot.db.execute(
            query.format(guild_id=ctx.guild.id), (entries,)
        )
        response = await cursor.fetchall()

        if not response:
            return await ctx.send('No database record found.')

        entries = []
        for r in response:
            entries.append(f'Case: {r[0]:>4} | Action: {r[1]:>7} | User: {r[2]}')

        last_n = '\n'.join(entries)
        await ctx.send(
            f'Last {len(entries)} records for guild {ctx.guild}.\n'
            f'```apache\n{last_n}\n```'
        )

    @commands.command()
    async def case(self, ctx, case_id: int):
        """Shows information about a case number from this server."""

        query = '''
            SELECT action, target_id, moderator_id, reason
            FROM `{guild_id}`
            WHERE case_id = ?
        '''
        cursor = await ctx.bot.db.execute(
            query.format(guild_id=ctx.guild.id), (case_id,)
        )
        response = await cursor.fetchone()

        if not response:
            return await ctx.send('No database record found.')

        user = ctx.guild.get_member(response[1]) or response[1]
        moderator = ctx.guild.get_member(response[2]) or response[2]

        if isinstance(user, discord.Member):
            user = f'{user} ({user.id})'
        if isinstance(moderator, discord.Member):
            moderator = f'{moderator} ({moderator.id})'

        entry = f'Action: {response[0]}\n' \
                f'User: {user}\n' \
                f'Moderator: {moderator}\n' \
                f'Reason: {response[3]}'
        await ctx.send(
            f'Information about Case ID {case_id}\n'
            f'```apache\n{entry}\n```'
        )

    async def reason_func(self, guild, case_id, reason, *, title=None):
        """Helper function for command below and softbans"""

        query = '''
            SELECT case_id, message_id
            FROM `{guild_id}`
            WHERE case_id = ?
        '''
        cursor = await self.bot.db.execute(
            query.format(guild_id=guild.id), (case_id,)
        )
        response = await cursor.fetchone()

        if not response:
            raise commands.BadArgument('No case ID found.')

        # idk how to do this one so I'll leave it like this
        query = 'UPDATE `{guild_id}` SET {action} = ? WHERE case_id = ?'
        action = 'action' if title else 'reason'

        cursor = await self.bot.db.execute(
            query.format(guild_id=guild.id, action=action),
            (title or reason, case_id,)
        )

        log_channel = self.bot._cache[guild.id].get('channel')
        if log_channel is not None:
            try:
                msg = await log_channel.fetch_message(response[1])
            except (discord.NotFound, discord.HTTPException):
                # we don't care about errors here
                # let the commit pass
                pass
            else:
                found = REGEX_FORMAT.search(msg.content)
                if found:
                    new_content = LOG_FORMAT.format(
                        action=title or found.group('action'),
                        case_id=found.group('case_id'),
                        target=found.group('target'),
                        moderator=found.group('moderator'),
                        reason=reason
                    )

                    await msg.edit(content=new_content)

        await self.bot.db.commit()

    @commands.command()
    async def reason(self, ctx, case_id: int, *, reason: str):
        """Edits a case number from this server by providing a new reason."""

        await self.reason_func(ctx.guild, case_id, reason)
        await ctx.send(':ok_hand:')

    @commands.command()
    async def user(self, ctx, member: DiscordID, entries: int=5):
        """Shows last N records for a user from this server.

        You can list up to 20 records. Defaults to 5.
        """

        query = '''
            SELECT case_id, action, reason, (
                SELECT COUNT(*)
                FROM `{guild_id}`
                WHERE target_id = ?1
            ) AS records
            FROM `{guild_id}`
            WHERE target_id = ?1
            ORDER BY case_id
            DESC LIMIT ?2
        '''
        cursor = await ctx.bot.db.execute(
            query.format(guild_id=ctx.guild.id), (member, entries,)
        )
        response = await cursor.fetchall()

        if not response:
            return await ctx.send('No database record found.')

        entries = []
        for r in response:
            entries.append(f'Case: {r[0]:>4} | Action: {r[1]:>7} | Reason: {r[2]}')
            records = r[3]

        last_n = '\n'.join(entries)
        await ctx.send(
            f'Last {len(entries)} records for user {member}, {records} in total.\n'
            f'```apache\n{last_n}\n```\n'
            'Find information about a case by running `case <case_id>`.'
        )

def setup(bot):
    bot.add_cog(AuditLogCommands(bot))
