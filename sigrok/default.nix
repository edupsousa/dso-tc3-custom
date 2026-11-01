# libsigrok, sigrok-cli and PulseView with the fnirsi-dso-tc3 driver (FNIRSI DSO-TC3, extension firmware).
#
#   nix-build sigrok -A sigrok-cli      # then: result/bin/sigrok-cli -d fnirsi-dso-tc3 --scan
#   nix-build sigrok -A pulseview       # rebuilds the Qt application against the patched libsigrok
#
# `pkgs` defaults to <nixpkgs>. Use the same nixpkgs as the rest of the workspace so the libsigrok source matches.
{ pkgs ? import <nixpkgs> { } }:
rec {
  libsigrok = pkgs.libsigrok.overrideAttrs (old: {
    postPatch = (old.postPatch or "") + ''
      sh ${./patch-libsigrok.sh} "$PWD" ${./fnirsi-dso-tc3}
    '';
  });

  sigrok-cli = pkgs.sigrok-cli.override { inherit libsigrok; };

  # PulseView binds no generator (frequency, amplitude, duty) or trigger (level, mode) controls of its own; the patch adds boxes for them.
  pulseview = (pkgs.pulseview.override { inherit libsigrok; }).overrideAttrs (old: {
    patches = (old.patches or [ ]) ++ [ ./pulseview-controls.patch ];
  });
}
