import discord
from discord.ext import commands

import re
import asyncio
from aiosqlite import OperationalError

DEBUG = True

LOG_FORMAT = (
    '**ACTION**: {action} | **CASE**: {case_id} ```apache\n'
    'User: {target}\n'
    'Moderator: {moderator}\n'
    'Reason: {reason}\n```'
)

REGEX_FORMAT = re.compile(
    r'\*\*ACTION\*\*\:\s(?P<action>.+?)\s\|'
    r'\s\*\*CASE\*\*\:\s(?P<case_id>\d+)\s\`\`\`apache\n'
    r'User\:\s(?P<target>.+?)\n'
    r'Moderator\:\s(?P<moderator>.+?)\n'
    r'Reason\:\s(?P<reason>.+?)\n\`\`\`'
)

class AuditLogChecker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot._cache = dict()
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock(loop=self.bot.loop)

        self.bot.loop.create_task(self.populate_cache())

    async def populate_cache(self, *, guild_ids=None):
        await self.bot.wait_until_ready()

        if DEBUG:
            print('inside populate_cache', guild_ids)

        guilds = [self.bot.get_guild(guild_id) for guild_id in guild_ids] \
                    if guild_ids else self.bot.guilds
        for guild in guilds:
            if not guild.me.guild_permissions.view_audit_log:
                continue

            query = '''
                CREATE TABLE IF NOT EXISTS `{guild_id}` (
                    case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT,
                    target_id INTEGER,
                    moderator_id INTEGER,
                    reason TEXT,
                    message_id INTEGER
                )
            '''
            await self.bot.db.execute(query.format(guild_id=guild.id))
            await self.bot.db.commit()

            self.bot._cache[guild.id] = dict()
            print('cached guild name', guild.name)

            query = 'SELECT channel_id FROM settings WHERE guild_id = ?'
            async with self.bot.db.execute(query, (guild.id,)) as cursor:
                response = await cursor.fetchone()

            if response:
                self.bot._cache[guild.id]['channel'] = guild.get_channel(response[0])

            actions = [
                discord.AuditLogAction.ban,
                discord.AuditLogAction.unban,
                discord.AuditLogAction.kick
            ]
            for action in actions:
                entries = await guild.audit_logs(action=action, limit=5).flatten()
                self.bot._cache[guild.id][action] = entries
                print('saved last 5 entries for', action)

        self._ready.set()

    async def log_formatter(self, guild, entry):
        if DEBUG:
            print('inside log_formatter', guild, entry)

        log_channel = self.bot._cache[guild.id].get('channel')

        # change this variable please :pray:
        action = str(entry.action).split('.')[1].upper()
        target, user, reason = entry.target, entry.user, entry.reason or 'N/A'

        if entry.action == discord.AuditLogAction.unban:
            cached_bans = self.bot._cache[guild.id][discord.AuditLogAction.ban]
            if any(x for x in cached_bans if x.target == entry.target
                                            and x.user == entry.user
                                            and x.reason == entry.reason):
                query = '''
                    SELECT case_id
                    FROM `{guild_id}`
                    WHERE target_id = ? AND moderator_id = ? AND reason = ?
                    ORDER BY case_id DESC
                '''
                cursor = await self.bot.db.execute(
                    query.format(guild_id=guild.id), (target.id, user.id, reason,)
                )
                response = await cursor.fetchone()

                # just in case this fails
                if response:
                    cog = self.bot.get_cog('AuditLogCommands')
                    func = cog.reason_func(guild, response[0], reason, title='SOFTBAN')
                    return await func

        if log_channel:
            msg = await log_channel.send(LOG_FORMAT.format(
                action=action,
                case_id=0,
                target=f'{target} ({target.id})',
                moderator=f'{user} ({user.id})',
                reason=reason
            ))

        query = '''
            INSERT INTO `{guild_id}` (action, target_id, moderator_id, reason, message_id)
            VALUES (?, ?, ?, ?, ?)
        '''
        try:
            cursor = await self.bot.db.execute(
                query.format(guild_id=guild.id),
                (action, target.id, user.id, reason, msg.id if log_channel else None,)
            )
        except OperationalError:
            # re-populate cache for this guild
            await self.populate_cache(guild_ids=[guild.id])
            # recursive after the guild was cached
            return await self.log_formatter(guild, entry)
        else:
            await self.bot.db.commit()

        # update case_id with the current id
        _id = cursor.lastrowid
        if log_channel:
            found = REGEX_FORMAT.search(msg.content)
            if found:
                new_content = LOG_FORMAT.format(
                    action=found.group('action'),
                    case_id=_id,
                    target=found.group('target'),
                    moderator=found.group('moderator'),
                    reason=found.group('reason')
                )

                # let's hope this `msg` variable won't fail
                # it shouldn't
                await msg.edit(content=new_content)

    async def log_checker(self, guild, user, *, action):
        if DEBUG:
            print('inside log_checker', guild, user, action)

        if not guild.me.guild_permissions.view_audit_log:
            return

        if not self._ready.is_set():
            self._ready.wait()

        for tries in range(5):
            entries = await guild.audit_logs(action=action, limit=5).flatten()

            cached_entries = self.bot._cache[guild.id][action]
            diff = []
            for entry in entries:
                if not any(entry.id == x.id for x in cached_entries):
                    diff.append(entry)

            if not diff:
                await asyncio.sleep(3)
                print(f'#{tries} no difference, retrying')
                continue

            for d in diff:
                await self.log_formatter(guild, d)

            self.bot._cache[guild.id][action] = entries
            break

        else:
            print('stopping after 5 retries')

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        if DEBUG:
            print('inside on_guild_join', guild)

        await self.populate_cache(guild_ids=[guild.id])

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        if DEBUG:
            print('inside on_guild_remove', guild)

        try:
            del self.bot._cache[guild.id]
        except KeyError:
            pass

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        async with self._lock:
            await self.log_checker(guild, user, action=discord.AuditLogAction.ban)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        async with self._lock:
            await self.log_checker(guild, user, action=discord.AuditLogAction.unban)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        async with self._lock:
            await self.log_checker(member.guild, member, action=discord.AuditLogAction.kick)

def setup(bot):
    bot.add_cog(AuditLogChecker(bot))
