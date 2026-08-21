{
  lib,
  python3,
  fetchFromGitHub,
}: let
  inherit (python3.pkgs)
    buildPythonPackage
    setuptools
    wheel
    aiohttp
    emoji
  ;
in buildPythonPackage {
  pname = "fluxer";
  version = "0.4.2";
  src = fetchFromGitHub {
    owner = "Fluxer-py";
    repo = "fluxer.py";
    # lol they don't push tags
    rev = "300d48e45deb88cbc1130b8749a5ad31cbc45d8b";
    hash = "sha256-BV8zKU3EXAnLcamQtXrHF7gGRfUSLagAw2jsqgfyqck=";
  };

  format = "pyproject";
  nativeBuildInputs = [
    setuptools
    wheel
  ];

  propagatedBuildInputs = [
    aiohttp
    emoji
  ];
}
