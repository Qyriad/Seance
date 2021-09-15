# Séance — a ritual to channel the unseen

*Séance* is simple tool to make it easy to proxy messages under a bot account. It includes a Discord bot, and a very experimental Telegram bot.

## The Ritual

Séance is written in Python, and as such can be easily installed via pip: `pip3 install git+https://github.com/Qyriad/Seance`.

Once it is installed, the Discord bot can be run with `python3 -m seance.discord_bot`. The Discord bot requires a Discord [bot token](https://discord.com/developers/applications) passed with `--token` or the `$SEANCE_DISCORD_TOKEN` environment variable, the [snowflake ID](https://discord.com/developers/docs/resources/user#user-object-user-structure) of the user to recognize messages to proxy from passed with `--ref-user-id` or the `$SEANCE_DISCORD_REF_USER_ID` environment variable, and a pattern in [Python regex](https://docs.python.org/3/library/re.html#regular-expression-syntax) passed with `--pattern` or the `$SEANCE_DISCORD_PATTERN` environment variable. The pattern must include a [named capture group](https://docs.python.org/3/library/re.html#index-17) called `content`, which defines the content to proxy.

Okay, that sounds really complicated, so here's an example, where the format for proxying messages is anything that starts `b:` — capital or lowercase.

```sh
$ python3 -m seance.discord_bot --token ODDFOFXUpgf7yEntul5ockCA.OFk6Ph.lmsA54bT0Fux1IpsYvey5XuZk04 --ref-user-id 188344527881400991 --pattern "[bB]:(?P<content>.*)"
```

Once started, the bot also accepts a few chat commands:
- `!edit [reply|link] <new content>` — takes a reply or a link to a message, and the new message content
- `!s/pattern/replacement` — takes a reply, and a sed-style substitution command to edit a message
- `!status [playing | streaming | listening to | watching | competing in] <status>` — sets the bot's status ("playing" is the default if not specified)
- `!presence [invisible|dnd|idle|online|sync]` — sets the bots presence to the specified value, or sets it to synchronize it to the reference user

The Séance CLI also takes an optional argument `--prefix`, which is an additional prefix to accept commands with. This is intended for cases where a single Discord user has more than one associated Séance bot, in order to be able to direct commands to a particular instance. For example, passing `--prefix b` allows you to run the chat command `b!status` to set the status for that specific instance of Séance.


There is also a sample file for running the Séance Discord bot as a systemd service in [contrib](contrib/seance-discord.service). Note that for proper systemd support the Python package `sdnotify` is also required (`pip3 install sdnotify`).


## Comparison to PluralKit

Pros of Séance over PluralKit on Discord:
- Much faster proxy times
- No webhooks; real Discord account
  - Role colors in name
  - Real replies

Cons of Séance over PluralKit on Discord:
- Requires self-hosting
- Requires a separate instance for each proxy/account
- Each instance requires Manage Messages permissions on each server
- Only a tool for proxying; not an all-in-one plural companion tool and never will be
