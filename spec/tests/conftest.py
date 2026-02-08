"""Shared fixtures and path constants for spec tests."""

from pathlib import Path

import pytest

# Test data paths
TEST_DATA_DIR = Path(__file__).parent.parent.parent / "tests" / "test_data"

SFJ_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
SFJ_XRZ = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrz"
SFJ_0101_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk"
SFJ_0101_XRZ = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrz"
SFJ_SUZUKA_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk"
FILE_86_XRK = TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"
FILE_86_XRZ = TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrz"
AIM_OFFICIAL_XRK = TEST_DATA_DIR / "aim_official" / "test.xrk"

ALL_XRK_FILES = [
    SFJ_XRK,
    SFJ_0101_XRK,
    SFJ_SUZUKA_XRK,
    FILE_86_XRK,
    AIM_OFFICIAL_XRK,
]
ALL_XRZ_FILES = [SFJ_XRZ, SFJ_0101_XRZ, FILE_86_XRZ]
ALL_FILES = ALL_XRK_FILES + ALL_XRZ_FILES


@pytest.fixture(scope="session")
def sfj_parsed():
    """Parse the SFJ XRK file once per session."""
    from spec.xrk_format import parse_xrk_file

    return parse_xrk_file(str(SFJ_XRK))


@pytest.fixture(scope="session")
def file_86_parsed():
    """Parse the 86 XRK file once per session."""
    from spec.xrk_format import parse_xrk_file

    return parse_xrk_file(str(FILE_86_XRK))


@pytest.fixture(scope="session")
def sfj_cython():
    """Load the SFJ file via Cython parser once per session."""
    from libxrk import aim_xrk

    return aim_xrk(str(SFJ_XRK))


@pytest.fixture(scope="session")
def file_86_cython():
    """Load the 86 file via Cython parser once per session."""
    from libxrk import aim_xrk

    return aim_xrk(str(FILE_86_XRK))
