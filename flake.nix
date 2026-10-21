{
  description = "ros-deps: list ROS workspace dependencies as nix-ros-overlay package names";

  inputs = {
    nix-ros-overlay.url = "github:lopsided98/nix-ros-overlay/master";
    nixpkgs.follows = "nix-ros-overlay/nixpkgs";
  };

  outputs =
    {
      self,
      nixpkgs,
      nix-ros-overlay,
    }:
    let
      inherit (nixpkgs) lib;

      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];

      forAllSystems =
        f:
        lib.genAttrs systems (
          system:
          f (
            import nixpkgs {
              inherit system;
              overlays = [
                nix-ros-overlay.overlays.default
                self.overlays.default
              ];
            }
          )
        );
    in
    {
      # Adds `ros-deps` to a package set. Apply it after nix-ros-overlay so the
      # index matches the rosPackages of that package set.
      overlays.default = final: prev: {
        ros-deps = final.callPackage ./package.nix { };
      };

      packages = forAllSystems (pkgs: {
        default = pkgs.ros-deps;
        inherit (pkgs) ros-deps;
      });

      # The test suite runs in the package's check phase
      checks = forAllSystems (pkgs: {
        inherit (pkgs) ros-deps;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          inputsFrom = [ pkgs.ros-deps ];
          packages = [
            pkgs.python3Packages.pytest
            pkgs.ruff
          ];
          ROS_DEPS_INDEX_DIR = pkgs.ros-deps.index;
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };

  nixConfig = {
    extra-substituters = [ "https://ros.cachix.org" ];
    extra-trusted-public-keys = [ "ros.cachix.org-1:dSyZxI8geDCJrwgvCOHDoAfOm5sV1wCPjBkKL+38Rvo=" ];
  };
}
