{
  lib,
  python3,
  fetchFromGitHub,
}: let
  inherit (python3.pkgs)
    buildPythonPackage
    setuptools
    wheel
  ;
in buildPythonPackage {
  pname = "sed";
  version = "1.0";

  src = fetchFromGitHub {
    owner = "GillesArcas";
    repo = "PythonSed";
    rev = "342534107e8168fe2889dcd2c9c8126546233c2f";
    hash = "sha256-RdjR+sWjymmB7xCxiPe1xs3x7NXUyXlFM8hVllOwqFE=";
  };

  nativeBuildInputs = [
    setuptools
    wheel
  ];

  meta = {
    license = lib.licenses.mit;
  };
}
