{
  lib,
  python3,
  fetchFromGitHub,
}: let
  inherit (python3.pkgs)
    buildPythonPackage
    setuptools
    wheel
    hatchling
  ;
in buildPythonPackage {
  pname = "pythonsed";
  version = "v2.1post0";

  src = fetchFromGitHub {
    owner = "fschaeck";
    repo = "PythonSed";
    rev = "d091c35b202959d4c899254b3f34b48ea4c283be";
    hash = "sha256-s3Oq7ox6ol/CEJmKhZgyECapRPA0Se5vBxd0bPlCPDk=";
  };

  format = "pyproject";

  nativeBuildInputs = [
    setuptools
    wheel
  ];

  buildInputs = [
    hatchling
  ];

  meta = {
    license = lib.licenses.mit;
  };
}
