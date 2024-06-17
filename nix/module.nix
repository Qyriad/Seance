#
# Configuration for Seance instances.
#
# vim: et:ts=2:sw=2:
#
packages: { config, lib, pkgs , ... }:
with lib;
let
  cfg = config.services.seance;

  #
  # Configuration option provided for each discord user.
  #
  discordUserOptions = {name, config, ... }: {
    options = {

      referenceUserId = mkOption {
        type = types.int;
        description = ''
          The Discord User ID of the account associated with this Seance instance.
          Commands -- and text to proxy -- will only be accepted from this user.
        '';
      };

      secureConfigFile = mkOption {
        type = types.path;
        description = ''
          A file containing any strings to be passed as a configuration file.
          Should at least contain a `token = <discord-token>` line.

          Example contents might be:

          [Discord]
          token = mySecureTokenHere
        '';
      };

      prefix = mkOption {
        type = types.str;
        default = "";
        description = ''
          The user's prefix. This prefix is used to diambiguate !-style commands when the
            user has more than one bot associated with them.
          '';
      };

      pattern = mkOption {
        type = types.str;
        description = ''
          A regular expression matching the pattern that should be used as a "proxy tag".
          Text that matches the "content" capture pattern will be proxied as the target uset.

          As an example "^(?:([nN ][:#;.])|([n;#.][nN]))(?P<content>.*)$" matches messages
          matching patterns such as n#, n:, N;, #n, #N, and etc.
        '';
      };

      dmDatabaseDir = mkOption {
        type = types.str;
        default = "/var/lib/seance";
        description = ''
          The directory used to store a DM database, if one is around. Must be configured
          if more than one user is present on this machine with a DM server.
        '';
      };

      environment = mkOption {
        type = types.listOf types.str;
        default = [];
        defaultText = "[]";
        description = ''
          Any additional variables that should be set in the environment; in the same syntax
          as when specifying systemd services; i.e. NAME=value.
        '';
      };

    };
  };

  #
  # Function that translates a discordUser into an equivalent systemd.service entry.
  #
  serviceFromDiscordUser = name: user: {

    # The raw systemd unit for each user.
    "seance-discord-${name}" = {
      description = "Seance Discord Bot for ${name}";

      # Start automatically once our network is up.
      wantedBy = [ "multi-user.target" ];
      wants    = [ "network-online.target" ];
      after    = [ "network-online.target"  ];

      serviceConfig = {
        Type = "notify";
        ExecStart = "${cfg.package}/bin/seance-discord --config %d/configFile --systemd-notify";

        # Pass in the secure environment file, which sets the user's key.
        LoadCredential = "configFile:${user.secureConfigFile}";

        # Pass in the remainder of the options.
        Environment = [
          "PYTHONUNBUFFERED=1"
          "SEANCE_DISCORD_REF_USER_ID=\"${toString user.referenceUserId}\""
          "SEANCE_DISCORD_PATTERN=\"${user.pattern}\""
          "SEANCE_DISCORD_PREFIX=\"${user.prefix}\""
        ] ++ user.environment;

        # Set the working directory to be whatever was set as the DM database dir.
        WorkingDirectory = user.dmDatabaseDir;

        # If this crahses, try again after 5s.
        Restart = "on-failure";
        RestartSec = "5s";

        # Run this as a dynamic, non-root user.
        DynamicUser = true;
      };
    };
  };

  #
  # Configuration option provided for each telegram user.
  #
  telegramUserOptions = {name, config, ... }: {
    options = {

      referenceUsername = mkOption {
        type = types.str;
        description = ''
          The Telegram username of the account associated with this Seance instance.
          Commands -- and text to proxy -- will only be accepted from this user.
        '';
      };

      secureConfigFile = mkOption {
        type = types.path;
        description = ''
          A file containing any strings to be passed as a configuration file.
          Should at least contain a `token = <telegram-token>` line.

          Example contents might be:

          [Telegram]
          token = mySecureTokenHere
        '';
      };

      pattern = mkOption {
        type = types.str;
        description = ''
          A regular expression matching the pattern that should be used as a "proxy tag".
          Text that matches the "content" capture pattern will be proxied as the target uset.

          As an example "^(?:([nN ][:#;.])|([n;#.][nN]))(?P<content>.*)$" matches messages
          matching patterns such as n#, n:, N;, #n, #N, and etc.
        '';
      };

      environment = mkOption {
        type = types.listOf types.str;
        default = [];
        defaultText = "[]";
        description = ''
          Any additional variables that should be set in the environment; in the same syntax
          as when specifying systemd services; i.e. NAME=value.
        '';
      };

    };
  };

  #
  # Function that translates a discordUser into an equivalent systemd.service entry.
  #
  serviceFromTelegramUser = name: user: {

    # The raw systemd unit for each user.
    "seance-telegram-${name}" = {
      description = "Seance Telegram Bot for ${name}";

      # Start automatically once our network is up.
      wantedBy = [ "multi-user.target" ];
      wants    = [ "network-online.target" ];
      after    = [ "network-online.target"  ];

      serviceConfig = {
        Type = "notify";
        ExecStart = "${cfg.package}/bin/seance-telegram --config %d/configFile --systemd-notify";

        # Pass in the secure environment file, which sets the user's key.
        LoadCredential = "configFile:${user.secureConfigFile}";

        # Pass in the remainder of the options.
        Environment = [
          "PYTHONUNBUFFERED=1"
          "SEANCE_TELEGRAM_REF_USERNAME=\"${toString user.referenceUsername}\""
          "SEANCE_TELEGRAM_PATTERN=\"${user.pattern}\""
        ] ++ user.environment;

        # If this crahses, try again after 5s.
        Restart = "on-failure";
        RestartSec = "5s";

        # Run this as a dynamic, non-root user.
        DynamicUser = true;
      };
    };
  };

in {
  options = {
    services.seance = {

      enable = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Enables Seance servers for each specified user.
        '';
      };

      package = mkOption {
        type = types.path;
        default = packages.${pkgs.system}.default;
        defaultText = "pkgs.seance";
        example = "pkgs.callPackage ./mySeance {}";
        description = ''
          Package to use to provide Seance.
        '';
      };

      discordUsers = mkOption {
        type = with types; attrsOf (submodule discordUserOptions);
        default = {};
        example = {
          alice = {
            secureConfigFile = "/run/credentials/my-secure-config";
            referenceUserId = 123456789123456789;
            pattern = "^(?:([aA ][:#;.])|([a;#.][aA]))(?P<content>.*)$";
            prefix = "a";
          };
        };
        description = ''
          A collection of Discord bots to be created. Each entry will result in a single
          single-user Seance instance.
        '';
      };

      telegramUsers = mkOption {
        type = with types; attrsOf (submodule telegramUserOptions);
        default = {};
        example = {
          alice = {
            secureConfigFile = "/run/credentials/my-secure-config";
            referenceUsename = "telegramalice";
            pattern = "^(?:([aA ][:#;.])|([a;#.][aA]))(?P<content>.*)$";
          };
        };
        description = ''
          A collection of Telegram bots to be created. Each entry will result in a single
          single-user Seance instance.
        '';
      };
    };
  };

  # Create our systemd services (and environment) based on what's used.
  config = mkMerge [
    (mkIf cfg.enable {

      # Ensure Seance is around; as we'll need it.
      environment.systemPackages = [ cfg.package ];

      # Create a systemd service for each Discord Seance user.
      systemd.services = lib.attrsets.concatMapAttrs serviceFromDiscordUser cfg.discordUsers;
    })
    (mkIf cfg.enable {
      # Create a systemd service for each Telegram Seance user.
      systemd.services = lib.attrsets.concatMapAttrs serviceFromTelegramUser cfg.telegramUsers;
    })
  ];
}
