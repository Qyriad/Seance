{
  lib,
  python3,
  python-sed,
}: let
  inherit (python3.pkgs)
    buildPythonPackage
    setuptools
    wheel
    discordpy
    emoji
    python-telegram-bot
    sdnotify
  ;
in buildPythonPackage {
  pname = "seance";
  version = "0.2";

  src = lib.cleanSource ./.;

  format = "pyproject";

  nativeBuildInputs = [
    setuptools
    wheel
  ];

  propagatedBuildInputs = [
    python-sed
    discordpy
    emoji
    python-telegram-bot
    sdnotify
  ];
}
