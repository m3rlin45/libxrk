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
# MyChron 5S: one GRP listing 3945 members (72 channels are defined) that no
# G message ever uses; every sample is an S message. See issue #80.
ISSUE80_XRK = TEST_DATA_DIR / "issue80" / "libxrk_issue80_5S_b.xrk"

ALL_XRK_FILES = [
    SFJ_XRK,
    SFJ_0101_XRK,
    SFJ_SUZUKA_XRK,
    FILE_86_XRK,
    AIM_OFFICIAL_XRK,
    ISSUE49_XRK,
    ISSUE80_XRK,
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
    "issue80_parsed",
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
    issue80_parsed,
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
        "issue80_parsed": issue80_parsed,
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
def sfj_0101_backend(backend):
    """Load SFJ_0101_XRK via each compiled backend once per session."""
    return _load_backend(backend, SFJ_0101_XRK)


@pytest.fixture(scope="session")
def sfj_suzuka_backend(backend):
    """Load SFJ_SUZUKA_XRK via each compiled backend once per session."""
    return _load_backend(backend, SFJ_SUZUKA_XRK)


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
def issue80_parsed():
    """Parse the issue80 XRK file once per session (spec parser)."""
    return _spec_parse(ISSUE80_XRK)


@pytest.fixture(scope="session")
def issue80_backend(backend):
    """Load ISSUE80_XRK via each compiled backend once per session."""
    return _load_backend(backend, ISSUE80_XRK)


@pytest.fixture(scope="session")
def issue80_dll():
    """Extract issue80 data from the official AIM DLL."""
    if not _dll_available():
        pytest.skip("AIM DLL or Wine not available")
    return _dll_extract(ISSUE80_XRK)


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


def _run_dll_script(script, xrk_path, *args, timeout=600):
    """Run a `tests/reference_dll/wine_*.py` script on a scratch copy of a file.

    Every call of the AIM DLL from the spec tests goes through here, because the
    DLL cannot be handed the caller's file as it is:

    - Its `open_file` takes an ANSI `char*`, and Wine converts the command line
      with the process locale, so a non-ASCII path (a RaceStudio export with a
      Japanese session name, say) never reaches it intact (issue #124).
    - It WRITES next to its input: handed an .xrz it decompresses a sibling
      .xrk, and it litters `<name>.NNN.tmp` scratch files.

    So the DLL gets a copy named `input<suffix>` (it tells .xrk from .xrz by
    the suffix) in a temporary directory that is removed afterwards, never a
    path under `tests/test_data/`. A file lock keeps Wine processes from
    running concurrently (xdist-safe).

    Args:
        script: File name of the script in `tests/reference_dll/`.
        xrk_path: The XRK/XRZ file to read; it is only copied.
        *args: Further command-line arguments for the script.
        timeout: Seconds to allow the Wine process.

    Returns:
        The JSON object the script printed.

    Raises:
        RuntimeError: The script printed nothing, or printed `{"error": ...}`.
    """
    import filelock

    xrk_path = Path(xrk_path)
    wine_path = "/usr/lib/wine/wine64"
    python_path = str(REFERENCE_DLL_DIR / ".setup" / "python-embed" / "python.exe")
    script_path = "Z:" + str((REFERENCE_DLL_DIR / script).resolve())
    env = {
        "WINELOADER": wine_path,
        "WINEDEBUG": "-all",
        "HOME": str(Path.home()),
        "PATH": "/usr/bin:/bin",
    }
    lock = filelock.FileLock(Path(__file__).parent / ".wine_extract.lock")
    with tempfile.TemporaryDirectory(prefix="libxrk-dll-") as tmp:
        scratch = Path(tmp) / f"input{xrk_path.suffix}"
        if not str(scratch).isascii():
            raise RuntimeError(
                f"The AIM DLL needs an ASCII path and the scratch copy {str(scratch)!r} is not: "
                "set TMPDIR to an ASCII directory, and keep the file's suffix ASCII"
            )
        shutil.copyfile(xrk_path, scratch)
        with lock:
            result = subprocess.run(
                [wine_path, python_path, script_path, "Z:" + str(scratch), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
    # Wine may return nonzero exit even on success; check stdout for JSON
    if not result.stdout.strip():
        raise RuntimeError(f"{script} printed nothing for {xrk_path}: {result.stderr[-2000:]}")
    data = json.loads(result.stdout)
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"{script} failed for {xrk_path}: {data['error']}")
    return data


def _dll_extract(xrk_path):
    """Extract channel data from the official AIM DLL via Wine.

    Returns a dict with 'channels' (list of {name, units, sample_count, ...}),
    'laps', 'metadata' and 'file' (`xrk_path`, not the scratch copy the DLL read).
    """
    data = _run_dll_script("wine_full_extract.py", xrk_path, timeout=120)
    data["file"] = str(xrk_path)
    return data


def _dll_extract_gps(xrk_path):
    """Extract raw + derived GPS channels (with per-sample times) from the AIM DLL.

    `_dll_extract` uses the DLL's normal channel API, which does not cover GPS;
    GPS lives behind get_GPS_channel_* / get_GPS_raw_channel_*. Returns
    {"gps": [...], "gps_raw": [...]} with times in seconds.
    """
    dll_path = "Z:" + str((REFERENCE_DLL_DIR / "MatLabXRK-2017-64-ReleaseU.dll").resolve())
    return _run_dll_script("wine_gps_extract.py", xrk_path, dll_path)


def _dll_channel_samples(xrk_path, channel_name):
    """Every sample of one channel from the AIM DLL.

    Returns {"t_ms": [...], "v": [...]} with times in milliseconds.
    """
    return _run_dll_script("wine_all_samples.py", xrk_path, channel_name)


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
