# ros-deps

A `rosdep` replacement for NixOS. `ros-deps` finds the ROS packages in a
workspace, collects the dependencies listed in their `package.xml` files, and
prints them as [nix-ros-overlay](https://github.com/lopsided98/nix-ros-overlay)
attribute names, ready to paste into the `paths` of a
`rosPackages.<distro>.buildEnv` in your `flake.nix`.

```console
$ ros-deps
ament-cmake
ament-lint-auto
rclcpp
sensor-msgs
std-msgs
# Not in rosPackages.jazzy (find system dependencies in nixpkgs):
#   eigen
ros-deps: 3 packages in 'src', 5 dependencies, 1 not found
```

How it works:

- Packages are found with `catkin_pkg`, the library rosdep uses, so folders
  containing `COLCON_IGNORE`, `AMENT_IGNORE` or `CATKIN_IGNORE` are skipped.
- Every dependency tag is read: `depend`, `buildtool_depend`,
  `buildtool_export_depend`, `build_depend`, `build_export_depend`,
  `exec_depend`, `run_depend` (format 1), `test_depend` and `doc_depend`.
- `condition` attributes are evaluated like rosdep does, using `ROS_DISTRO`,
  `ROS_VERSION` (default `2`), `ROS_PYTHON_VERSION` (default `3`) and the rest
  of the environment.
- Dependencies on packages in the workspace itself are left out.
- Names are converted to attribute names (`std_msgs` → `std-msgs`) and checked
  against an index of `rosPackages.<distro>`. Those that are missing, usually
  system dependencies like `eigen` or `python3-numpy`, are listed separately
  as comments so you can find them in nixpkgs.

## Adding it to a project

Add ros-deps as an input of your project's flake. Making its `nix-ros-overlay`
follow yours keeps the index in sync with the ROS packages you actually use.

```nix
{
  inputs = {
    nix-ros-overlay.url = "github:lopsided98/nix-ros-overlay/master";
    nixpkgs.follows = "nix-ros-overlay/nixpkgs";
    ros-deps = {
      url = "github:<owner>/ros-deps";
      inputs.nix-ros-overlay.follows = "nix-ros-overlay";
    };
  };

  outputs = { nixpkgs, nix-ros-overlay, ros-deps, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ nix-ros-overlay.overlays.default ];
      };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          ros-deps.packages.${system}.default
          (pkgs.rosPackages.jazzy.buildEnv {
            paths = [
              # output of `ros-deps`
            ];
          })
        ];
      };
    };
}
```

Alternatively, add `ros-deps.overlays.default` after
`nix-ros-overlay.overlays.default` in your overlays and use `pkgs.ros-deps`.
The index is then built from your own `rosPackages`, whatever its version.

To try it without changing anything:

```sh
nix run github:<owner>/ros-deps -- --distro jazzy path/to/src
```

## Usage

```
ros-deps [options] [src]
```

`src` is the folder to search for packages, recursively. It defaults to `src`.

| Option | Description |
| --- | --- |
| `-d`, `--distro DISTRO` | ROS distro whose index to check against; also sets `ROS_DISTRO` for conditions. Defaults to `$ROS_DISTRO`, which a ROS `buildEnv` dev shell sets. |
| `-t`, `--type TYPE` | Only list dependencies of this type. Repeatable. |
| `-s`, `--skip TYPE` | Leave out dependencies of this type. Repeatable. |
| `-f`, `--format FORMAT` | `nix` (default): names, then unresolved dependencies as comments. `plain`: names only. `json`: everything, including which packages need each dependency. |
| `-a`, `--annotate` | With `--format nix`, add a comment saying which packages need each dependency. |
| `-i`, `--index FILE` | Check against this file of whitespace separated attribute names instead of the built-in index. |
| `--no-index` | List every dependency without checking that it exists. |
| `--strict` | Exit with status 1 if some dependencies are not in the index. |
| `-q`, `--quiet` | Do not print the summary and warnings to stderr. |
| `-V`, `--version` | Print the version. |

Dependency types are `buildtool`, `build`, `exec`, `test` and `doc`. A
`<depend>` counts as `build` and `exec`. Export dependencies count as their
base type.

Examples:

```sh
ros-deps                           # dependencies of ./src
ros-deps -s test -s doc ws/src     # without test and documentation dependencies
ros-deps --annotate                # show who needs what
ros-deps -f plain --strict         # fail if something cannot be resolved
ros-deps -f json | jq .unresolved  # machine readable output
```

### Exit status

| Status | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | `--strict` was given and some dependencies are not in the index. |
| 2 | Invalid usage, missing source folder, no packages found, unreadable `package.xml` or index, or unknown distro. |

### Environment

| Variable | Use |
| --- | --- |
| `ROS_DISTRO` | Default for `--distro`. |
| `ROS_DEPS_INDEX_DIR` | Directory with one index file per distro, named after the distro. Set by the Nix package. |
| `ROS_VERSION`, `ROS_PYTHON_VERSION`, ... | Used by `condition` attributes. |

## Development

```sh
nix develop      # Python with catkin_pkg, pytest and ruff
pytest           # run the tests
ruff check .     # lint
nix flake check  # build the package and run the tests in the Nix sandbox
nix fmt          # format the Nix files
```

The script is a single module, `ros_deps.py`, that only needs `catkin_pkg`,
so it can also be run directly or installed with pip, in which case pass
`--index` or `--no-index`.
