""" The Discord bot version of Seance. """

import os
import re
import sys
import argparse
from io import StringIO
from typing import Union

import discord
from discord import Message, Member, Status
from discord.activity import Activity, ActivityType, CustomActivity
from discord.errors import HTTPException
from discord.guild import Guild
from discord.message import PartialMessage

import PythonSed
from PythonSed import Sed


# A pattern that matches a link to a Discord message, and captures the channel ID and message ID.
DISCORD_MESSAGE_URL_PATTERN = re.compile(r'https://(?:\w+.)?discord(?:app)?.com/channels/\d+/(\d+)/(\d+)')

# A pattern for matching Discord activities (https://discord.com/developers/docs/topics/gateway#activity-object).
DISCORD_STATUS_PATTERN = re.compile(r'(?P<type>playing|streaming|listening to|watching|competing in)?\s*(?P<name>.+)', re.IGNORECASE | re.DOTALL)


class KeepCurrentSentinel:
    """ A sentinal type used just for SeanceClient._set_presence(). """
keep_current = KeepCurrentSentinel()


class SeanceClient(discord.Client):

    def __init__(self, ref_user_id, pattern, command_prefix, *args, **kwargs):

        self.ref_user_id = ref_user_id
        self.pattern = re.compile(pattern, re.DOTALL)
        self.command_prefix = command_prefix

        super().__init__(*args, **kwargs)

        # Store any status overrides present.
        self._status_override = None
        self._cached_status = Status.online

        # Cache our current presence, as we seem to have trouble fetching it from Discord later on.
        self._current_activity = None
        self._current_status = Status.online

        self.command_handlers = {
            '!s/': self.handle_substitute_command,
            '!edit': self.handle_edit_command,
            '!status': self.handle_status_command,
            '!presence': self.handle_presence_command,
        }


    def _matches_command(self, content, command):
        return content.startswith(command) or content.startswith(f"{self.command_prefix}{command}")


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

        return await message.channel.fetch_message(message)


    async def _get_target_message_and_args(self, message: Message, command_terminator=' '):
        """ Parse out a target message and remaining arguments from a message.

        This is useful for commands like !edit that take a message somehow, but messages can be passed in 3 forms:
            1. A reply, in which case the message is not specified anywhere in the content, and so the rest of the
                arguments are the entire content of the message (besides the command itself).
            2. An ID, in which case the channel is implied to be the same as the command, and the rest of the
                arguments are everything after "word 1".
            3. A link, in which case the channel and message ID are both parsed from that command word, and the
                rest of the arguments are everything after "word 1" again.

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

                    # Okay. No link. No ID. No reply. Just find the last proxied message within 50 messages.
                    prev_messages = message.channel.history(limit=50)
                    async for msg in prev_messages:
                        if msg.author.id == self.user.id:
                            return msg, message.content[(message.content.find(command_terminator) + 1):]


    @staticmethod
    async def proxy(message: Message, new_content: str):
        """ Sends a new message based on the metadata of the original, but with the modified content. """

        # Copy over any attachments, and copy the inline reply if any.
        files = [await att.to_file() for att in message.attachments]
        ref = message.reference
        if ref is not None:
            ref.fail_if_not_exists = False

        # Send the new message.
        await message.channel.send(new_content, files=files, reference=ref)


    async def handle_substitute_command(self, message: Message):
        """ !s/ -- performs a sed-subsitution on a target proxied message. """

        # If the command replied to a message, then use that to get the message to edit.
        if message.reference is not None:
            target = message.reference.resolved

        # Otherwise, assume the most recent message within 50 messages.
        else:

            prev_messages = message.channel.history(limit=50)
            target = None
            async for msg in prev_messages:
                if msg.author.id == self.user.id:
                    target = msg
                    break

        if target is None:
            print("Substitution requested but no proxied message was found within 50 messages!")
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
            print(f"Failed to edit message: {e}.\nNot deleting original message.")
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
            print("Edit requested but no proxied message found within 50 messages!", file=sys.stderr)
            return

        new_content = args

        try:
            await target.edit(content=new_content)
        except HTTPException as e:
            print(f"Failed to edit message: {e}\nNot deleting original message.", file=sys.stderr)
            return

        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete original message: {e}.", file=sys.stderr)


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


    async def handle_status_command(self, message: Message):
        """ !status [status] -- sets the bot user's status. """

        # Get the arguments to the command.
        _command, *args = message.content.split(' ')
        args = ' '.join(args)

        matches = re.match(DISCORD_STATUS_PATTERN, args)

        if matches:

            activity_type = matches.group('type')
            if activity_type:
                # Discord.py's names for these don't include the second word for "listening to" or "competing in",
                # and are only in lowercase.
                activity_type = matches.group('type').split()[0].lower()
            else:
                # If not specified, default to 'Playing'.
                activity_type = 'playing'

            name = matches.group('name')

            new_activity = Activity(type=ActivityType[activity_type], name=name)

            # Set the new activity.
            await self._set_presence(activity=new_activity)

        else:
            # If it didn't match, clear the status.
            await self._set_presence(activity=None)

        # And delete the command message.
        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete command message: {e}.", sys.stderr)


    #
    # discord.py event handler overrides.
    #

    async def on_ready(self):
        print("Séance Discord client startup complete.")


    async def on_message(self, message: Message):

        # Sometimes message.content seems to be not-populated. Dunno why, but we can re-fetch to populate it.
        if not message.content:
            message = await self._refetch_message(message)

        # Only do anything with messages from the reference account.
        if message.author.id != self.ref_user_id:
            return

        # See if the message matches the pattern that indicates we should proxy it.
        matches = self.pattern.match(message.content)
        if matches:
            new_content = matches.groupdict()['content']
            if new_content:
                new_content = new_content.strip()

            # Now actually proxy the message.
            try:
                await self.proxy(message, new_content)
            except HTTPException as e:
                print(f"Failed to proxy message: {e}\nNot deleting original message.", file=sys.stderr)
                return

            # Delete the original message.
            try:
                await message.delete()
            except HTTPException as e:
                print(f"Failed to delete original message: {e}.", file=sys.stderr)

        else:

            for string, handler in self.command_handlers.items():
                if self._matches_command(message.content, string):
                    await handler(message)
                    break

    async def on_message_edit(self, before: Message, after: Message):

        # Sometimes the content seems to be not-populated. Dunno why, but we can re-fetch to populate it.
        if not after.content:
            after = await self._refetch_message(after)

        # We only care about messages from the reference account.
        if after.author.id != self.ref_user_id:
            return

        # If the message wasn't actually edited, we con't care.
        if before.content == after.content:
            return

        # Normal proxy handling follows.

        matches = self.pattern.match(after.content)
        if matches:
            new_content = matches.groupdict()['content']
            if new_content:
                new_content = new_content.strip()

            # Proxy the message.
            try:
                await self.proxy(after, new_content)
            except HTTPException as e:
                print(f"Failed to proxy message: {e}\nNot deleting original message.", file=sys.stderr)
                return

            # Delete the original message.
            try:
                await after.delete()
            except HTTPException as e:
                print(f"Failed to delete original message: {e}.", file=sys.stderr)


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


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=False, action='store', type=str,
        help="The token to use for authentication. Required or `$SEANCE_DISCORD_TOKEN` environment variable.")
    parser.add_argument('--ref-user-id', required=False, action='store', type=int, metavar='ID',
        help="The ID of the message to recognize messages to proxy from. "
            "Required or `$SEANCE_DISCORD_REF_USER_ID` environment variable.")
    parser.add_argument('--pattern', required=False, action='store', type=str,
        help="The Python regex used to match messages. "
            "Required or `$SEANCE_DISCORD_PATTERN` environment variable.")
    parser.add_argument('--prefix', required=False, action='store', type=str, default='',
        help="An additional prefix to accept commands with.")

    args = parser.parse_args()

    token = args.token if args.token else os.getenv("SEANCE_DISCORD_TOKEN")
    if not token:
        parser.error("--token required or $SEANCE_DISCORD_TOKEN")

    ref_user_id = args.ref_user_id if args.ref_user_id else os.getenv("SEANCE_DISCORD_REF_USER_ID")
    try:
        ref_user_id = int(ref_user_id)
    except (ValueError, TypeError):
        parser.error("--ref-user-id required or $SEANCE_DISCORD_REF_USER_ID")

    pattern = args.pattern if args.pattern else os.getenv('SEANCE_DISCORD_PATTERN')
    if not pattern:
        parser.error("--pattern required or $SEANCE_DISCORD_PATTERN")

    # HACK: Monkey-patch the base API URL, as discord.py uses API v7 and replies seem to want v8.
    discord.http.Route.BASE = 'https://discord.com/api/v8'

    # Ensure we can access member lists and collections of their presences.
    intents = discord.Intents.default()
    intents.members = True
    intents.presences = True

    client = SeanceClient(ref_user_id, pattern, args.prefix, intents=intents)
    print("Starting Séance Discord bot.")
    client.run(token)


if __name__ == '__main__':
    sys.exit(main())
