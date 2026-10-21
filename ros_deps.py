"""List the dependencies of a ROS workspace as nix-ros-overlay package names.

``ros-deps`` is a small replacement for ``rosdep`` on NixOS. It searches a
source folder for ROS packages (directories containing a ``package.xml``),
collects every dependency they declare, and prints the matching attribute
names from ``rosPackages.<distro>`` in nix-ros-overlay, ready to be pasted
into the ``paths`` of a ``buildEnv`` in a ``flake.nix`` development shell.

Package discovery and ``condition`` evaluation are handled by ``catkin_pkg``,
the same library rosdep uses, so ``COLCON_IGNORE``, ``AMENT_IGNORE`` and
``CATKIN_IGNORE`` markers and conditional dependencies such as
``<depend condition="$ROS_VERSION == 2">`` behave as they do with rosdep.

Dependencies are checked against an index of the attribute names available in
``rosPackages.<distro>``. The Nix package ships one index file per distro and
points ``ROS_DEPS_INDEX_DIR`` at them; ``--index`` selects a file directly.
Dependencies that are not in the index are reported separately: these are
usually system dependencies (``eigen``, ``python3-numpy``, ...) that have to
be found in nixpkgs by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from catkin_pkg.packages import find_packages

__version__ = "0.1.0"

#: Environment variable naming a directory with one index file per distro.
INDEX_DIR_ENV = "ROS_DEPS_INDEX_DIR"

#: Dependency types accepted by ``--type``/``--skip``, mapped to the
#: ``catkin_pkg.package.Package`` attributes holding them. ``<depend>`` adds
#: to build, build_export and exec; ``<run_depend>`` (format 1) to
#: build_export and exec.
DEPENDENCY_TYPES: dict[str, tuple[str, ...]] = {
    "buildtool": ("buildtool_depends", "buildtool_export_depends"),
    "build": ("build_depends", "build_export_depends"),
    "exec": ("exec_depends",),
    "test": ("test_depends",),
    "doc": ("doc_depends",),
}

#: Values used when evaluating ``condition`` attributes, unless overridden by
#: the environment (or ``--distro`` for ``ROS_DISTRO``).
DEFAULT_CONDITION_ENV = {"ROS_VERSION": "2", "ROS_PYTHON_VERSION": "3"}

EXIT_OK = 0
EXIT_UNRESOLVED = 1
EXIT_ERROR = 2


class RosDepsError(Exception):
    """An error caused by the user's input, reported without a traceback."""


@dataclass
class Report:
    """The dependencies of a workspace, split by whether they were found."""

    #: Names of the packages found in the source folder.
    packages: list[str]
    #: Attribute name -> names of the workspace packages depending on it.
    resolved: dict[str, list[str]] = field(default_factory=dict)
    #: rosdep key -> names of the workspace packages depending on it.
    unresolved: dict[str, list[str]] = field(default_factory=dict)
    #: Whether ``resolved`` was checked against an index.
    verified: bool = True


def to_attr_name(key: str) -> str:
    """Convert a rosdep key to its nix-ros-overlay attribute name."""
    return key.replace("_", "-")


def condition_env(distro: str | None, environ: Mapping[str, str]) -> dict[str, str]:
    """Build the variables used to evaluate ``condition`` attributes."""
    env = dict(DEFAULT_CONDITION_ENV)
    env.update(environ)
    if distro:
        env["ROS_DISTRO"] = distro
    return env


def collect_dependencies(
    src: Path,
    types: Iterable[str],
    environ: Mapping[str, str],
) -> tuple[list[str], dict[str, set[str]]]:
    """Find the packages under ``src`` and the dependencies they declare.

    Returns the names of the packages found, and a mapping from each rosdep
    key to the packages depending on it. Dependencies on packages that are in
    the workspace themselves and dependencies whose condition evaluates to
    false are left out.
    """
    try:
        packages = list(find_packages(str(src)).values())
    except Exception as e:  # catkin_pkg raises bare RuntimeErrors and InvalidPackage
        raise RosDepsError(f"failed to read packages in '{src}': {e}") from e

    local = {pkg.name for pkg in packages}
    fields = [f for t in types for f in DEPENDENCY_TYPES[t]]

    dependencies: dict[str, set[str]] = {}
    for pkg in packages:
        pkg.evaluate_conditions(environ)
        for f in fields:
            for dep in getattr(pkg, f):
                if dep.evaluated_condition is False or dep.name in local:
                    continue
                dependencies.setdefault(dep.name, set()).add(pkg.name)

    return sorted(local), dependencies


def load_index(path: Path) -> set[str]:
    """Read an index file: whitespace separated attribute names."""
    try:
        return set(path.read_text().split())
    except OSError as e:
        raise RosDepsError(f"cannot read index '{path}': {e.strerror}") from e


def find_index(index: Path | None, distro: str | None, environ: Mapping[str, str]) -> Path | None:
    """Pick the index to check dependencies against, if there is one.

    An explicit ``index`` wins. Otherwise the file named after the distro in
    ``$ROS_DEPS_INDEX_DIR`` is used. Returns None if neither is available.
    """
    if index is not None:
        return index

    index_dir = environ.get(INDEX_DIR_ENV)
    if not index_dir:
        return None

    try:
        available = sorted(p.name for p in Path(index_dir).iterdir() if p.is_file())
    except OSError as e:
        raise RosDepsError(f"cannot read {INDEX_DIR_ENV} '{index_dir}': {e.strerror}") from e
    if not distro:
        raise RosDepsError(
            "cannot tell which ROS distro to use: pass --distro or set ROS_DISTRO "
            f"(available: {', '.join(available)})"
        )
    if distro not in available:
        raise RosDepsError(f"unknown ROS distro '{distro}' (available: {', '.join(available)})")
    return Path(index_dir) / distro


def build_report(
    packages: list[str],
    dependencies: Mapping[str, set[str]],
    index: set[str] | None,
) -> Report:
    """Resolve rosdep keys to attribute names, using ``index`` if given."""
    report = Report(packages=packages, verified=index is not None)
    for key in sorted(dependencies):
        dependents = sorted(dependencies[key])
        attr = to_attr_name(key)
        if index is None or attr in index:
            # Distinct keys can map to the same name (foo_bar and foo-bar)
            merged = set(report.resolved.get(attr, [])) | set(dependents)
            report.resolved[attr] = sorted(merged)
        else:
            report.unresolved[key] = dependents
    return report


def format_nix(report: Report, annotate: bool, distro: str | None) -> str:
    """Format a report as lines that can be pasted into a Nix list."""
    width = max(map(len, report.resolved), default=0)
    lines = []
    for attr, dependents in report.resolved.items():
        if annotate:
            lines.append(f"{attr:<{width}}  # {', '.join(dependents)}")
        else:
            lines.append(attr)

    if report.unresolved:
        where = f"rosPackages.{distro}" if distro else "the index"
        lines.append(f"# Not in {where} (find system dependencies in nixpkgs):")
        for key, dependents in report.unresolved.items():
            suffix = f" ({', '.join(dependents)})" if annotate else ""
            lines.append(f"#   {key}{suffix}")
    return "\n".join(lines)


def format_plain(report: Report) -> str:
    """Format a report as the bare attribute names, one per line."""
    return "\n".join(report.resolved)


def format_json(report: Report, distro: str | None) -> str:
    """Format a report as a JSON object."""
    return json.dumps(
        {
            "distro": distro,
            "verified": report.verified,
            "packages": report.packages,
            "resolved": report.resolved,
            "unresolved": report.unresolved,
        },
        indent=2,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="ros-deps",
        description=(
            "List the dependencies declared in the package.xml files of a ROS "
            "workspace as nix-ros-overlay attribute names, ready to paste into "
            "the `paths` of a rosPackages.<distro>.buildEnv. A rosdep "
            "replacement for NixOS."
        ),
        epilog=(
            f"environment: ROS_DISTRO is the default for --distro; {INDEX_DIR_ENV} "
            "is a directory holding one index file per distro (set by the Nix "
            "package); ROS_VERSION (default 2), ROS_PYTHON_VERSION (default 3) "
            "and any other variable can be used by `condition` attributes. "
            "exit status: 0 on success, 1 if --strict is given and some "
            "dependencies are not in the index, 2 on errors."
        ),
    )
    parser.add_argument(
        "src",
        nargs="?",
        default=Path("src"),
        type=Path,
        help="folder to search for packages, recursively (default: %(default)s)",
    )
    parser.add_argument(
        "-d",
        "--distro",
        help=(
            "ROS distro whose index to check dependencies against; also sets "
            "ROS_DISTRO for `condition` attributes (default: $ROS_DISTRO)"
        ),
    )

    types = parser.add_mutually_exclusive_group()
    types.add_argument(
        "-t",
        "--type",
        dest="types",
        action="append",
        choices=list(DEPENDENCY_TYPES),
        help="only list dependencies of this type (repeatable; default: all)",
    )
    types.add_argument(
        "-s",
        "--skip",
        action="append",
        default=[],
        choices=list(DEPENDENCY_TYPES),
        help="leave out dependencies of this type (repeatable), e.g. -s test -s doc",
    )

    parser.add_argument(
        "-f",
        "--format",
        choices=["nix", "plain", "json"],
        default="nix",
        help=(
            "output format: `nix` lists names followed by unresolved dependencies "
            "as comments, `plain` only lists the names, `json` gives every detail "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "-a",
        "--annotate",
        action="store_true",
        help="with --format nix, add a comment saying which packages need each dependency",
    )

    index = parser.add_mutually_exclusive_group()
    index.add_argument(
        "-i",
        "--index",
        type=Path,
        help=(
            "file of available attribute names, separated by whitespace "
            "(default: $ROS_DEPS_INDEX_DIR/<distro>)"
        ),
    )
    index.add_argument(
        "--no-index",
        action="store_true",
        help="list every dependency without checking that it exists",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with status 1 if some dependencies are not in the index",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="do not print the summary and warnings to stderr",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """Run ros-deps with parsed arguments, returning the exit status."""

    def note(message: str) -> None:
        if not args.quiet:
            print(f"ros-deps: {message}", file=sys.stderr)

    if not args.src.is_dir():
        raise RosDepsError(f"source folder '{args.src}' does not exist")

    distro = args.distro or environ.get("ROS_DISTRO") or None
    types = args.types or [t for t in DEPENDENCY_TYPES if t not in args.skip]

    packages, dependencies = collect_dependencies(args.src, types, condition_env(distro, environ))
    if not packages:
        raise RosDepsError(f"no packages (package.xml files) found in '{args.src}'")

    index_path = None if args.no_index else find_index(args.index, distro, environ)
    if index_path is None and not args.no_index:
        note("no index available, dependencies are listed without being checked")
    index = load_index(index_path) if index_path is not None else None

    report = build_report(packages, dependencies, index)

    if args.format == "nix":
        output = format_nix(report, args.annotate, distro)
    elif args.format == "plain":
        output = format_plain(report)
    else:
        output = format_json(report, distro)
    if output:
        print(output)

    note(
        f"{len(packages)} packages in '{args.src}', {len(report.resolved)} dependencies"
        + (f", {len(report.unresolved)} not found" if report.unresolved else "")
    )
    if args.strict and report.unresolved:
        return EXIT_UNRESOLVED
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``ros-deps`` command."""
    args = build_parser().parse_args(argv)
    try:
        return run(args, os.environ)
    except RosDepsError as e:
        print(f"ros-deps: error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
