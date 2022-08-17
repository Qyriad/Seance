""" This module handles a Discord server that has been marked as the proxy for DMs
that the bot receives and for the bot to send. """

import re
import sys
import time
import sqlite3
from datetime import datetime, timedelta

import discord
import discord.abc
import discord.utils
import discord.errors
import discord.app_commands
from discord import User, Message, TextChannel, DMChannel, ChannelType, Guild
from discord.abc import Messageable
from discord.errors import HTTPException

from discord import Interaction
from discord.app_commands import CommandTree

from ..errors import SeanceError


class SeanceDMManagerError(SeanceError):
    """ Base class for Séance DM Manager specific errors. """

class SeanceInvalidDMProxyChannelError(SeanceError):
    """
    Indicates that a DM server channel looks like a DM-proxy channel, (meaning it's in the category for those),
    but doesn't have the topic set to a valid user ID.
    """


async def create_exception_and_send_message(error_type: type, text: str, channel: Messageable):
    """ Helper to create an error of a specified type, and also report an error message to the user in Discord.

    Note: so the stack traces look right, this function *does not raise the exception*; it only creates it.
    Callers should use this function like `raise await create_exception_and_send_message()`.
    """

    await channel.send(f"❌ {text}")
    return error_type(text)


def get_nested(d: dict, *keys):
    """ Gets nested dict values, returning None if any in the chain don't exist or are None. """

    if len(keys) == 1:
        return d[keys[0]]

    else:
        nested = d.get(keys[0], None)
        if nested is not None:
            return get_nested(nested, *keys[1:])
        else:
            return None


# With or without a nickname.
DISCORD_USER_MENTION_PATTERN = re.compile(r'<@!?(?P<id>\d+)>')


def datetime_to_unixtime(dt: datetime) -> int:
    return int(time.mktime(dt.timetuple()))


sqlite3.register_adapter(datetime, datetime_to_unixtime)

class DiscordDMGuildManager:

    # XXX SWITCH PROXY UNTAGGED BACK TO FALSE
    def __init__(self, client, guild: Guild, *, proxy_untagged=False, pattern):
        """
        `proxy_untagged` specifies that messages which do not meet the proxy pattern should still be proxied from
        the server channel to the DM channel.
        """

        self.client = client
        self.command_tree = CommandTree(self.client)
        self.guild = guild
        self.proxy_untagged = proxy_untagged

        # If we weren't given an already compiled re.Pattern, compile it now.
        if not isinstance(pattern, re.Pattern):
            self.pattern = re.compile(pattern, re.DOTALL)
        else:
            self.pattern = pattern

        self._db_con = sqlite3.connect('seance_dm.db')
        cur = self._db_con.cursor()
        cur.execute(
            'CREATE TABLE IF NOT EXISTS "msg_id_mappings" ('
                '"entry_id" INTEGER PRIMARY KEY, '
                '"dm_id" BLOB CONSTRAINT NOT_NULL, '
                '"server_id" BLOB CONSTRAINT NOT_NULL, '
                '"insertion_date" BLOB CONSTRAINT NOT_NULL'
            ');'
        )
        # cur.execute(
            # 'CREATE TABLE IF NOT EXISTS "server_to_dm" ('
                # '"server_id" BLOB CONSTRAINT PRIMARY_KEY,'
                # '"dm_id" BLOB CONSTRAINT NOT_NULL,'
                # '"date" INTEGER CONSTRAINT NOT_NULL'
            # ');'
        # )
        self._db_con.commit()

        self.admin_category = None
        self.friends_list_channel = None
        self.dm_category = None

        # CREATE TABLE IF NOT EXISTS "dm_to_server" (
        # "dm_id" BLOB CONSTRAINT PRIMARY_KEY,
        # "server_id" BLOB CONSTRAINT NOT_NULL,
        # "date" INTEGER CONSTRAINT NOT_NULL,
        # );
        # .mode column
        # .headers on


    def _cache_message_id_mappings(self, *, dm_msg_id, server_msg_id):

        cur = self._db_con.cursor()

        now = datetime.now()

        cur.execute(
            '''INSERT INTO "msg_id_mappings" ("dm_id", "server_id", "insertion_date") '''
                '''VALUES (?, ?, ?);''',
            (dm_msg_id, server_msg_id, now)
        )

        # cur.execute(
            # 'INSERT INTO "server_to_dm" ("server_id", "dm_id", "date") VALUES (?, ?, ?);',
            # (server_msg_id, dm_msg_id, now)
        # )

        self._db_con.commit()


        # With that done, if have over 200 messages,
        # then delete the least recently stored 100 of them.
        cur = self._db_con.cursor()

        dm_to_server_count = cur.execute(
            'SELECT COUNT("dm_id") FROM "msg_id_mappings";'
        ).fetchall()[0][0]
        if dm_to_server_count > 200:
            # Delete the cache for anything older than 24 hours ago.
            yesterday = now - timedelta(days=1)
            yesterday_unix = datetime_to_unixtime(yesterday)
            cur.execute(
                'DELETE FROM "msg_id_mappings" WHERE "entry_id" NOT IN ('
                    'SELECT "entry_id" FROM "msg_id_mappings" WHERE "insertion_date" <= ? ORDER BY "insertion_date"'
                ');',
                (yesterday_unix,)
            )


        self._db_con.commit()


    def _retrieve_message_id_mapping_for(self, *, dm_msg_id=None, server_msg_id=None):

        if dm_msg_id is not None and server_msg_id is not None:
            raise ValueError("Both dm_msg_id and server_msg_id specified")

        cur = self._db_con.cursor()

        try:

            if dm_msg_id is not None:

                server_msg_id = cur.execute(
                    'SELECT "server_id" FROM "msg_id_mappings" WHERE "dm_id" = ?',
                    dm_msg_id,
                ).fetchall()[0][0]

                return server_msg_id

            if server_msg_id is not None:

                dm_msg_id = cur.execute(
                    'SELECT "dm_id" FROM "msg_id_mappings" WHERE "server_id" = ?',
                    server_msg_id,
                ).fetchall()[0][0]

                return dm_msg_id


        except IndexError:
            return None


    @discord.app_commands.describe(account='Snowflake ID or @mention of account to DM')
    async def handle_newdm_command(self, interaction: Interaction, account: str):

        # Ensure only one user was specified and nothing else, to avoid confusion.
        users = get_nested(interaction.data, 'resolved', 'users') # type: ignore
        if users is not None:
            if len(users) > 1:
                await interaction.response.send_message(
                    "❌ `account` option must specify only one user",
                    ephemeral=True
                )
                return

            matches = DISCORD_USER_MENTION_PATTERN.match(account)
            start, end = matches.span()
            if end - start != len(account):
                await interaction.response.send_message("❌ `account` option must be a user ID or a mention and nothing else", ephemeral=True)
                return

            user = User(state=self.client._connection, data=list(users.values())[0])

        else:
            # Otherwise, a user was not mentioned. Try to parse the content as an ID instead.
            try:
                user_id = int(account)
                user = await self.client.fetch_user(user_id)
            except (ValueError, discord.errors.NotFound):
                await interaction.response.send_message("❌ `account` option must be a valid user ID or a mention", ephemeral=True)
                return


        channel = await self.ensure_channel_for(user)
        await interaction.response.send_message("<#{}>".format(channel.id), ephemeral=True)


    def register_slash_commands(self):
        """ Registers the slash commands that will be used for DM management. """

        self.handle_newdm_command = self.command_tree.command( # type: ignore
            name="newdm",
            description="Opens a new DM",
            guild=self.guild,
        )(self.handle_newdm_command)


    async def setup_channels(self):
        """ Ensures that all the base necessary channels are present, creating any missing. """

        ### Category: Administrivia — $seance_account_name
        # Channel: #friends-list
        # Channel: #general

        ### Category: DMs — $seance_account_name
        # Channels: #username-descriminator [channel topic stores user ID]

        # First, re-populate the guild so it has all of attributes we need.
        self.guild = self.client.get_guild(self.guild.id)

        admin_category_name = '{} — Administrivia'.format(self.client.user.display_name)
        admin_category = discord.utils.find(lambda cat : cat.name == admin_category_name, self.guild.categories)
        if admin_category is None:
            admin_category = await self.guild.create_category(admin_category_name)

        self.admin_category = admin_category

        friends_list_channel = discord.utils.find(lambda ch : ch.name == 'friends-list', admin_category.channels)
        if friends_list_channel is None:
            friends_list_channel = await admin_category.create_text_channel('friends-list')

        self.friends_list_channel = friends_list_channel

        dm_category_name = '{} — DMs'.format(self.client.user.display_name)
        dm_category = discord.utils.find(lambda cat : cat.name == dm_category_name, self.guild.categories)
        if dm_category is None:
            dm_category = await self.guild.create_category(dm_category_name)

        self.dm_category = dm_category


    async def setup(self):
        await self.setup_channels()
        self.register_slash_commands()
        await self.command_tree.sync(guild=self.guild)


    async def ensure_channel_for(self, user: User) -> TextChannel:
        """ Retrives the DM-proxy channel for the specified user, creating if necessary. """

        channel = discord.utils.find(lambda ch : ch.topic == str(user.id), self.dm_category.channels)
        if channel is None:
            name = '{}-{}'.format(user.name.lower(), user.discriminator.lower())
            channel = await self.dm_category.create_text_channel(name, topic=str(user.id))

        return channel


    async def ensure_dm_for(self, channel: TextChannel) -> User:
        """ Retrieves the DM channel for the specified DM-proxy channel, creating if necessary.

            Note: unlike `ensure_channel_for`, this can return None if the specified channel is
            not a DM-proxy channel.
            However, if the specified *looks* like a DM-proxy channel (it's under the DMs category),
            but a user for the channel could not be found (such as if the topic is not set to a valid user ID),
            *then* and only then will this bot send an error in that channel.
        """

        try:
            target_user_id = int(channel.topic)
        except (ValueError, TypeError) as e:
            if channel.category.id == self.dm_category.id:
                raise await create_exception_and_send_message(SeanceInvalidDMProxyChannelError,
                    "This appears to be a DM-proxy channel, but the topic is not set to a valid user ID!",
                    channel,
                ) from e
            else:
                return None

        user = self.client.get_user(target_user_id)
        if user.dm_channel is not None:
            return user.dm_channel
        else:
            return await user.create_dm()


    async def handle_typing(self, channel, user, when):
        """ This is called by SeanceClient when it receives a typing notification over the gateway. """

        # If it's in a DM and it's not from this bot, handle that.
        # Note that this code is mostly here for completeness and Just In Case™️.
        # In our testing, bots don't seem to receive typing notifications in DMs.
        # If for whatever reason we do, though, Séance will still proxy it.
        if channel.type == ChannelType.private and user.id != self.client.user.id:

            channel = await self.ensure_channel_for(user)
            await channel.trigger_typing()
            return

        # Otherwise, if it's in the DM server, and it's from the reference user,
        # then proxy that to the DM with the user.
        if user.id == self.client.ref_user_id and channel.guild.id == self.guild.id:

            dm = await self.ensure_dm_for(channel)
            if dm is not None:
                await dm.typing()

            return


    async def handle_dm_to_server(self, message: Message):
        """ Proxies from the DM channel to the server channel via a webhook. """

        target_channel = await self.ensure_channel_for(message.author)

        webhook = discord.utils.find(lambda hook : hook.user == self.client.user, await target_channel.webhooks())
        if not webhook:
            webhook = await target_channel.create_webhook(name=self.client.user.display_name)

        # TODO: Handle replies.
        files = [await att.to_file(spoiler=att.is_spoiler()) for att in message.attachments]
        server_message = await webhook.send(message.content, wait=True,
            username=message.author.name, avatar_url=message.author.display_avatar.url, files=files,
        )

        self._cache_message_id_mappings(dm_msg_id=message.id, server_msg_id=server_message.id)


    async def handle_server_to_dm_edit(self, after: Message):

        # TODO: implement this.
        pass


    async def handle_server_to_dm(self, message: Message):

        target_dm = await self.ensure_dm_for(message.channel)
        # ensure_dm_for() returns None if the specified channel is not a proxy channel.
        if target_dm is None:
            return


        if self.proxy_untagged:

            # If it *does* match the pattern, then we have to remove the proxy tag.
            # Otherwise, proxy as is.
            if matches := self.pattern.match(message.content):
                content_to_proxy = matches.groupdict()['content']
            else:
                content_to_proxy = message.content

        else:

            if matches := self.pattern.match(message.content):
                content_to_proxy = matches.groupdict()['content']
            else:
                # We're only supposed to match messages in the proxy format, but this message does not match.
                # Nothing to do, so just return.
                return


        # TODO: Handle replies.
        files = [await att.to_file(spoiler=att.is_spoiler()) for att in message.attachments]
        server_message = await message.channel.send(content_to_proxy, files=files)
        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete original message: {e}.", file=sys.stderr)

        dm_message = await target_dm.send(content_to_proxy, files=files)

        self._cache_message_id_mappings(server_msg_id=server_message.id, dm_msg_id=dm_message.id)


    async def handle_dm_to_server_edit(self, after: Message):

        # TODO: implement
        pass

