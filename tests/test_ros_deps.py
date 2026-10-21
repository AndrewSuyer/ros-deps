from __future__ import annotations

import json
from pathlib import Path

import pytest

import ros_deps
from ros_deps import EXIT_ERROR, EXIT_OK, EXIT_UNRESOLVED, main

INDEX = """
ament-cmake ament-lint-auto rclcpp rclpy std-msgs sensor-msgs
geometry-msgs rosidl-default-generators ament-cmake-gtest doxygen-tool
"""


def make_package(root: Path, name: str, deps: str, fmt: int = 3, subdir: str | None = None) -> Path:
    """Write a package.xml declaring ``deps`` (raw XML) to root/<subdir or name>."""
    path = root / (subdir or name)
    path.mkdir(parents=True)
    (path / "package.xml").write_text(
        f"""<?xml version="1.0"?>
<package format="{fmt}">
  <name>{name}</name>
  <version>0.0.0</version>
  <description>Test package</description>
  <maintainer email="test@example.com">Test</maintainer>
  <license>MIT</license>
  {deps}
</package>
"""
    )
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Isolate tests from the ROS environment of the shell running them."""
    for var in ("ROS_DISTRO", "ROS_VERSION", "ROS_PYTHON_VERSION", ros_deps.INDEX_DIR_ENV):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def src(tmp_path):
    path = tmp_path / "src"
    path.mkdir()
    return path


@pytest.fixture
def index_dir(tmp_path, monkeypatch):
    """An index directory with jazzy and humble, set as $ROS_DEPS_INDEX_DIR."""
    path = tmp_path / "index"
    path.mkdir()
    (path / "jazzy").write_text(INDEX)
    (path / "humble").write_text("rclcpp std-msgs")
    monkeypatch.setenv(ros_deps.INDEX_DIR_ENV, str(path))
    return path


def run(capsys, *argv):
    """Run ros-deps, returning (exit status, stdout lines, stderr)."""
    status = main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return status, out.splitlines(), err


# Dependency collection


def test_to_attr_name():
    assert ros_deps.to_attr_name("std_msgs") == "std-msgs"
    assert ros_deps.to_attr_name("python3-numpy") == "python3-numpy"


def test_collects_all_dependency_types(src, capsys):
    make_package(
        src,
        "pkg",
        """
        <buildtool_depend>ament_cmake</buildtool_depend>
        <buildtool_export_depend>buildtool_export</buildtool_export_depend>
        <depend>rclcpp</depend>
        <build_depend>build_only</build_depend>
        <build_export_depend>build_export</build_export_depend>
        <exec_depend>exec_only</exec_depend>
        <test_depend>ament_lint_auto</test_depend>
        <doc_depend>doc_only</doc_depend>
        """,
    )
    status, out, _ = run(capsys, "--no-index", src)
    assert status == EXIT_OK
    assert out == [
        "ament-cmake",
        "ament-lint-auto",
        "build-export",
        "build-only",
        "buildtool-export",
        "doc-only",
        "exec-only",
        "rclcpp",
    ]


def test_format_1_packages(src, capsys):
    make_package(
        src,
        "old",
        """
        <buildtool_depend>catkin</buildtool_depend>
        <build_depend>roscpp</build_depend>
        <run_depend>rospy</run_depend>
        <test_depend>rostest</test_depend>
        """,
        fmt=1,
    )
    _, out, _ = run(capsys, "--no-index", src)
    assert out == ["catkin", "roscpp", "rospy", "rostest"]


def test_duplicates_are_merged(src, capsys):
    make_package(src, "a", "<depend>rclcpp</depend><exec_depend>std_msgs</exec_depend>")
    make_package(src, "b", "<depend>rclcpp</depend><build_depend>std_msgs</build_depend>")
    _, out, _ = run(capsys, "--no-index", src)
    assert out == ["rclcpp", "std-msgs"]


def test_workspace_packages_are_excluded(src, capsys):
    make_package(src, "my_msgs", "<buildtool_depend>rosidl_default_generators</buildtool_depend>")
    make_package(src, "my_node", "<depend>my_msgs</depend><depend>rclpy</depend>")
    _, out, _ = run(capsys, "--no-index", src)
    assert out == ["rclpy", "rosidl-default-generators"]


def test_nested_packages_are_found(src, capsys):
    make_package(src, "inner", "<depend>rclpy</depend>", subdir="repo/group/inner")
    make_package(src, "outer", "<depend>rclcpp</depend>", subdir="repo/outer")
    _, out, err = run(capsys, "--no-index", src)
    assert out == ["rclcpp", "rclpy"]
    assert "2 packages" in err


@pytest.mark.parametrize("marker", ["COLCON_IGNORE", "AMENT_IGNORE", "CATKIN_IGNORE"])
def test_ignore_markers(src, capsys, marker):
    make_package(src, "kept", "<depend>rclcpp</depend>")
    ignored = make_package(src, "ignored", "<depend>rclpy</depend>")
    (ignored / marker).touch()
    _, out, _ = run(capsys, "--no-index", src)
    assert out == ["rclcpp"]


# Conditions


CONDITIONAL = """
    <depend condition="$ROS_VERSION == 2">rclcpp</depend>
    <depend condition="$ROS_VERSION == 1">roscpp</depend>
    <exec_depend condition="$ROS_DISTRO == humble">humble_only</exec_depend>
    <exec_depend condition="$ROS_DISTRO != humble">not_humble</exec_depend>
    <exec_depend condition="$ROS_PYTHON_VERSION == 3">python3-yaml</exec_depend>
"""


def test_conditions_use_ros2_defaults(src, capsys):
    make_package(src, "pkg", CONDITIONAL)
    _, out, _ = run(capsys, "--no-index", src)
    assert out == ["not-humble", "python3-yaml", "rclcpp"]


def test_conditions_use_distro_option(src, capsys, monkeypatch):
    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    make_package(src, "pkg", CONDITIONAL)
    _, out, _ = run(capsys, "--no-index", "--distro", "humble", src)
    assert out == ["humble-only", "python3-yaml", "rclcpp"]


def test_conditions_use_environment(src, capsys, monkeypatch):
    monkeypatch.setenv("ROS_VERSION", "1")
    monkeypatch.setenv("ROS_DISTRO", "humble")
    make_package(src, "pkg", CONDITIONAL)
    _, out, _ = run(capsys, "--no-index", src)
    assert out == ["humble-only", "python3-yaml", "roscpp"]


# Dependency type selection


TYPED = """
    <buildtool_depend>ament_cmake</buildtool_depend>
    <depend>rclcpp</depend>
    <exec_depend>exec_only</exec_depend>
    <test_depend>ament_lint_auto</test_depend>
    <doc_depend>doc_only</doc_depend>
"""


def test_skip(src, capsys):
    make_package(src, "pkg", TYPED)
    _, out, _ = run(capsys, "--no-index", "-s", "test", "--skip", "doc", src)
    assert out == ["ament-cmake", "exec-only", "rclcpp"]


def test_type(src, capsys):
    make_package(src, "pkg", TYPED)
    _, out, _ = run(capsys, "--no-index", "-t", "exec", "--type", "test", src)
    assert out == ["ament-lint-auto", "exec-only", "rclcpp"]


def test_type_and_skip_are_exclusive(src, capsys):
    with pytest.raises(SystemExit) as e:
        main(["-t", "exec", "-s", "test", str(src)])
    assert e.value.code == 2


# Index lookup


def test_index_dir_uses_distro_env(src, capsys, index_dir, monkeypatch):
    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    make_package(
        src, "pkg", "<depend>rclcpp</depend><depend>sensor_msgs</depend><depend>eigen</depend>"
    )
    status, out, _ = run(capsys, src)
    assert status == EXIT_OK
    assert out == [
        "rclcpp",
        "sensor-msgs",
        "# Not in rosPackages.jazzy (find system dependencies in nixpkgs):",
        "#   eigen",
    ]


def test_index_dir_uses_distro_option(src, capsys, index_dir, monkeypatch):
    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    make_package(src, "pkg", "<depend>rclcpp</depend><depend>sensor_msgs</depend>")
    _, out, _ = run(capsys, "--distro", "humble", src)
    assert out == [
        "rclcpp",
        "# Not in rosPackages.humble (find system dependencies in nixpkgs):",
        "#   sensor_msgs",
    ]


def test_index_dir_without_distro_is_an_error(src, capsys, index_dir):
    make_package(src, "pkg", "<depend>rclcpp</depend>")
    status, out, err = run(capsys, src)
    assert status == EXIT_ERROR
    assert out == []
    assert "pass --distro" in err
    assert "humble, jazzy" in err


def test_unknown_distro_is_an_error(src, capsys, index_dir):
    make_package(src, "pkg", "<depend>rclcpp</depend>")
    status, _, err = run(capsys, "--distro", "nonexistent", src)
    assert status == EXIT_ERROR
    assert "unknown ROS distro 'nonexistent'" in err


def test_explicit_index(src, capsys, tmp_path, index_dir):
    index = tmp_path / "custom-index"
    index.write_text("eigen\n")
    make_package(src, "pkg", "<depend>rclcpp</depend><depend>eigen</depend>")
    _, out, _ = run(capsys, "--index", index, src)
    assert out == [
        "eigen",
        "# Not in the index (find system dependencies in nixpkgs):",
        "#   rclcpp",
    ]


def test_missing_index_file_is_an_error(src, capsys, tmp_path):
    make_package(src, "pkg", "<depend>rclcpp</depend>")
    status, _, err = run(capsys, "--index", tmp_path / "missing", src)
    assert status == EXIT_ERROR
    assert "cannot read index" in err


def test_no_index_ignores_index_dir(src, capsys, index_dir):
    make_package(src, "pkg", "<depend>eigen</depend>")
    _, out, err = run(capsys, "--no-index", src)
    assert out == ["eigen"]
    assert "without being checked" not in err


def test_without_index_warns(src, capsys):
    make_package(src, "pkg", "<depend>eigen</depend>")
    status, out, err = run(capsys, src)
    assert status == EXIT_OK
    assert out == ["eigen"]
    assert "no index available" in err


# Output


DEPS = """
    <depend>rclcpp</depend>
    <depend>ament_cmake_gtest</depend>
    <depend>eigen</depend>
"""


def test_annotate(src, capsys, index_dir):
    make_package(src, "a", DEPS)
    make_package(src, "b", "<depend>rclcpp</depend>")
    _, out, _ = run(capsys, "--distro", "jazzy", "--annotate", src)
    assert out == [
        "ament-cmake-gtest  # a",
        "rclcpp             # a, b",
        "# Not in rosPackages.jazzy (find system dependencies in nixpkgs):",
        "#   eigen (a)",
    ]


def test_plain_format(src, capsys, index_dir):
    make_package(src, "a", DEPS)
    _, out, _ = run(capsys, "--distro", "jazzy", "--format", "plain", src)
    assert out == ["ament-cmake-gtest", "rclcpp"]


def test_json_format(src, capsys, index_dir):
    make_package(src, "a", DEPS)
    make_package(src, "b", "<depend>a</depend><depend>rclcpp</depend>")
    status, out, _ = run(capsys, "--distro", "jazzy", "--format", "json", src)
    assert status == EXIT_OK
    assert json.loads("\n".join(out)) == {
        "distro": "jazzy",
        "verified": True,
        "packages": ["a", "b"],
        "resolved": {"ament-cmake-gtest": ["a"], "rclcpp": ["a", "b"]},
        "unresolved": {"eigen": ["a"]},
    }


def test_nix_output_is_valid_list_contents(src, capsys, index_dir):
    """The output pasted between [ and ] must be a list of identifiers."""
    make_package(src, "a", DEPS)
    _, out, _ = run(capsys, "--distro", "jazzy", "--annotate", src)
    for line in out:
        code = line.split("#", 1)[0].strip()
        assert code == "" or code.replace("-", "_").isidentifier()


def test_quiet(src, capsys):
    make_package(src, "pkg", "<depend>rclcpp</depend>")
    _, out, err = run(capsys, "--quiet", src)
    assert out == ["rclcpp"]
    assert err == ""


def test_summary(src, capsys, index_dir):
    make_package(src, "a", DEPS)
    _, _, err = run(capsys, "--distro", "jazzy", src)
    assert f"1 packages in '{src}', 2 dependencies, 1 not found" in err


# Exit status


def test_strict_with_unresolved(src, capsys, index_dir):
    make_package(src, "pkg", "<depend>rclcpp</depend><depend>eigen</depend>")
    status, out, _ = run(capsys, "--distro", "jazzy", "--strict", src)
    assert status == EXIT_UNRESOLVED
    assert out[0] == "rclcpp"


def test_strict_when_all_resolved(src, capsys, index_dir):
    make_package(src, "pkg", "<depend>rclcpp</depend>")
    status, _, _ = run(capsys, "--distro", "jazzy", "--strict", src)
    assert status == EXIT_OK


def test_default_src(tmp_path, capsys, monkeypatch):
    make_package(tmp_path / "src", "pkg", "<depend>rclcpp</depend>")
    monkeypatch.chdir(tmp_path)
    _, out, _ = run(capsys)
    assert out == ["rclcpp"]


def test_missing_src_is_an_error(tmp_path, capsys):
    status, _, err = run(capsys, tmp_path / "missing")
    assert status == EXIT_ERROR
    assert "does not exist" in err


def test_empty_src_is_an_error(src, capsys):
    status, _, err = run(capsys, src)
    assert status == EXIT_ERROR
    assert "no packages" in err


def test_duplicate_package_names_are_an_error(src, capsys):
    make_package(src, "pkg", "<depend>rclcpp</depend>", subdir="one")
    make_package(src, "pkg", "<depend>rclpy</depend>", subdir="two")
    status, _, err = run(capsys, src)
    assert status == EXIT_ERROR
    assert "failed to read packages" in err


def test_invalid_package_is_an_error(src, capsys):
    (src / "broken").mkdir()
    (src / "broken" / "package.xml").write_text("<package format='3'><name>broken</name>")
    status, _, err = run(capsys, src)
    assert status == EXIT_ERROR
    assert "failed to read packages" in err


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert ros_deps.__version__ in capsys.readouterr().out
