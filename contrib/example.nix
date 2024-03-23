# Seance Discord bots.
#
# vim: et:ts=2:sw=2:
#
{ pkgs, config, ... }:
let
  discordUserId     = 123456789123456789;
  telegramUsername  = "telegramalice";

in {
  # My secrets
  # Age usage is optional, but due to flakes encluding the entire contents of the
  # source repository into the world readable Nix store, you'll want to use some
  # source that is either encrypted and thus safe to pull into the Nix store directly
  # (which you'll need to have decrypted at runtime) or you'll want to point it at a
  # preconfigured path using a string in the Séance options below these.
  age.secrets.secureConfigHeather.file          = ./secureConfigHeather.age;
  age.secrets.secureConfigLily.file             = ./secureConfigLily.age;
  age.secrets.secureConfigHeatherTelegram.file  = ./secureConfigHeather.age;
  age.secrets.secureConfigLilyTelegram.file     = ./secureConfigLily.age;

  services.seance = {
    enable = true;

    #
    # Discord.
    #
    discordUsers = {

      heather = {
        secureConfigFile = config.age.secrets.secureConfigHeather.path;
        referenceUserId = discordUserId;
        pattern = "^(?:([hH][.,;#])|([.,#;][hH]))(?P<content>.*)$";
        prefix = "v";
      };

      lily = {
        secureConfigFile = config.age.secrets.secureConfigLily.path;
        referenceUserId = discordUserId;
        pattern = "^(?:([lL][.,;#])|([.,#;][lL]))(?P<content>.*)$";
        prefix = "l";
      };
    };

    #
    # Telegram.
    #
    telegramUsers = {

      heather = {
        secureConfigFile = config.age.secrets.secureConfigHeatherTelegram.path;
        referenceUsername = telegramUsername;
        pattern = "^(?:([hH][.,;#])|([.,#;][hH]))(?P<content>.*)$";
      };

      lily = {
        secureConfigFile = config.age.secrets.secureConfigLilyTelegram.path;
        referenceUsername = telegramUsername;
        pattern = "^(?:([lL][.,;#])|([.,#;][lL]))(?P<content>.*)$";
      };
    };

  };
}
