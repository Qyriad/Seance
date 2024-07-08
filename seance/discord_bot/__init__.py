""" The Discord bot version of Seance. """

import os
import re
import sys
from io import StringIO
from typing import Union, Optional

import discord
from discord import Message, Member, PartialEmoji, Status, ChannelType
from discord.raw_models import RawReactionActionEvent
from discord import Emoji
from discord.activity import Activity
from discord.enums import ActivityType
from discord.errors import HTTPException
from discord.message import PartialMessage

import PythonSed
from PythonSed import Sed

from emoji import is_emoji

try:
    import sdnotify
except ImportError:
    pass


from ..config import ConfigOption, ConfigHandler
from .dm_mode import DiscordDMGuildManager


# A pattern that matches a link to a Discord message, and captures the channel ID and message ID.
DISCORD_MESSAGE_URL_PATTERN = re.compile(r'https://(?:\w+.)?discord(?:app)?.com/channels/\d+/(\d+)/(\d+)')

# A pattern for matching Discord activities (https://discord.com/developers/docs/topics/gateway#activity-object).
DISCORD_STATUS_PATTERN = re.compile(r'(?P<type>playing|streaming|listening to|watching|competing in)?\s*(?P<name>.+)', re.IGNORECASE | re.DOTALL)

# A pattern for matching the reaction add (and remove) shortcuts in the standard client.
DISCORD_REACTION_SHORTCUT_PATTERN = re.compile(r'(?P<action>[+-])\<a?:\w{2,}:(?P<id>\d+)\>')


class KeepCurrentSentinel:
    """ A sentinal type used just for SeanceClient._set_presence(). """
keep_current = KeepCurrentSentinel()


def running_in_systemd() -> bool:
    return 'INVOCATION_ID' in os.environ


class SeanceClient(discord.Client):

    def __init__(self, ref_user_id, pattern, command_prefix, *args, dm_guild_id=None, dm_manager_options=None,
        sdnotify=False, default_status=False, default_presence=False, forward_pings=None, proxied_emoji=set(),
        valid_reproxy_targets=set(), **kwargs
    ):

        self.ref_user_id = ref_user_id

        # If we weren't given an already compiled re.Pattern, compile it now.
        if not isinstance(pattern, re.Pattern):
            self.pattern = re.compile(pattern, re.DOTALL)
        else:
            self.pattern = pattern

        self.command_prefix = command_prefix
        self.dm_guild_id = dm_guild_id
        self.sdnotify = sdnotify
        self.default_status = default_status
        self.default_presence = default_presence
        self.forward_pings = forward_pings
        self.proxied_emoji = proxied_emoji
        self.valid_reproxy_targets = valid_reproxy_targets

        # We always support reproxying our reference user.
        self.valid_reproxy_targets.add(self.ref_user_id)

        super().__init__(*args, enable_debug_events=True, **kwargs)

        # self.ref_user() will populate this when it's accessed.
        self._ref_user = None

        # Store any status overrides present.
        self._status_override = None
        self._cached_status = Status.online

        # Cache our current presence, as we seem to have trouble fetching it from Discord later on.
        self._current_activity = None
        self._current_status = Status.online

        self.command_handlers = {
            '!s/': self.handle_substitute_command,
            '!edit': self.handle_edit_command,
            '!delete': self.handle_delete_command,
            '!status': self.handle_status_command,
            '!presence': self.handle_presence_command,
            '!nick': self.handle_nickname_command,
            '!reproxy': self.handle_reproxy_command,
        }

        self.shortcut_handlers = {
            self._matches_simple_react: self.handle_simple_reaction,
            self._matches_custom_react: self.handle_custom_reaction,
        }


        self.dm_guild_manager = None
        self.dm_manager_options = dm_manager_options if dm_manager_options is not None else {}
        self.slash = None


    def _matches_command(self, content, command):
        return content.startswith(command) or content.startswith(f"{self.command_prefix}{command}")


    def _matches_simple_react(self, content):

        if len(content) > 1:
            return content[0] in "+-" and is_emoji(content[1:])
        else:
            return False


    def _matches_custom_react(self, content):
        if DISCORD_REACTION_SHORTCUT_PATTERN.fullmatch(content):
            return True
        else:
            return False


    async def _set_presence(self, *, activity=keep_current, status=keep_current):
        """ Allows setting activity and status separately without messing with each other. """

        new_activity = self._current_activity if activity == keep_current else activity
        new_status = self._current_status if status == keep_current else status

        await self.change_presence(activity=new_activity, status=new_status)

        if activity != keep_current:
            self._current_activity = activity

        if status != keep_current:
            self._current_status = status


    @staticmethod
    async def _refetch_message(message: Union[Message, PartialMessage]):
        """ For some reason, message.content doesn't always seem to be populated properly, so sometimes we have
        to re-fetch the message.  """

        return await message.channel.fetch_message(message.id)

    def ref_user(self) -> discord.User:
        """ discord.User object corresponding to ref_user_id; populated on first use. """

        if self._ref_user is not None:
            return self._ref_user

        self._ref_user = self.get_user(self.ref_user_id)
        return self._ref_user


    async def _get_shortcut_target(self, message: Message):
        """ Pulls out the referenced message, or the first non-invoking message."""
        # If the command replied to a message, then use that to get the message to edit.
        if message.reference is not None:
            target = message.reference.resolved
            # Get full message.
            target = await message.channel.fetch_message(target.id)

        # Otherwise, assume the most recent (non-invoking) message.
        else:
            prev_messages = message.channel.history(limit=2)
            target = None
            async for msg in prev_messages:
                if msg.id != message.id:
                    target = msg
                    break

        return target


    async def _get_target_message_and_args(self, message: Message, command_terminator=' ', use_history=True):
        """ Parse out a target message and remaining arguments from a message.

        This is useful for commands like !edit that take a message somehow, but messages can be passed in 3 forms:
            1. A reply, in which case the message is not specified anywhere in the content, and so the rest of the
                arguments are the entire content of the message (besides the command itself).
            2. An ID, in which case the channel is implied to be the same as the command, and the rest of the
                arguments are everything after "word 1".
            3. A link, in which case the channel and message ID are both parsed from that command word, and the
                rest of the arguments are everything after "word 1" again.
            4. If all else fails we will search the last five messages for the most recent proxied message. This is controlled by the `use_history` parameter and defaults to `True`.

            This method is a disaster.
        """

        # If the command replied to a message, then that's the target.
        if message.reference is not None:
            target = message.reference.resolved
            # And the rest of the arguments to the command are the message content after the command word itself.
            args = message.content[(message.content.find(command_terminator) + 1):]

            return target, args

        # Otherwise, let's see if we were passed a message ID or link.
        else:

            # Get the argument that we'll try to parse as a message ID or link.
            start = message.content.find(command_terminator) + 1
            end = message.content.find(' ', start)
            arg = message.content[start:end]

            try:
                msg_id = int(arg)
                # If that worked, then we were probably just passed a message ID.
                # If that's the case, then assume that means a message in the same channel as the command.
                channel = message.channel

                # And fetch the full message.
                target = await self._refetch_message(channel.get_partial_message(msg_id))

                return target, message.content[(end + 1):]

            except (ValueError, HTTPException):
                # If that didn't work, then we were probably passed a link.
                matches = DISCORD_MESSAGE_URL_PATTERN.match(arg)

                try:
                    channel_id = matches.group(1)
                    msg_id = matches.group(2)

                    # Try to fetch the message with that channel and message ID.
                    channel = await self.fetch_channel(channel_id)
                    target = await self._refetch_message(channel.get_partial_message(msg_id))

                    return target, message.content[(end + 1):]

                except (AttributeError, IndexError, HTTPException) as e:

                    if not use_history:
                        return None, None

                    # Okay. No link. No ID. No reply. Just find the last proxied message within 5 messages.
                    prev_messages = message.channel.history(limit=5)
                    async for msg in prev_messages:
                        if msg.author.id == self.user.id:
                            return msg, message.content[(message.content.find(command_terminator) + 1):]
        # If nothing worked, return None, None.
        return None, None


    async def _handle_content(self, message: Message, content: str):
        """ Interal handler for a message of content to be handled. """

        if content:
            content = content.strip()

        # Check if it is a shortcut reaction command.
        for check, handler in self.shortcut_handlers.items():
            if check(content):
                await handler(message, content)
                break
        # Default to proxying the message
        else:
            # Now actually proxy the message.
            try:
                await self.proxy(message, content)
            except HTTPException as e:
                print(f"Failed to proxy message: {e}\nNot deleting original message.", file=sys.stderr)
                return

        # Delete the original message.
        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete original message: {e}.", file=sys.stderr)


    async def _handle_reaction(self, target: Message, payload: Union[Emoji, str], adding: bool):
        """ Handles adding or removing a reaction to a target message. """

        if adding:
            try:
                await target.add_reaction(payload)
            except HTTPException as e:
                print(f"Failed to handle reaction: {e}\nNot deleting original message.", file=sys.stderr)
        # Handle removing a reaction
        else:
            try:
                await target.remove_reaction(payload, self.user)
            except HTTPException as e:
                print(f"Failed to handle reaction: {e}\nNot deleting original message.", file=sys.stderr)

    @staticmethod
    def _parse_activity_spec(spec: str) -> Optional[Activity]:
        """
        Accepts a string describing a Discord activity for the !status command, and returns
        the discord.py Activity object it corresponds to, or None if `spec` doesn't match
        an activity format.
        """

        matches = re.match(DISCORD_STATUS_PATTERN, spec)
        if matches:
            activity_type = matches.group("type")
            if activity_type:
                # Discord.py's names for these don't include the second word for "listening to"
                # or "competing in", and are only in lowercase.
                activity_type = activity_type.split()[0].lower()
            else:
                # If not specified, default to "Playing".
                activity_type = "playing"

            name = matches.group("name")

            new_activity = Activity(type=ActivityType[activity_type], name=name)

            return new_activity

        return None

    @staticmethod
    async def proxy(message: Message, new_content: str):
        """ Sends a new message based on the metadata of the original, but with the modified content. """

        # Copy over any attachments, and copy the inline reply if any.
        files = [await att.to_file(spoiler=att.is_spoiler()) for att in message.attachments]
        ref = message.reference
        mention_flag = True
        if ref is not None:
            ref.fail_if_not_exists = False
            if message.reference.resolved.author.id not in map(lambda x: x.id, message.mentions):
                mention_flag = False

        # Send the new message.
        await message.channel.send(new_content, files=files, reference=ref, mention_author=mention_flag)


    async def handle_substitute_command(self, message: Message):
        """ !s/ -- performs a sed-subsitution on a target proxied message. """

        # If the command replied to a message, then use that to get the message to edit.
        if message.reference is not None:
            target = message.reference.resolved

        # Otherwise, assume the most recent message within 5 messages.
        else:

            prev_messages = message.channel.history(limit=5)
            target = None
            async for msg in prev_messages:
                if msg.author.id == self.user.id:
                    target = msg
                    break

        if target is None:
            print("Substitution requested but no proxied message was found within 5 messages!")
            return

        sed = Sed()

        # Basic regular expressions suck.
        sed.regexp_extended = True

        # Don't include the command prefix.
        start = message.content.find('!') + 1
        try:
            sed.load_string(message.content[start:])
        except PythonSed.sed.SedException:
            # If it failed, try again adding a trailing slash.
            sed.load_string(f"{message.content[start:]}/")

        # PythonSed accepts a file-like object, not a string, so we have to wrap the it in a StringIO object.
        new_content = sed.apply(StringIO(target.content), output=None)


        # Sed returns a list of lines, but we need a single string, so...
        new_content = '\n'.join(new_content)

        # Finally, perform the actual edit request to Discord's API.
        try:
            await target.edit(content=new_content)
        except HTTPException as e:
            print(f"Failed to edit message: {e}.\nNot deleting command message.")
            return

        # Delete the message that executed the command.
        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete command message: {e}.")


    async def handle_edit_command(self, message: Message):
        """ !edit -- changes the content of a target proxied message to a specified string. """

        target, args = await self._get_target_message_and_args(message)

        if target is None:
            print("Edit requested but no proxied message found within 5 messages!", file=sys.stderr)
            return

        new_content = args

        try:
            await target.edit(content=new_content)
        except HTTPException as e:
            print(f"Failed to edit message: {e}\nNot deleting command message.", file=sys.stderr)
            return

        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete command message: {e}.", file=sys.stderr)


    async def handle_delete_command(self, message: Message):
        """ !delete -- delete a message"""

        target, args = await self._get_target_message_and_args(message)

        if target is None:
            print("Delete requested but no proxied message found within 5 messages!", file=sys.stderr)
            return

        new_content = args

        try:
            await target.delete()
        except HTTPException as e:
            print(f"Failed to delete targeted message: {e}\nNot deleting command message.", file=sys.stderr)
            return

        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete command message: {e}.", file=sys.stderr)


    async def handle_presence_command(self, message: Message):
        """ !presence [offline|dnd|idle|online|sync] -- changes the bot user's presence. """

        # Parse the command.
        try:
            _, *presence = message.content.split(' ')
            presence = ' '.join(presence)
        except ValueError:
            print(f"Incorrect parameters for !presence: {message.content}", file=sys.stderr)
            return

        # If we're switching back to sync, sync!
        if presence == 'sync':
            self._status_override = None
            try:
                await self._set_presence(status=self._cached_status)
            except HTTPException as e:
                print(f"Failed to apply presence: {e}.", file=sys.stderr)

        # Otherwise, apply the new override.
        else:

            # Try to grab the relevant presence.
            try:
                self._status_override = Status[presence]
            except KeyError:
                print(f"Could not apply unknown presence {presence}.", file=sys.stderr)

            try:
                await self._set_presence(status=self._status_override)
            except HTTPException as e:
                print(f"Could not apply presence: {e}.", file=sys.stderr)
                return

        # Delete the original message.
        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete messsage: {e}.", file=sys.stderr)


    async def handle_startup_presence(self):
        applied_presence = None

        # If sync then look up current reference user presence
        if self.default_presence == 'sync':
            mutual_guilds = self.ref_user().mutual_guilds

            if not mutual_guilds:
                print("Failed to sync to user's presence: could not find shared guild", file=sys.stderr)
                return

            ref_user_member = await mutual_guilds[0].fetch_member(self.ref_user_id)
            applied_presence = ref_user_member.status if ref_user_member.status != Status.offline else Status.invisible

            self._status_override = None
            self._cached_status = applied_presence

        # Otherwise, set the override.
        else:
            # Try to grab the relevant presence.
            try:
                self._status_override = Status[self.default_presence]
                applied_presence = self._status_override
            except KeyError:
                print(f"Could not apply unknown presence {presence}.", file=sys.stderr)

        # Attempt to apply whichever presence we determined
        try:
            await self._set_presence(status=applied_presence)
        except HTTPException as e:
            print(f"Failed to apply presence: {e}.", file=sys.stderr)


    async def handle_status_command(self, message: Message):
        """ !status [status] -- sets the bot user's status. """

        # Get the arguments to the command.
        _command, *args = message.content.split(' ')
        args = ' '.join(args)

        # Parse out what activity this command is describing.
        new_activity = self._parse_activity_spec(args)

        # And then ask Discord to set it.
        await self._set_presence(activity=new_activity)

        # And finally delete the command message.
        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete command message: {e}.", sys.stderr)

    async def handle_nickname_command(self, message: Message):
        """ !nick [nickname] -- sets the bot user's nickname. """

        # Get the arguments to the command.
        _command, *args = message.content.split(' ')
        nickname = ' '.join(args)

        try:
            await message.guild.me.edit(nick=nickname)
        except HTTPException as e:
            print(f"Could not apply nickname update: {e}.", file=sys.stderr)
            return

        # Delete the original message.
        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete messsage: {e}.", file=sys.stderr)

    async def handle_reproxy_command(self, message: Message):
        """ <prefix>!reproxy -- reproxies a specified message. """

        # We don't care about any arguments, but a reproxy *must* be prefixed
        # if we've at all defined a prefix, this is a heuristic of sorts to
        # indicate that there are other Séance instances to worry about.
        if not message.content.startswith(self.command_prefix):
            # An empty prefix (`''`) will match against anything so this will still allow
            # unprefixed reproxying when a prefix isn't set.
            print("Reproxy requested but command was unprefixed which is disallowed for safety.", file=sys.stderr)
            return

        # We require a targeted message to know what to reproxy.
        # We have to manually handle history based targeting because we need custom logic to skip the
        # command message and still include the reference user's previous messages.
        target, _ = await self._get_target_message_and_args(message, use_history = False)

        # Custom history based targeting logic.
        if target is None:
            # Try to look in the last 6 messages, skipping our command message.
            prev_messages = message.channel.history(limit=6)
            async for msg in prev_messages:
                if msg.id == message.id:
                    # Skip our command message.
                    continue
                if msg.author.id in self.valid_reproxy_targets:
                    target = msg
                    break

        if target is None:
            print("Reproxy requested but no valid proxied message found within 5 messages!", file=sys.stderr)
            return

        # We must check that the returned result was *actually* a message by an author we're allowed to reproxy.
        if target.author.id not in self.valid_reproxy_targets:
            print("Reproxy requested but specified message was not authored by a reproxy allowed user!",
                  file = sys.stderr)
            return

        # Now we get down to actually reproxying
        try:
            await self.proxy(target, target.content)
        except HTTPException as e:
            print(f"Failed to proxy message during reproxy: {e}\nNot deleting original message.", file=sys.stderr)
            return

        try:
            await target.delete()
        except HTTPException as e:
            print(f"Failed to delete target message: {e}\nNot deleting command message.", file=sys.stderr)
            return

        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete command message: {e}.", file=sys.stderr)


    async def handle_simple_reaction(self, message: Message, content: str):
        """ Adds or removes a simple emoji reaction to a given message """

        target = await self._get_shortcut_target(message)
        await self._handle_reaction(target, content[1], content[0] == '+')


    async def handle_custom_reaction(self, message: Message, content: str):
        """ Adds or removes a custom emoji reaction to a given message """

        target = await self._get_shortcut_target(message)

        group_dict = DISCORD_REACTION_SHORTCUT_PATTERN.fullmatch(content).groupdict()

        # Find the emoji in the client cache.
        if emoji := self.get_emoji(int(group_dict["id"])):
            payload = emoji
        else:
            # Fail over to searching the messaage reactions.
            for react in target.reactions:
                if react.emoji.id == int(group_dict["id"]):
                    payload = react.emoji
                    break
            # Fail out.
            else:
                print(f"Custom Emoji ({content[1:]}) out of scope; not directly accessible by bot or present in message reactions.", file=sys.stderr)
                return

        await self._handle_reaction(target, payload, group_dict["action"] == '+')


    async def handle_newdm_command(self, accountish):
        print("account: {}".format(accountish))

    async def handle_ping(self, message):
        author = message.author
        guild_name = message.guild.name
        channel_name = message.channel.name
        message_url = message.jump_url
        silent = message.flags.silent

        await self.ref_user().send(
            f"You have been pinged by {author.display_name} in ***{guild_name}*** #{channel_name}: {message_url}",
            silent=silent
        )

    #
    # discord.py event handler overrides.
    #

    async def on_ready(self):

        if self.dm_guild_manager is None and self.dm_guild_id is not None:

            print("DM mode enabled for server ID {}".format(self.dm_guild_id))
            guild = await self.fetch_guild(self.dm_guild_id)
            self.dm_guild_manager = DiscordDMGuildManager(self, guild, pattern=self.pattern, **self.dm_manager_options)
            await self.dm_guild_manager.setup()

        print("Séance Discord client startup complete.")

        if self.sdnotify:
            # Tell systemd we've started up.
            notifer = sdnotify.SystemdNotifier(debug=True)
            notifer.notify("READY=1")

        if self.default_status:
            print("Setting startup status {}".format(self.default_status))
            default_activity = self._parse_activity_spec(self.default_status)
            await self._set_presence(activity=default_activity)

        if self.default_presence:
            print("Setting startup presence {}".format(self.default_presence))
            await self.handle_startup_presence()


    async def on_typing(self, channel, user, when):

        if self.dm_guild_manager is not None:
            await self.dm_guild_manager.handle_typing(channel, user, when)


    async def on_message(self, message: Message):

        # Sometimes message.content seems to be not-populated. Dunno why, but we can re-fetch to populate it.
        if not message.content:
            message = await self._refetch_message(message)


        # If the message was a DM to this bot, and DM management is enabled, handle that.
        if self.dm_guild_manager is not None:

            if isinstance(message.channel, discord.DMChannel) and message.author.id != self.user.id:
                await self.dm_guild_manager.handle_dm_to_server(message)
                return

        if self.forward_pings:

            # If the message mentions this bot, does not already mention the reference account,
            # and the author is not this bot or the reference account, forward the ping.

            mentions_this_bot = self.user.mentioned_in(message)
            mentions_ref_user = self.ref_user().mentioned_in(message)

            by_this_bot = message.author.id == self.ref_user_id
            by_ref_user = message.author.id == self.user.id

            if mentions_this_bot and not mentions_ref_user and not by_this_bot and not by_ref_user:
                await self.handle_ping(message)
                return


        # Otherwise, only do anything with messages from the reference account.
        if message.author.id != self.ref_user_id:
            return


        # If the message was sent in the designated DM guild, and DM management is enabled,
        # then proxy the message as a DM.
        if self.dm_guild_manager is not None:

            if message.channel.guild.id == self.dm_guild_id:
                await self.dm_guild_manager.handle_server_to_dm(message)
                return


        # See if the message matches the pattern that indicates we should proxy it.
        if matches := self.pattern.match(message.content):
            await self._handle_content(message, matches.groupdict()['content'])


        # Otherwise check for command prefixes.
        else:
            for string, handler in self.command_handlers.items():
                if self._matches_command(message.content, string):
                    await handler(message)
                    break


    async def on_message_edit(self, before: Message, after: Message):

        # Sometimes the content seems to be not-populated. Dunno why, but we can re-fetch to populate it.
        if not after.content:
            after = await self._refetch_message(after)

        # # If the edited message was a DM to this bot, and DM management is enabled, handle that.
        # if self.dm_guild_manager is not None:

            # if isinstance(after.channel, discord.DMChannel) and after.author.id != self.user.id:
                # await self.dm_guild_manager.handle_dm_to_server_edit(after)
                # return


        # With DM mamagement enabled, there are extra cases we care about.
        if self.dm_guild_manager is not None:


            # Or, if the edit is not from this bot, and it's in a DM to this bot, proxy that through.
            if after.author.id != self.user.id and after.channel.type == ChannelType.private:
                self.dm_guild_manager.handle_dm_to_server_edit(after)
                return


            # Or, if the edit is from this bot, and we're in the DM server, proxy that through.
            guild_id = after.guild.id if after.guild is not None else None
            if after.author.id == self.user.id and guild_id == self.dm_guild_id:
                self.dm_guild_manager.handle_server_to_dm_edit(after)
                return



        # We only care about messages from the reference account.
        if after.author.id != self.ref_user_id:
            return

        # If the message wasn't actually edited, we con't care.
        if before.content == after.content:
            return

        # Normal proxy handling follows.

        if matches := self.pattern.match(after.content):
            await self._handle_content(after, matches.groupdict()['content'])


    async def on_presence_update(self, _before: Member, after: Member):

        # Only sync status with the reference account.
        if after.id != self.ref_user_id:
            return

        status = after.status if after.status != Status.offline else Status.invisible

        # If we don't have a status override, adopt whatever status we've seen.
        if self._status_override is None:
            await self._set_presence(status=status)

        # Always cache the status, in case override turns off.
        self._cached_status = status


    # Needs to be raw because message might not be in the message cache.
    async def on_raw_reaction_add(self, payload: RawReactionActionEvent):

        # Restrict to only reactions added by our reference user.
        if payload.user_id != self.ref_user_id:
            return

        # Keep track of if this emoji matched our list of force proxied emoji.
        # `*` is usable as a global "proxy any reactions by the reference user".
        proxied = '*' in self.proxied_emoji

        # We have to handle default Unicode emoji slightly differently than Discord emoji.
        if payload.emoji.id is None:
            # This is a Unicode emoji and the actual value is stored in name.
            if payload.emoji.id in self.proxied_emoji:
                proxied=True
        else:
            # This is a custom Discord emoji
            if str(payload.emoji.id) in self.proxied_emoji:
                proxied=True

        # Don't do anything further if this reaction shouldn't be force proxied.
        if not proxied:
            return

        # Try to add the given emoji and clear the other, if the add fails this will prevent clearing the existing
        # reaction so you still get the reaction.
        # NOTE: Due to a quirk of how Discord/discord.py works, if another user has already added a given emoji
        # and that emoji is *still* on the given message when we add the reaction, we don't have to actually
        # have that emoji ourselves, this means that force proxied emoji *always* work.
        channel = self.get_channel(payload.channel_id)
        message = channel.get_partial_message(payload.message_id)
        try:
            await message.add_reaction(payload.emoji)
            await message.remove_reaction(payload.emoji, member=payload.member)
        except HTTPException:
            print('An error occurred while trying to reproxy a force proxied emoji', file=sys.stdout)


def _split_option(s: str) -> set[str]:
    """Split a list of whitespace or comma separated values provided in an option."""

    values = set()

    # Split by non-alphanum and commas.
    for item in re.split(r'\s+|,', s):
        if not len(item):
            # Skip blank entries.
            continue

        # Append to our set of items.
        values.add(item)

    return values

def main():

    options = [
        ConfigOption(name='token', required=True,
            help="The token to use for authentication. Required.",
        ),
        ConfigOption(name='ref user ID', required=True, metavar='ID', type=int,
            help="The ID of the user to recognize messages to proxy from.",
        ),
        ConfigOption(name='pattern', required=True,
            help="The Python regex used to match messages. Must have a named capture group called `content`.",
        ),
        ConfigOption(name='prefix', required=False, default='',
            help="An additional prefix to accept commands with.",
        ),
        ConfigOption(name='default status', required=False, default=None,
            help="The status to set upon startup",
        ),
        ConfigOption(name='default presence', required=False, default=None,
            help="The presence to set upon startup",
        ),
        ConfigOption(name='DM server ID', required=False, default=None, metavar='ID', type=int,
            help="The guild to use as the DM server. Not passing this disables DM mode.",
        ),
        ConfigOption(name='DM proxy untagged', required=False, default=None, type=bool,
            help="When using DM mode, proxy untagged messages in the DM server.",
        ),
        ConfigOption(name='Forward pings', required=False, default=False, type=bool,
            help="Whether to message the proxied user upon the bot getting pinged",
        ),
        ConfigOption(name='proxied emoji', required=False, default='',
            help="Comma or whitespace separated list of emoji or emoji IDs to always proxy when used as a reaction by the reference user."
        ),
        ConfigOption(name='valid reproxy targets', required=False, default='',
            help="Comma or whitespace separated list of user IDs. Only messages authored by an entry in this list will be allowed as targets of !reproxy."
        ),
    ]

    sdnotify_available = 'sdnotify' in sys.modules
    help_addendum = ' (Requires `sdnotify` Python package, not found.)' if not sdnotify_available else ''
    options.append(ConfigOption(name='systemd notify', required=False, default=None, type=bool,
        help=f'Notify systemd when startup is complete.{help_addendum}'
    ))

    help_epilog = ("All options can also be specified in an INI config (path passed with `--config`) "
        "as `key = value` under a section called `[Discord]`, where the key name is the option name "
        "without the leading -- (words can be separated by dashes, underscores, or spaces).\n"
        "Options can also be specified as environment variables, where the name of the variable is "
        "`SEANCE_DISCORD_` followed by the name of the option in SCREAMING_SNAKE_CASE."
    )

    config_handler = ConfigHandler(options, env_var_prefix='SEANCE_DISCORD_', config_section='Discord',
        argparse_init_kwargs={ 'prog': 'seance-discord', 'epilog': help_epilog },
    )

    options = config_handler.parse_all_sources()

    try:
        pattern = re.compile(options.pattern, re.DOTALL)
    except:
        print('Invalid regular expression given for --pattern', file=sys.stderr)
        raise

    if 'content' not in pattern.groupindex:
        options.argparser.error('regex pattern must have a named capture group called `content` (see https://docs.python.org/3/library/re.html#index-13')

    if options.systemd_notify and not sdnotify_available:
        options.argparser.error('--systemd-notify specified but `sdnotify` Python package not available. Try `pip3 install sdnotify`?')

    if running_in_systemd():
        if not options.systemd_notify:
            print("Warning: you seem to be running in a systemd service, but --systemd-notify was not passed.",
                file=sys.stderr
            )
            print("Warning: systemd will not properly detect if Séance fails to start.", file=sys.stderr)

        if not sys.stdout.write_through:
            print("Warning: you seem to be running in a systemd service, but line buffering is on.", file=sys.stderr)
            print("Warning: Séance's output will not properly redirect to systemd-journald. Set $PYTHONUNBUFFERED=1", file=sys.stderr)
            sys.stderr.flush()

    intents = discord.Intents.default()
    intents.members = True
    intents.presences = True
    intents.message_content = True
    client = SeanceClient(options.ref_user_id, pattern, options.prefix,
        sdnotify=options.systemd_notify,
        dm_guild_id=options.dm_server_id,
        dm_manager_options=dict(proxy_untagged=options.dm_proxy_untagged),
        default_status = options.default_status,
        default_presence = options.default_presence,
        forward_pings=options.forward_pings,
        intents=intents,
        proxied_emoji=_split_option(options.proxied_emoji),
        valid_reproxy_targets={int(i) for i in _split_option(options.valid_reproxy_targets)},
    )
    print("Starting Séance Discord bot.")
    client.run(options.token)


if __name__ == '__main__':
    sys.exit(main())
