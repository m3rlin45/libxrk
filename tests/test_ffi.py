"""The C ABI (crates/libxrk-ffi) must hand out what the Python LogFile holds.

Loads the ``xrk_ffi`` shared library with ctypes, imports every channel and
the laps table through the Arrow C Data Interface, and compares them with the
Rust backend's ``aim_xrk()`` on every fixture: channel names and order,
schemas including field metadata, bit-exact timecodes and values, laps, and
the metadata document with its key order.

The library is not part of the Python package, so these tests only run when
``LIBXRK_FFI_LIB`` names it (absolute, or relative to the repository root).
``just ffi-test`` builds it and sets the variable.
"""

import ctypes
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterator

import pyarrow as pa
import pytest

from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk

REPO_ROOT = Path(__file__).parent.parent
TEST_DATA_DIR = Path(__file__).parent / "test_data"
FIXTURES = sorted(TEST_DATA_DIR.rglob("*.xr[kz]"))

_LIB_ENV = os.environ.get("LIBXRK_FFI_LIB", "")

pytestmark = pytest.mark.skipif(
    not _LIB_ENV, reason="LIBXRK_FFI_LIB is not set (run `just ffi-test`)"
)

XRK_OK = 0
XRK_ERR_INDEX = 1


class Ffi:
    """The ten functions of the C ABI, with their signatures."""

    def __init__(self, path: Path) -> None:
        lib = ctypes.CDLL(str(path))
        session = ctypes.c_void_p
        string = ctypes.c_void_p  # owned char*: read with string_at, then free
        error = ctypes.POINTER(ctypes.c_void_p)

        def bind(name: str, restype: Any, *argtypes: Any) -> Any:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        self.open_path = bind("xrk_open_path", session, ctypes.c_char_p, error)
        self.open_bytes = bind("xrk_open_bytes", session, ctypes.c_char_p, ctypes.c_size_t, error)
        self.close = bind("xrk_close", None, session)
        self.channel_count = bind("xrk_channel_count", ctypes.c_size_t, session)
        self.channel_name = bind("xrk_channel_name", string, session, ctypes.c_size_t)
        self.metadata_json = bind("xrk_metadata_json", string, session)
        self.channel_stream = bind(
            "xrk_channel_stream", ctypes.c_int, session, ctypes.c_size_t, ctypes.c_void_p
        )
        self.laps_stream = bind("xrk_laps_stream", ctypes.c_int, session, ctypes.c_void_p)
        self.string_free = bind("xrk_string_free", None, string)
        self.version = bind("xrk_version", ctypes.c_char_p)

    def take_string(self, pointer: Any) -> str:
        assert pointer, "expected a string, got NULL"
        try:
            return ctypes.string_at(pointer).decode("utf-8")
        finally:
            self.string_free(pointer)

    def open(self, source: "Path | bytes") -> int:
        error = ctypes.c_void_p()
        if isinstance(source, bytes):
            handle = self.open_bytes(source, len(source), ctypes.byref(error))
        else:
            handle = self.open_path(str(source).encode("utf-8"), ctypes.byref(error))
        if not handle:
            raise ValueError(self.take_string(error.value))
        assert not error.value
        return int(handle)

    def table(self, export: Callable[..., int], *args: Any) -> pa.Table:
        # struct ArrowArrayStream is five pointers; the reader takes it over.
        stream = (ctypes.c_void_p * 5)()
        code = export(*args, ctypes.addressof(stream))
        assert code == XRK_OK, f"export failed with code {code}"
        reader = pa.RecordBatchReader._import_from_c(ctypes.addressof(stream))
        return reader.read_all()


@pytest.fixture(scope="module")
def ffi() -> Ffi:
    path = Path(_LIB_ENV)
    if not path.is_absolute():
        path = REPO_ROOT / path
    assert path.is_file(), f"LIBXRK_FFI_LIB={_LIB_ENV!r}: {path} does not exist"
    return Ffi(path)


@pytest.fixture
def opened(ffi: Ffi) -> Iterator[Callable[["Path | bytes"], int]]:
    """Open sessions that are closed again when the test ends."""
    handles: list[int] = []

    def _open(source: "Path | bytes") -> int:
        handles.append(ffi.open(source))
        return handles[-1]

    yield _open
    for handle in handles:
        ffi.close(handle)


def _assert_same_table(actual: pa.Table, expected: pa.Table, what: str) -> None:
    assert actual.schema.equals(expected.schema, check_metadata=True), (
        f"{what}: schema {actual.schema.to_string(show_field_metadata=True)!r} != "
        f"{expected.schema.to_string(show_field_metadata=True)!r}"
    )
    assert actual.num_rows == expected.num_rows, what
    for name in expected.column_names:
        if pa.types.is_string(expected.schema.field(name).type):
            assert actual.column(name).to_pylist() == expected.column(name).to_pylist(), what
        else:
            # Bytes, not values: NaN payloads and -0.0 must survive too.
            a = actual.column(name).to_numpy()
            e = expected.column(name).to_numpy()
            assert a.dtype == e.dtype and a.tobytes() == e.tobytes(), f"{what}: {name} differs"


def _json_view(value: Any) -> Any:
    """A metadata value as its JSON round trip yields it, keeping dict order."""
    if isinstance(value, dict):
        return [(key, _json_view(item)) for key, item in value.items()]
    if isinstance(value, list):
        return [_json_view(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None  # JSON has no NaN or infinity; the ABI writes null
    return (type(value).__name__, value)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_session_matches_python_logfile(
    ffi: Ffi, opened: Callable[["Path | bytes"], int], fixture: Path
) -> None:
    expected = rust_aim_xrk(str(fixture))
    session = opened(fixture)

    count = ffi.channel_count(session)
    names = [ffi.take_string(ffi.channel_name(session, i)) for i in range(count)]
    assert names == list(expected.channels)

    for index, name in enumerate(names):
        table = ffi.table(ffi.channel_stream, session, index)
        _assert_same_table(table, expected.channels[name], name)

    _assert_same_table(ffi.table(ffi.laps_stream, session), expected.laps, "laps")

    metadata = json.loads(ffi.take_string(ffi.metadata_json(session)))
    assert _json_view(metadata) == _json_view(expected.metadata)


def test_open_bytes_matches_open_path(ffi: Ffi, opened: Callable[["Path | bytes"], int]) -> None:
    fixture = TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrz"
    from_path = opened(fixture)
    from_bytes = opened(fixture.read_bytes())

    count = ffi.channel_count(from_path)
    assert count > 0 and ffi.channel_count(from_bytes) == count
    for index in range(count):
        _assert_same_table(
            ffi.table(ffi.channel_stream, from_bytes, index),
            ffi.table(ffi.channel_stream, from_path, index),
            f"channel {index}",
        )
    assert ffi.take_string(ffi.metadata_json(from_bytes)) == ffi.take_string(
        ffi.metadata_json(from_path)
    )


def test_exported_tables_outlive_the_session(ffi: Ffi) -> None:
    fixture = TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
    expected = rust_aim_xrk(str(fixture))
    session = ffi.open(fixture)
    name = ffi.take_string(ffi.channel_name(session, 0))
    table = ffi.table(ffi.channel_stream, session, 0)
    ffi.close(session)
    _assert_same_table(table, expected.channels[name], name)


def test_errors_come_back_as_strings(ffi: Ffi, opened: Callable[["Path | bytes"], int]) -> None:
    missing = TEST_DATA_DIR / "no such file.xrk"
    with pytest.raises(ValueError, match="no such file.xrk"):
        ffi.open(missing)

    session = opened(TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk")
    count = ffi.channel_count(session)
    assert not ffi.channel_name(session, count)
    stream = (ctypes.c_void_p * 5)()
    assert ffi.channel_stream(session, count, ctypes.addressof(stream)) == XRK_ERR_INDEX
    assert ffi.version().decode("ascii")
