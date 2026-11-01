{
  description = "FNIRSI DSO-TC3 Custom Firmware Extension, Host Tools, and Sigrok Driver";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      nixpkgsFor = forAllSystems (system: import nixpkgs { inherit system; });
    in {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgsFor.${system};
          sigrokPkgs = import ./sigrok/default.nix { inherit pkgs; };
          armGcc = pkgs.pkgsCross.arm-embedded.buildPackages.gcc;
        in rec {
          default = pulseview;
          pulseview = sigrokPkgs.pulseview;
          sigrok-cli = sigrokPkgs.sigrok-cli;
          libsigrok = sigrokPkgs.libsigrok;

          firmware = pkgs.stdenv.mkDerivation {
            pname = "dso-tc3-firmware-ext";
            version = "1.1.0";
            src = ./firmware_ext;
            nativeBuildInputs = [ armGcc ];
            buildPhase = ''
              make CROSS_COMPILE=arm-none-eabi-
            '';
            installPhase = ''
              mkdir -p $out/bin
              cp build/extension.bin build/extension.elf build/extension.dis $out/bin/
            '';
          };
        });

      devShells = forAllSystems (system:
        let
          pkgs = nixpkgsFor.${system};
          sigrokPkgs = import ./sigrok/default.nix { inherit pkgs; };
          armGcc = pkgs.pkgsCross.arm-embedded.buildPackages.gcc;
        in {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: with ps; [
                pyserial
                pytest
              ]))
              armGcc
              sigrokPkgs.sigrok-cli
              sigrokPkgs.pulseview
            ];
          };
        });
    };
}
