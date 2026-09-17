# Séance — a ritual to channel the unseen

*Séance* is simple tool to make it easy to proxy messages under a bot account. It includes a Discord bot, and a very experimental Telegram bot.

## The Ritual

Séance is written in Python, and as such can be easily installed via pip: `pip3 install git+https://github.com/Qyriad/Seance`.

Once it is installed, the Discord bot can be run with `seance-discord`. The Discord bot requires a Discord [bot token](https://discord.com/developers/applications) passed with `--token` or the `$SEANCE_DISCORD_TOKEN` environment variable, the [snowflake ID](https://discord.com/developers/docs/resources/user#user-object-user-structure) of the user to recognize messages to proxy from passed with `--ref-user-id` or the `$SEANCE_DISCORD_REF_USER_ID` environment variable, and a pattern in [Python regex](https://docs.python.org/3/library/re.html#regular-expression-syntax) passed with `--pattern` or the `$SEANCE_DISCORD_PATTERN` environment variable. The pattern must include a [named capture group](https://docs.python.org/3/library/re.html#index-17) called `content`, which defines the content to proxy.

Okay, that sounds really complicated, so here's an example, where the format for proxying messages is anything that starts `b:` — capital or lowercase.

```sh
$ seance-discord --token ODDFOFXUpgf7yEntul5ockCA.OFk6Ph.lmsA54bT0Fux1IpsYvey5XuZk04 --ref-user-id 188344527881400991 --pattern "[bB]:(?P<content>.*)"
```

Note that the Discord bot also requires the Presence and Server Members Privileged Gateway Intents, which can be enabled in the "Bot" settings of the Discord application page.

### Options

These are the available configuration options, configured as described [below](#config-file).

#### Discord
- `token` - The Discord bot token used to authenticate, **important**: this must be kept secret as it allows anyone to control your bot account.
- `ref-user-id` - The reference user's Discord ID. This is the user account allowed to proxy messages and execute Séance commands.
- `pattern` - The regular expression pattern used to match a message to be proxied. Must contain a group named `content` which should contain the message body that will be in the proxied message.
- `prefix` - A prefix that can be used before a `!` command (such as `!edit`) to uniquely indicate that this instance of Séance should handle the command. Unprefixed commands are always accepted, even when this is set.
- `proxied-emoji` - A whitespace or comma separated list of unicode emoji (`🤝`) and Discord custom emoji ID numbers that will *always* be reproxied by Séance when used as a reaction by the reference user. The reference user will *not* be able to react with this emoji themselves, it will always be removed.

- **TODO: DM Mode configuration**


#### Telegram
**TODO**

### Config File

Anything that can be passed as a command-line option can also be specified an INI config file. Options for the Discord bot are placed under a `[Discord]` section, with the name of the INI key being the same as the command-line option without the leading `--`. Words can be separated by dashes, underscores, or spaces. For example, `--ref-user-id 188344527881400991` can be any of the following:
```ini
ref_user_id = 188344527881400991
ref user id = 188344527881400991
ref-user-id = 188344527881400991
```

Specify the config file to use on the command-line with `--config /path/to/file` (this is the one option that cannot itself be passed in a config file 😉). Options specified on the command line override options specified in a configuration file.

An example configuration file (which is functionally identical to the `seance-discord` CLI example invocation above) can be found in [contrib/](contrib/seance.ini).

### Commands

Once started, the bot also accepts a few chat commands:
- `!edit [reply|link] <new content>` — takes a reply or a link to a message, and the new message content
- `!s/pattern/replacement` — takes a reply, and a sed-style substitution command to edit a message
- `!status [playing | streaming | listening to | watching | competing in] <status>` — sets the bot's status ("playing" is the default if not specified)
- `!presence [invisible|dnd|idle|online|sync]` — sets the bot's presence to the specified value, or sets it to synchronize it to the reference user
- `!nick [nickname]` — sets the bot's nickname

The Séance CLI also takes an optional argument `--prefix`, which is an additional prefix to accept commands with. This is intended for cases where a single Discord user has more than one associated Séance bot, in order to be able to direct commands to a particular instance. For example, passing `--prefix b` allows you to run the chat command `b!status` to set the status for that specific instance of Séance.

### systemd

There is also a sample file for running the Séance Discord bot as a systemd service in [contrib](contrib/seance-discord.service). Note that for proper systemd support the Python package `sdnotify` is also required (`pip3 install sdnotify`). If you do not wish to enable this feature, you should remove the `--systemd-notify` argument from the provided service. 

It is suggested that you create a specific non-privileged user to run the bot under, the service config assumes this user is called "seance". 

Such a user can be created with `sudo useradd seance`. To avoid installing Séance globally, which might be ill-advised, you can create a home directory for the user, something like `sudo useradd --create-home --home-dir /srv/seance seance` will create a home directory for the user in `/srv/seance`. 

To install seance and sdnotify for this user use `sudo -u seance pip3 install --user sdnotify git+https://github.com/Qyriad/Seance`.

### Nix and NixOS

Séance provides a Nix flake, this flake contains a package definition for Séance and a NixOS service module for
configuring and running it.

To use the module in your NixOS configuration:

1. Add the Séance flake to your NixOS configuration: `seance.url = "github:Qyriad/Séance.git"`.
2. Import the NixOS module somewhere in your configuration: `{ seance, ... }: { imports = [ seance.nixosModules.default ]; };`.
3. Configure the services [see example](./contrib/example.nix).
4. Rebuild your NixOS configuration.


### Discord DM Mode

TODO: fill this out more >.>

#### Limitations:

- Bots do not receive typing notifications in DMs, so typing notification in a DM to the Séance user will not be proxied to the DM server.
- Bots cannot be added to group DMs, so neither can Séance users


## Comparison to PluralKit

Pros of Séance over PluralKit on Discord:
- Much faster proxy times
- No webhooks; real Discord account
  - Role colors in name
  - Real replies
- Easily proxy emoji reactions

Cons of Séance over PluralKit on Discord:
- Requires self-hosting
- Requires a separate instance for each proxy/account
- Each instance requires Manage Messages permissions on each server
- Only a tool for proxying; not an all-in-one plural companion tool and never will be
