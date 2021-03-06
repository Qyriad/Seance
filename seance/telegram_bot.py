""" The Telegram bot version of Seance. """

import os
import re
import sys
import argparse
from io import StringIO

import telegram
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext


class SeanceClient:

    def __init__(self, ref_usernames, pattern, token):

        self.ref_usernames = ref_usernames
        self.pattern = re.compile(pattern, re.DOTALL)

        self.updater = Updater(token=token, use_context=True)
        message_filter = Filters.update.message & (~Filters.command) & Filters.user(username=self.ref_usernames)
        message_handler = MessageHandler(message_filter, self.on_message)
        self.updater.dispatcher.add_handler(message_handler)


    def run(self):
        self.updater.start_polling()


    def proxy(self, context: CallbackContext, message: telegram.Message, new_content: str, entity_shift: int):

        # Man I wish Python had a null-coalescing member access operator.
        reply_id = message.reply_to_message.message_id if message.reply_to_message is not None else None

        # Rich text in Telegram is specified by an index, but we've changed the content, so those indicies are no
        # longer valid. So we have to shift those indecies by however much we changed the start of the content.

        entities = message.entities[:] if message.entities is not None else message.caption_entities[:]
        for entity in entities:
            entity.offset -= entity_shift
        
        # FIXME: handle non-photo, non-video attachement and media groups (more than one photo/video or combined photo+video)

        if message.video:
            context.bot.send_video(message.chat_id, message.video, caption=new_content, reply_to_message_id=reply_id, caption_entities=entities)
        elif message.photo:
            # Assume that the largest number of pixels is the best version of the photo available?
            largest_photo = max(message.photo, key=lambda photo : photo.width*photo.height)
            context.bot.send_photo(message.chat_id, largest_photo, caption=new_content, reply_to_message_id=reply_id, caption_entities=entities)
        else:
            context.bot.send_message(message.chat_id, new_content, reply_to_message_id=reply_id, entities=entities)


    def on_message(self, update: Update, context: CallbackContext):

        message: telegram.Message = update.message

        text = message.text if message.text is not None else message.caption
        matches = self.pattern.match(text)
        if matches:

            new_content = matches.groupdict()['content']
            offset_to_content = matches.start('content')

            if new_content:
                pre_strip_len = len(new_content)
                new_content = new_content.strip()
                post_strip_len = len(new_content)
                stripped_count = pre_strip_len - post_strip_len
                offset_to_content += stripped_count

            # Proxy the message.
            try:
                self.proxy(context, message, new_content, entity_shift=offset_to_content)
            except telegram.error.BadRequest as e:
                print(f"Failed to proxy message: {e}\nNot deleting original message.")
                return

            # Delete the original message.
            try:
                message.delete()
            except telegram.error.BadRequest as e:
                print(f"Failed to delete original message: {e}.", file=sys.stderr)


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=False, action='store', type=str,
        help="The token to use for authentication. Required or `$SEANCE_TELEGRAM_TOKEN` environment variable.")
    parser.add_argument('--ref-username', required=False, action='store', type=str,
        help="The username(s) of the user to recognize messages to proxy from. "
        "Required or `$SEANCE_TELEGRAM_REF_USERNAME` environment variable. "
        "Multiple usernames can be separated with commas.")
    parser.add_argument('--pattern', required=True, action='store', type=str,
        help="The Python regex to use to match messages. Must have a capture group named `content`.")

    args = parser.parse_args()

    token = args.token if args.token else os.getenv("SEANCE_TELEGRAM_TOKEN")
    ref_username = args.ref_username if args.ref_username else os.getenv("SEANCE_TELEGRAM_REF_USERNAME")
    ref_usernames = set(ref_username.split(','))

    pattern = args.pattern

    bot = SeanceClient(ref_usernames, pattern, token)

    bot.run()

if __name__ == '__main__':
    main()
