import re
import sys

import fluxer
from fluxer import Message, Intents, File
from fluxer.models import Attachment
from fluxer.http import Route

from ..config import ConfigHandler, ConfigOption

class SeanceClient(fluxer.Client):
    def __init__(
        self,
        ref_user_id: int,
        pattern: str | re.Pattern,
        command_prefix: str,
        intents=None,
        *args,
        **kwargs,
    ):
        if not isinstance(pattern, re.Pattern):
            pattern = re.compile(pattern, re.DOTALL)
        self.pattern = pattern
        if intents is None:
            intents = Intents.default() | Intents.MESSAGE_CONTENT
        super().__init__(*args, intents=intents, **kwargs)

        self.ref_user_id = ref_user_id

        @self.event
        async def on_message(message: Message):
            await self._on_message(message)

        @self.event
        async def on_ready():
            print("Séance Fluxer startup complete")

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


    async def _proxy(self, message: Message, new_content: str):
        channel = message.channel
        # message.channel is only resolved if it's already been cached.
        if not channel:
            channel = await self.fetch_channel(str(message.channel_id))

        attachment_files = [await self._get_from_cdn(att) for att in message.attachments]

        await channel.send(new_content, files=attachment_files)

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

    async def _on_message(self, message: Message):
        if message.author.id != self.ref_user_id:
            return

        if matches := self.pattern.match(message.content):
            await self._handle_content(message, matches.groupdict()['content'])



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

    client = SeanceClient(options.ref_user_id, pattern, options.prefix)
    client.run(options.token)

if __name__ == '__main__':
    main()
