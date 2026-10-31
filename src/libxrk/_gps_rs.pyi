"""Type stubs for the _gps_rs extension module (crate crates/libxrk-gps-python).

The shared GPS pipeline: see ``libxrk.gps.process`` for the usual entry
point and ``spec/docs/companion.md`` sections 5-7 for the algorithms.
"""

from typing import Optional

import pyarrow as pa

GPS_CHANNEL_NAMES: tuple[str, ...]
GPS_RECORD_SIZE: int
MARKER_SIZE_M: float

LapRow = tuple[int, int, int, str]

def process(
    gps_data: bytes,
    gnfi_data: bytes,
    time_offset: int,
    non_gps_max_end_time: Optional[int],
    start_finish: Optional[tuple[float, float]],
    laps: list[LapRow],
    expected_dt_ms: float = 40.0,
) -> tuple[dict[str, pa.Table], list[LapRow]]:
    """Decode GPS channels, apply the timing fix and finish the lap table."""
    ...

def decode(gps_data: bytes, time_offset: int) -> dict[str, pa.Table]:
    """Decode GPS records into the 14 GPS channels, without the timing fix."""
    ...

def reconstruct_timecodes(timecodes: list[int]) -> list[int]:
    """Rebuild GPS timecodes whose upper 16 bits are unreliable."""
    ...

def detect_gap_corrections(
    gps_timecodes: list[int],
    gnfi_timecodes: list[int],
    non_gps_max_end_time: Optional[int] = None,
    expected_dt_ms: float = 40.0,
) -> list[tuple[int, int]]:
    """Detect the 65533 ms GPS timing bug; returns (gap_time, correction) pairs."""
    ...

def find_laps(
    latitude: list[float],
    longitude: list[float],
    timecodes: list[int],
    start_finish: tuple[float, float],
) -> list[float]:
    """Start/finish crossing times [ms] of a GPS track."""
    ...

def ecef2lla(x: float, y: float, z: float) -> tuple[float, float, float]: ...
def lla2ecef(lat: float, lon: float, alt: float) -> tuple[float, float, float]: ...
