{
  lib,
  python3Packages,
  linkFarm,
  writeText,
  rosPackages ? { },
}:

let
  # ROS distros provided by nix-ros-overlay, skipping helpers like `lib`
  isDistro =
    set:
    let
      result = builtins.tryEval (lib.isAttrs set && set ? buildEnv && set ? ros-core);
    in
    result.success && result.value;

  distros = lib.filterAttrs (_: isDistro) rosPackages;

  # One file per distro listing the attribute names of rosPackages.<distro>.
  # Only names are read, so this does not evaluate or build any ROS package.
  index = linkFarm "ros-deps-index" (
    lib.mapAttrsToList (distro: set: {
      name = distro;
      path = writeText "ros-deps-index-${distro}" (lib.concatLines (builtins.attrNames set));
    }) distros
  );
in
python3Packages.buildPythonApplication {
  pname = "ros-deps";
  version = "0.1.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./pyproject.toml
      ./README.md
      ./ros_deps.py
      ./tests
    ];
  };

  build-system = [ python3Packages.setuptools ];
  dependencies = [ python3Packages.catkin-pkg ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  pythonImportsCheck = [ "ros_deps" ];

  makeWrapperArgs = lib.optionals (distros != { }) [
    "--set-default"
    "ROS_DEPS_INDEX_DIR"
    "${index}"
  ];

  passthru = {
    inherit index;
    distros = builtins.attrNames distros;
  };

  meta = {
    description = "List ROS workspace dependencies as nix-ros-overlay package names (a rosdep replacement for NixOS)";
    mainProgram = "ros-deps";
    platforms = lib.platforms.all;
  };
}
