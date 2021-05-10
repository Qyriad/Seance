#!/usr/bin/env python3

import os
import re
import argparse

import telegram
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext


class SeanceTelegramBot:

    def __init__(self, ref_username, pattern, token):

        self.ref_username = ref_username
        self.pattern = re.compile(pattern, re.DOTALL)

        self.updater = Updater(token=token, use_context=True)
        message_handler = MessageHandler(Filters.text & (~Filters.command), self.on_message)
        self.updater.dispatcher.add_handler(message_handler)


    def run(self):
        self.updater.start_polling()


    def proxy(self, context: CallbackContext, message: telegram.Message, new_content: str):

        # FIXME: handle attachments

        # Man I wish Python had a null-coalescing member access operator.
        reply_id = message.reply_to_message.message_id if message.reply_to_message is not None else None

        context.bot.send_message(message.chat_id, new_content, reply_to_message_id=reply_id)


    def on_message(self, update: Update, context: CallbackContext):

        message: telegram.Message = update.message
        author: telegram.User = message.from_user

        # We only care about messages from the reference user.
        if author.username != self.ref_username:
            return

        matches = self.pattern.match(message.text)
        if matches:
            new_content = matches.groupdict()['content']
            if new_content:
                new_content = new_content.strip()

            # Proxy the message.
            try:
                self.proxy(context, message, new_content)
            except telegram.error.BadRequest as e:
                print("Failed to proxy message: {}\nNot deleting original message.".format(e))
                return

            # Delete the original message.
            try:
                message.delete()
            except telegram.error.BadRequest as e:
                print("Failed to delete original message: {}".format(e))




def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=False, action='store', type=str,
        help="The token to use for authentication. Required or `$SEANCE_TELEGRAM_TOKEN` environment variable.")
    parser.add_argument('--ref-username', required=False, action='store', type=str,
        help="The username of the user to recognize messages to proxy from."
        "Required or `$SEANCE_TELEGRAM_REF_USERNAME` environment variable.")
    parser.add_argument('--pattern', required=True, action='store', type=str,
        help="The Python regex to use to match messages. Must have a capture group named `content`.")

    args = parser.parse_args()

    token = args.token if args.token else os.getenv("SEANCE_TELEGRAM_TOKEN")
    ref_username = args.ref_username if args.ref_username else os.getenv("SEANCE_TELEGRAM_REF_USERNAME")
    pattern = args.pattern


    bot = SeanceTelegramBot(ref_username, pattern, token)

    bot.run()


if __name__ == '__main__':
    main()
