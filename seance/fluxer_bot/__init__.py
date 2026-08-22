import re
import sys
from io import StringIO
from typing import AsyncIterator, Optional

import fluxer
from fluxer import Message, Channel, Intents, File
from fluxer.errors import HTTPException
from fluxer.models import Attachment
from fluxer.http import Route

import PythonSed
from PythonSed import Sed

try:
    import sdnotify
except ImportError:
    pass

from ..config import ConfigHandler, ConfigOption

class SeanceClient(fluxer.Client):
    def __init__(
        self,
        ref_user_id: int,
        pattern: str | re.Pattern,
        command_prefix: str,
        intents=None,
        *args,
        sdnotify=False,
        **kwargs,
    ):
        if not isinstance(pattern, re.Pattern):
            pattern = re.compile(pattern, re.DOTALL)
        self.pattern = pattern
        if intents is None:
            intents = Intents.default() | Intents.MESSAGE_CONTENT
        super().__init__(*args, intents=intents, **kwargs)

        self.ref_user_id = ref_user_id
        self.command_prefix = command_prefix
        self.sdnotify = sdnotify

        self.command_handlers = {
            '!s/': self.handle_substitute_command,
            '!edit': self.handle_edit_command,
            '!delete': self.handle_delete_command,
        }

        @self.event
        async def on_message(message: Message):
            await self._on_message(message)

        @self.event
        async def on_ready():
            print("Séance Fluxer startup complete")
            if self.sdnotify:
                notifier = sdnotify.SystemdNotifier(debug=True)
                notifier.notify('READY=1')

        @self.event
        async def on_message_edit(message: Message):
            await self._on_message_edit(message)

    async def _get_from_cdn(self, attachment: Attachment) -> File:
        assert self._http
        session = await self._http._ensure_session()
        headers = dict()
        if content_type := attachment.content_type:
            headers['Content-Type'] = content_type

        url = attachment.url
        if proxy_url := attachment.proxy_url:
            url = proxy_url

        async with session.request(
            'GET',
            url,
            headers=headers,
        ) as resp:
            if resp.status == 200:
                file_data = await resp.read()
                return File(
                    file_data,
                    filename=attachment.filename,
                    description=attachment.description,
                )
            elif resp.status == 404:
                raise fluxer.errors.NotFound(resp.status, 'asset not found', attachment.filename)
            else:
                raise fluxer.errors.HTTPException(resp.status, 'failed to get asset', attachment.filename)

    async def _channel_history(
        self,
        channel: Channel,
        limit: Optional[int] = 100,
    ) -> AsyncIterator[Message]:
        assert self._http
        route = self._http._route(
            'GET',
            '/channels/{channel_id}/messages?limit={limit}',
            channel_id=channel.id,
            limit=limit,
        )
        resp = await self._http.request(route)
        for message_json in resp:
            yield Message.from_data(message_json, http=self._http)

    async def _proxy(self, message: Message, new_content: str):
        # message.channel is only resolved if it's already been cached.
        channel = message.channel if message.channel else await self.fetch_channel(str(message.channel_id))

        attachment_files = [await self._get_from_cdn(att) for att in message.attachments]

        ref = None
        if reply_target := message.referenced_message:
            ref = dict(
                channel_id = reply_target.channel_id,
                message_id = reply_target.id,
            )

        await channel.send(
            new_content,
            files=attachment_files,
            message_reference=ref,
        )

    async def _channel_from_message(self, message: Message) -> Channel:
        if ch := message.channel:
            return ch

        return await self.fetch_channel(str(message.channel_id))

    async def _get_edit_target(self, command_message: Message) -> Message:
        if target := command_message.referenced_message:
            return target

        channel = await self._channel_from_message(command_message)
        # Assume the most recent message within 5 messages.
        history = self._channel_history(channel, limit=5)
        async for msg in history:
            if msg.author.id == self.user.id:
                return msg

        raise ValueError("Edit requested but no proxied message was found within 5 messages!")

    async def handle_substitute_command(self, message: Message):
        """ !s/ -- performs a sed substitution on a target proxied message. """

        # Messages can be targeted by replies, or just the most recent message within 5.
        target = await self._get_edit_target(message)

        # Basic regex sucks.
        sed = Sed(regexp_extended=True)

        # Don't include the comand prefix.
        start = message.content.find('!') + 1
        sed_cmd = message.content[start:]
        try:
            sed.load_string(sed_cmd)
            sed.script.compile()
        except PythonSed.sed.SedException:
            # If it failed, try again with a trailing slash.
            sed = Sed(regexp_extended=True)
            sed.load_string(f"{sed_cmd}/")
            sed.script.compile()

        new_content = sed.apply(StringIO(target.content), output=None)
        new_content = '\n'.join([line.strip() for line in new_content])

        # Submit the actual edit!
        try:
            await target.edit(content=new_content)
        except HTTPException as e:
            print(f"Failed to edit message: {e}.\nNot deleting original message.")
            return

        # Command messages don't stick around.
        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete command message: {e}")


    async def handle_edit_command(self, message: Message):
        """ !edit -- changes the content of a target proxied message to a specified string, wholesale. """

        # Same message targeting as !s
        target = await self._get_edit_target(message)

        new_content = message.content.removeprefix('!edit').strip()

        try:
            await target.edit(content=new_content)
        except HTTPException as e:
            print(f"Failed to edit message: {e}\nNot deleting original message.", file=sys.stderr)
            return

        try:
            await message.delete()
        except HTTPException as e:
            print(f"Failed to delete original message: {e}.", file=sys.stderr)

    async def handle_delete_command(self, message: Message):
        """ !delete -- delete a message """
        target = await self._get_edit_target(message)
        try:
            await target.delete()
        except Exception as e:
            print(f"Failed to delete message: {e}\nNot deleting command message.", file=sys.stderr)
            return

        try:
            await message.delete()
        except Exception as e:
            print(f"Failed to delete command message: {e}", file=sys.stderr)

    async def _handle_content(self, message: Message, content: str):
        if content:
            content = content.strip()

        # Proxy the message.
        try:
            await self._proxy(message, content)
        except Exception as e:
            print(f"Failed to proxy message: {e}\nNot deleting original message.", file=sys.stderr)
            return

        # Delete the original message.
        try:
            await message.delete()
        except Exception as e:
            print(f"Failed to delete original message: {e}.", file=sys.stderr)

    def _matches_command(self, content, command):
        return content.startswith(command) or content.startswith(f"{self.command_prefix}{command}")

    async def _on_message(self, message: Message):
        if message.author.id != self.ref_user_id:
            return

        if matches := self.pattern.match(message.content):
            await self._handle_content(message, matches.groupdict()['content'])
            return

        for string, handler in self.command_handlers.items():
            if self._matches_command(message.content, string):
                await handler(message)
                break

    async def _on_message_edit(self, message: Message):
        # We actually handle this the same way as normal messages!
        # Mostly because Fluxer doesn't give us the before and after.
        await self._on_message(message)


def main():
    options = [
        ConfigOption(name='token', required=True,
            help="The OAuth token to use for authentication.",
        ),
        ConfigOption(name='ref user ID', required=True, metavar='ID', type=int,
            help="The ID of the user to recognize messages to proxy from",
        ),
        ConfigOption(name='pattern', required=True,
            help="The Python regex used to match messages. Must have a named capture group called `content`.",
        ),
        ConfigOption(name='prefix', required=False, default='',
            help="An additional prefix to accept commands with.",
        ),
    ]

    sdnotify_available = 'sdnotify' in sys.modules
    help_addendum = ' (Requires `sdnotify` Python package, not found)' if not sdnotify_available else ''
    options.append(ConfigOption(name='systemd notify', required=False, default=None, type=bool,
        help=f'Notify systemd when startup is complete.{help_addendum}',
    ))

    help_epilog = ("All options can also be specified in an INI config (path passed with `--config`) "
        "as `key = value` under a section called `[Fluxer]`, where the key name is the option name "
        "without the leading -- (words can be separated by dashes, underscores, or spaces).\n"
        "Options can also be specified as environment variables, where the name of the variable is "
        "`SEANCE_FLUXER_` followed by the name of the option in SCREAMING_SNAKE_CASE."
    )

    config_handler = ConfigHandler(
        options,
        env_var_prefix='SEANCE_FLUXER_',
        config_section='Fluxer',
        argparse_init_kwargs=dict(prog='seance-fluxer'),
    )

    options = config_handler.parse_all_sources()

    pattern = options.pattern
    try:
        pattern = re.compile(options.pattern, re.DOTALL)
    except:
        print(f'Invalid regular expression given for {pattern=}', file=sys.stderr)
        raise

    if 'content' not in pattern.groupindex:
        options.argparser.error('regex pattern must have a named capture group called `content` (see https://docs.python.org/3/library/re.html#index-13')

    if options.systemd_notify and not sdnotify_available:
        options.argparser.error('--systemd-notify specified but `sdnotify` Python package not available. Try `pip3 install sdnotify`?')

    intents = Intents.default() \
        | Intents.MESSAGE_CONTENT \
        | Intents.GUILD_MEMBERS \
        | Intents.GUILD_PRESENCES

    client = SeanceClient(options.ref_user_id, pattern, options.prefix, intents=intents)
    client.run(options.token)

if __name__ == '__main__':
    main()
