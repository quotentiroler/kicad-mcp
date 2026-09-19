"""Tests for centralized KiCad CLI detection."""

import os
from unittest import mock

from kicad_mcp.utils.kicad_cli import KiCadCLIManager


def _manager(system):
    m = KiCadCLIManager()
    m._system = system
    return m


def test_windows_versioned_install_paths():
    r"""A real Windows install lives under a version directory.

    KiCad installs to C:\Program Files\KiCad\<version>\bin, not straight
    into KiCad\bin, so a detector that only knows the unversioned path
    finds nothing on a normal machine.
    """
    paths = _manager("Windows")._get_common_installation_paths()
    for version in ("9.0", "8.0", "7.0"):
        want = rf"C:\Program Files\KiCad\{version}\bin\kicad-cli.exe"
        assert want in paths, f"{version} install path missing"


def test_windows_versioned_paths_come_first():
    """A versioned install is the normal case, so it is searched first."""
    paths = _manager("Windows")._get_common_installation_paths()
    versioned = [i for i, p in enumerate(paths) if r"KiCad\9.0" in p]
    plain = [i for i, p in enumerate(paths) if p == r"C:\Program Files\KiCad\bin\kicad-cli.exe"]
    assert versioned and plain, "both shapes should be searched"
    assert min(versioned) < min(plain)


def test_detect_finds_versioned_path_when_not_on_PATH():
    """The whole point: nothing on PATH, a versioned install present."""
    m = _manager("Windows")
    target = r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe"

    with mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch("shutil.which", return_value=None), \
         mock.patch("os.path.isfile", side_effect=lambda p: p == target), \
         mock.patch("os.access", return_value=True):
        assert m._detect_cli_path() == target


def test_env_override_still_wins():
    """KICAD_CLI_PATH is checked before anything else."""
    m = _manager("Linux")
    with mock.patch.dict(os.environ, {"KICAD_CLI_PATH": "/opt/mine/kicad-cli"}), \
         mock.patch("os.path.isfile", return_value=True), \
         mock.patch("os.access", return_value=True):
        assert m._detect_cli_path() == "/opt/mine/kicad-cli"
