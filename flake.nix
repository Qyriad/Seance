{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system: let

      pkgs = import nixpkgs { inherit system; };

      package = pkgs.callPackage ./package.nix {
        python-sed = pkgs.callPackage ./nix/python-sed.nix { };
      };

    in {
      packages.default = package;

      devShells.default = pkgs.mkShell {
        inputsFrom = [
          package
        ];

        packages = with pkgs.python3.pkgs; [
          build
          pip
          pkgs.pyright
        ];
      };

    }) # eachDefaultSystem
  ; # outputs
}
