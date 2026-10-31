"""Shared fixtures and path constants for spec tests."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from libxrk._aim_xrk_cy import aim_xrk as cython_aim_xrk
from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk

# Test data paths
TEST_DATA_DIR = Path(__file__).parent.parent.parent / "tests" / "test_data"
REFERENCE_DLL_DIR = Path(__file__).parent.parent.parent / "tests" / "reference_dll"

SFJ_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
SFJ_XRZ = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrz"
SFJ_0101_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk"
SFJ_0101_XRZ = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrz"
SFJ_SUZUKA_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk"
FILE_86_XRK = TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"
FILE_86_XRZ = TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrz"
AIM_OFFICIAL_XRK = TEST_DATA_DIR / "aim_official" / "test.xrk"
ISSUE49_XRK = TEST_DATA_DIR / "issue49" / "badGPSdata.xrk"
ISSUE68_XRZ = TEST_DATA_DIR / "issue68" / "CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz"
# GPS timecode corruption fixture: one replayed 41-record block, which steps the
# logger clock backwards by 1600ms and duplicates 41 iTOW epochs. See issue #84.
ISSUE84_XRZ = TEST_DATA_DIR / "issue84" / "CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz"

ALL_XRK_FILES = [
    SFJ_XRK,
    SFJ_0101_XRK,
    SFJ_SUZUKA_XRK,
    FILE_86_XRK,
    AIM_OFFICIAL_XRK,
    ISSUE49_XRK,
]
ALL_XRZ_FILES = [SFJ_XRZ, SFJ_0101_XRZ, FILE_86_XRZ, ISSUE68_XRZ]
ALL_FILES = ALL_XRK_FILES + ALL_XRZ_FILES


def _spec_parse(path):
    """Parse one fixture with the Construct spec (pure Python, 1-14 s)."""
    from spec.xrk_format import parse_xrk_file

    return parse_xrk_file(str(path))


# Session-scoped parse fixtures, one per distinct file. The three XRZ files
# that are zlib twins of an XRK in this list get no fixture of their own:
# TestParseAllFiles.test_xrk_xrz_equivalence proves they decompress to the
# identical bytes, so everything proven on the XRK parse holds for them.
ALL_PARSED_FIXTURES = [
    "sfj_parsed",
    "sfj_0101_parsed",
    "sfj_suzuka_parsed",
    "file_86_parsed",
    "aim_official_parsed",
    "issue49_parsed",
    "issue68_parsed",
    "issue84_parsed",
]


@pytest.fixture(scope="session", params=ALL_PARSED_FIXTURES, ids=ALL_PARSED_FIXTURES)
def any_parsed(
    request,
    sfj_parsed,
    sfj_0101_parsed,
    sfj_suzuka_parsed,
    file_86_parsed,
    aim_official_parsed,
    issue49_parsed,
    issue68_parsed,
    issue84_parsed,
):
    """Each distinct fixture file's parse, for tests that run over all files."""
    return {
        "sfj_parsed": sfj_parsed,
        "sfj_0101_parsed": sfj_0101_parsed,
        "sfj_suzuka_parsed": sfj_suzuka_parsed,
        "file_86_parsed": file_86_parsed,
        "aim_official_parsed": aim_official_parsed,
        "issue49_parsed": issue49_parsed,
        "issue68_parsed": issue68_parsed,
        "issue84_parsed": issue84_parsed,
    }[request.param]


@pytest.fixture(scope="session")
def sfj_0101_parsed():
    """Parse the SFJ 0101 XRK file once per session."""
    return _spec_parse(SFJ_0101_XRK)


@pytest.fixture(scope="session")
def sfj_suzuka_parsed():
    """Parse the SFJ Suzuka XRK file once per session."""
    return _spec_parse(SFJ_SUZUKA_XRK)


@pytest.fixture(scope="session")
def sfj_parsed():
    """Parse the SFJ XRK file once per session."""
    return _spec_parse(SFJ_XRK)


@pytest.fixture(scope="session")
def file_86_parsed():
    """Parse the 86 XRK file once per session."""
    return _spec_parse(FILE_86_XRK)


@pytest.fixture(scope="session", params=["cython", "rust"], ids=["cython", "rust"])
def backend(request):
    """Both compiled backends. Every spec-vs-backend test runs once per
    backend in a single session, so the parsed fixtures are shared; there
    is no need to re-run the suite under LIBXRK_BACKEND=rust."""
    return request.param


_BACKENDS = {"cython": cython_aim_xrk, "rust": rust_aim_xrk}


def _load_backend(backend, path):
    return _BACKENDS[backend](str(path))


@pytest.fixture(scope="session")
def sfj_backend(backend):
    """Load SFJ_XRK via each compiled backend once per session."""
    return _load_backend(backend, SFJ_XRK)


@pytest.fixture(scope="session")
def file_86_backend(backend):
    """Load FILE_86_XRK via each compiled backend once per session."""
    return _load_backend(backend, FILE_86_XRK)


@pytest.fixture(scope="session")
def issue49_parsed():
    """Parse the issue49 XRK file once per session (spec parser)."""
    return _spec_parse(ISSUE49_XRK)


@pytest.fixture(scope="session")
def issue49_backend(backend):
    """Load ISSUE49_XRK via each compiled backend once per session."""
    return _load_backend(backend, ISSUE49_XRK)


@pytest.fixture(scope="session")
def aim_official_parsed():
    """Parse the AIM official XRK file once per session."""
    return _spec_parse(AIM_OFFICIAL_XRK)


@pytest.fixture(scope="session")
def aim_official_backend(backend):
    """Load AIM_OFFICIAL_XRK via each compiled backend once per session."""
    return _load_backend(backend, AIM_OFFICIAL_XRK)


@pytest.fixture(scope="session")
def issue68_parsed():
    """Parse the issue68 XRZ file once per session (spec parser)."""
    return _spec_parse(ISSUE68_XRZ)


@pytest.fixture(scope="session")
def issue68_cython():
    """Load the issue68 file via Cython parser once per session."""
    return _load_backend("cython", ISSUE68_XRZ)


@pytest.fixture(scope="session")
def issue68_backend(backend):
    """Load ISSUE68_XRZ via each compiled backend once per session."""
    return _load_backend(backend, ISSUE68_XRZ)


@pytest.fixture(scope="session")
def issue68_rust():
    """Load the issue68 file via Rust parser once per session."""
    return rust_aim_xrk(str(ISSUE68_XRZ))


@pytest.fixture(scope="session")
def issue84_parsed():
    """Parse the issue84 XRZ file once per session (spec parser)."""
    return _spec_parse(ISSUE84_XRZ)


@pytest.fixture(scope="session")
def issue84_cython():
    """Load the issue84 file via Cython parser once per session."""
    return _load_backend("cython", ISSUE84_XRZ)


@pytest.fixture(scope="session")
def issue84_backend(backend):
    """Load ISSUE84_XRZ via each compiled backend once per session."""
    return _load_backend(backend, ISSUE84_XRZ)


@pytest.fixture(scope="session")
def issue84_rust():
    """Load the issue84 file via Rust parser once per session."""
    return rust_aim_xrk(str(ISSUE84_XRZ))


@pytest.fixture(scope="session")
def issue68_dll():
    """Extract issue68 data from the official AIM DLL."""
    if not _dll_available():
        pytest.skip("AIM DLL or Wine not available")
    return _dll_extract(ISSUE68_XRZ)


@pytest.fixture(scope="session", params=["sfj", "86"], ids=["sfj", "86"])
def parsed_and_backend(request, sfj_parsed, sfj_backend, file_86_parsed, file_86_backend):
    """Yield (parsed, backend log) pairs for both SFJ and 86 datasets."""
    if request.param == "sfj":
        return sfj_parsed, sfj_backend
    return file_86_parsed, file_86_backend


@pytest.fixture(scope="session", params=["sfj", "86"], ids=["sfj", "86"])
def parsed_only(request, sfj_parsed, file_86_parsed):
    """Yield parsed result for both SFJ and 86 datasets."""
    if request.param == "sfj":
        return sfj_parsed
    return file_86_parsed


@pytest.fixture(scope="session", params=["sfj", "86"], ids=["sfj", "86"])
def parsed_and_dll(request, sfj_parsed, sfj_dll, file_86_parsed, file_86_dll):
    """Yield (parsed, dll_data) pairs for both SFJ and 86 datasets."""
    if request.param == "sfj":
        return sfj_parsed, sfj_dll
    return file_86_parsed, file_86_dll


def _dll_available():
    """Check if Wine and the AIM DLL are available."""
    dll_path = REFERENCE_DLL_DIR / "MatLabXRK-2017-64-ReleaseU.dll"
    wine_path = Path("/usr/lib/wine/wine64")
    python_path = REFERENCE_DLL_DIR / ".setup" / "python-embed" / "python.exe"
    return dll_path.exists() and wine_path.exists() and python_path.exists()


def _dll_extract(xrk_path):
    """Extract channel data from the official AIM DLL via Wine.

    Returns a dict with 'channels' (list of {name, units, sample_count, ...}),
    'laps', and 'metadata'.
    Uses a file lock to prevent concurrent Wine processes (xdist-safe).
    """
    wine_path = "/usr/lib/wine/wine64"
    python_path = str(REFERENCE_DLL_DIR / ".setup" / "python-embed" / "python.exe")
    script_path = "Z:" + str(REFERENCE_DLL_DIR / "wine_full_extract.py").replace("/", "/")
    xrk_wine_path = "Z:" + str(xrk_path)

    env = {
        "WINELOADER": wine_path,
        "WINEDEBUG": "-all",
        "HOME": str(Path.home()),
        "PATH": "/usr/bin:/bin",
    }
    import filelock

    lock = filelock.FileLock(Path(__file__).parent / ".wine_extract.lock")
    with lock:
        result = subprocess.run(
            [wine_path, python_path, script_path, xrk_wine_path],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    # Wine may return nonzero exit even on success; check stdout for JSON
    if not result.stdout.strip():
        raise RuntimeError(f"DLL extraction failed: {result.stderr}")
    return json.loads(result.stdout)


def _dll_extract_gps(xrk_path):
    """Extract raw + derived GPS channels (with per-sample times) from the AIM DLL.

    `_dll_extract` uses the DLL's normal channel API, which does not cover GPS;
    GPS lives behind get_GPS_channel_* / get_GPS_raw_channel_*. Returns
    {"gps": [...], "gps_raw": [...]} with times in seconds.

    The DLL WRITES next to its input: handed an .xrz it decompresses a sibling
    .xrk, and it litters `<name>.NNN.tmp` scratch files. So it is always pointed
    at a copy in a temp directory, never at `tests/test_data/`.
    """
    wine_path = "/usr/lib/wine/wine64"
    python_path = str(REFERENCE_DLL_DIR / ".setup" / "python-embed" / "python.exe")
    script_path = "Z:" + str((REFERENCE_DLL_DIR / "wine_gps_extract.py").resolve())
    dll_path = "Z:" + str((REFERENCE_DLL_DIR / "MatLabXRK-2017-64-ReleaseU.dll").resolve())

    env = {
        "WINELOADER": wine_path,
        "WINEDEBUG": "-all",
        "HOME": str(Path.home()),
        "PATH": "/usr/bin:/bin",
    }
    import filelock

    lock = filelock.FileLock(Path(__file__).parent / ".wine_extract.lock")
    with tempfile.TemporaryDirectory(prefix="libxrk-dll-") as tmp:
        scratch = Path(tmp) / Path(xrk_path).name
        shutil.copy2(xrk_path, scratch)
        with lock:
            result = subprocess.run(
                [wine_path, python_path, script_path, "Z:" + str(scratch), dll_path],
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
            )
    if not result.stdout.strip():
        raise RuntimeError(f"DLL GPS extraction failed: {result.stderr[-2000:]}")
    return json.loads(result.stdout)


@pytest.fixture(scope="session")
def issue84_dll_gps():
    """GPS channels for the issue84 fixture, from the official AIM DLL."""
    if not _dll_available():
        pytest.skip("AIM DLL or Wine not available")
    return _dll_extract_gps(ISSUE84_XRZ)


@pytest.fixture(scope="session")
def aim_official_dll():
    """Extract aim_official data from the official AIM DLL."""
    if not _dll_available():
        pytest.skip("AIM DLL or Wine not available")
    return _dll_extract(AIM_OFFICIAL_XRK)


@pytest.fixture(scope="session")
def sfj_dll():
    """Extract SFJ data from the official AIM DLL."""
    if not _dll_available():
        pytest.skip("AIM DLL or Wine not available")
    return _dll_extract(SFJ_XRK)


@pytest.fixture(scope="session")
def file_86_dll():
    """Extract 86 data from the official AIM DLL."""
    if not _dll_available():
        pytest.skip("AIM DLL or Wine not available")
    return _dll_extract(FILE_86_XRK)
