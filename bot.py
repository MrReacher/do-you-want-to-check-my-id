import traceback
import discord
import sys

from discord.ext import commands

extensions = (
    'modules.events',
    'modules.cmds',
)

class Main(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.typing = False
        super().__init__(command_prefix='`', pm_help=None, intents=intents)

        for extension in extensions:
            try:
                self.load_extension(extension)
                print(f'loaded extension {extension} [!]')
            except Exception as e:
                print(f'failed to load {extension}:', file=sys.stderr)
                traceback.print_exc()

    async def on_command_error(self, ctx, error):
        if hasattr(ctx.command, 'on_error'):
            return

        if isinstance(error, commands.CommandNotFound):
            return

        error = getattr(error, 'original', error)
        if isinstance(error, commands.BadArgument):
            return await ctx.send(f'Invalid input: {error}')

        print(f'Ignoring exception in {ctx.command.qualified_name}:', file=sys.stderr)
        traceback.print_tb(error.__traceback__)
        print(f'{error.__class__.__name__}: {error}', file=sys.stderr)

    async def on_connect(self):
        print('connected to discord [..]')

    async def on_ready(self):
        await self.change_presence(status=discord.Status.dnd)
        print(len(self.guilds))
        print(f'ready to serve with username {self.user} and ID {self.user.id} [!]')

    async def on_resumed(self):
        print('resumed [..]')

    async def on_message(self, message):
        if message.author.bot:
            return

        await self.process_commands(message)

    async def close(self):
        await super().close()

    def run(self):
        super().run(token, reconnect=True)