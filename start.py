import discord
import logging
import asyncio
import aiosqlite
import contextlib

from bot import Main

# source: rdanny because I'm lazy to do my own launcher
@contextlib.contextmanager
def setlogging():
    try:
        logging.getLogger('discord').setLevel(logging.INFO)
        logging.getLogger('discord.http').setLevel(logging.INFO)

        log = logging.getLogger()
        handler = logging.FileHandler(filename='mod.log', encoding='utf-8', mode='w')
        date_fmt = "%d-%m-%Y %H:%M:%S"
        fmt = logging.Formatter('[{asctime}] [{levelname:<7}] {name}: {message}', date_fmt, style='{')
        handler.setFormatter(fmt)
        log.addHandler(handler)

        yield
    finally:
        handlers = log.handlers[:]
        for handler in handlers:
            handler.close()
            log.removeHandler(handler)

def run_bot():
    loop = asyncio.get_event_loop()
    log = logging.getLogger()

    try:
        db = loop.run_until_complete(aiosqlite.connect('mod.db'))
    except Exception as e:
        print(e)
        return

    bot = Main()
    bot.db = db
    bot.run()

if __name__ == '__main__':
    run_bot()