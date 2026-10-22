{
  description = "Reach mailboxes over IMAP and SMTP under per-mailbox permissions the agent cannot grant itself";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs, ... }:
    let
      inherit (nixpkgs) lib;
      # systemd holds the secrets, so there is no port to a system without it
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: {
        default = pkgs.callPackage ./nix/package.nix { };
      });

      # The unit carries the isolation the service depends on, so it ships here rather
      # than being written again by every consumer
      nixosModules.default = import ./nix/module.nix { inherit self; };

      # The module's sandbox and a real IMAP and SMTP server exist only in a booted
      # system, so the service is tested in one
      checks = forAllSystems (pkgs: {
        vm = import ./nix/vm-test.nix { inherit self pkgs; };
        vm-private = import ./nix/vm-test.nix {
          inherit self pkgs;
          private = true;
        };
      });

      # The pinned toolbox for check.sh, locally and in CI — a linter looked up from a
      # registry at job time makes the run a test of someone else's mirror
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            actionlint
            # The same binary the formatter output wraps with treefmt. The gate calls it
            # directly, because `nix fmt` needs the flake and a check should not
            nixfmt
            deadnix
            statix
            shellcheck
            shfmt
            jq
            (python3.withPackages (ps: [
              ps.imapclient
              ps.html2text
              ps.regex
              ps.pytest
            ]))
          ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
