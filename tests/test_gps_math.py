"""Degenerate GPS geometry is handled explicitly, not through IEEE division by zero.

Two inputs used to make numpy emit RuntimeWarnings from gps.py on real files:
a receiver with no fix reports ECEF (0, 0, 0), the centre of the Earth, where
the geodetic altitude is undefined; and a receiver that repeats a fix gives two
identical consecutive positions, a zero-length segment that cannot cross the
start/finish plane. In find_laps the results were right (NaN altitude, no
crossing) but reached through -1/0 and 0/0; in find_crossing_idx the 0/0
poisoned argmin and the function returned a NaN index. These tests run with
RuntimeWarning promoted to an error.
"""

import warnings

import numpy as np
import pytest

from libxrk import gps

CENTRE = (35.3705, 138.9270)  # Fuji, roughly


@pytest.fixture(autouse=True)
def _runtime_warnings_are_errors():
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        yield


def _circuit(laps: float, radius_m: float = 300.0, speed_mps: float = 30.0, dt_ms: int = 100):
    """A car lapping a circle anticlockwise; the start/finish marker sits at angle 0.

    Returns (XYZ, timecodes, marker, expected_crossing_times_ms).
    """
    n = int(laps * 2 * np.pi * radius_m / (speed_mps * dt_ms / 1000))
    angle = np.arange(n) * (speed_mps * dt_ms / 1000) / radius_m - 0.3  # start before the line
    lat = CENTRE[0] + (radius_m * np.sin(angle)) / 111_320
    lon = CENTRE[1] + (radius_m * np.cos(angle)) / (111_320 * np.cos(np.radians(CENTRE[0])))
    XYZ = np.column_stack(gps.lla2ecef(lat, lon, np.zeros(n)))
    timecodes = np.arange(n) * dt_ms
    marker_lat = CENTRE[0]
    marker_lon = CENTRE[1] + radius_m / (111_320 * np.cos(np.radians(CENTRE[0])))
    crossings = [
        ((k * 2 * np.pi + 0.3) * radius_m / speed_mps) * 1000 for k in range(int(laps) + 1)
    ]
    return XYZ, timecodes, (marker_lat, marker_lon), [c for c in crossings if c < timecodes[-1]]


def test_no_fix_position_has_undefined_altitude():
    lat, lon, alt = gps.ecef2lla(np.array([0.0, 1e6]), np.array([0.0, 2e6]), np.array([0.0, 6e6]))
    assert (lat[0], lon[0]) == (0.0, 0.0)
    assert np.isnan(alt[0])
    assert np.isfinite(alt[1])


def test_ecef_round_trip_still_exact_for_real_positions():
    x, y, z = gps.lla2ecef(35.3705, 138.927, 612.0)
    lat, lon, alt = gps.ecef2lla(np.array([x]), np.array([y]), np.array([z]))
    assert lat[0] == pytest.approx(35.3705, abs=1e-9)
    assert lon[0] == pytest.approx(138.927, abs=1e-9)
    assert alt[0] == pytest.approx(612.0, abs=1e-6)


def test_find_laps_with_a_stopped_car():
    XYZ, timecodes, marker, expected = _circuit(laps=3)
    # The car stops for a second on the far side of the circle: eleven
    # identical fixes, i.e. ten zero-length segments.
    stop = len(XYZ) // 3
    XYZ = np.vstack([XYZ[:stop], np.repeat(XYZ[stop : stop + 1], 10, axis=0), XYZ[stop:]])
    timecodes = np.concatenate(
        [timecodes[:stop], timecodes[stop] + np.arange(1, 11) * 100, timecodes[stop:] + 1000]
    )
    expected = [t if t < timecodes[stop] else t + 1000 for t in expected]

    found = gps.find_laps(XYZ, timecodes, marker)

    assert len(found) == len(expected)
    assert np.allclose(found, expected, atol=100)


def test_find_laps_with_a_car_stopped_on_the_line():
    XYZ, timecodes, marker, expected = _circuit(laps=2)
    # Stop right at the first crossing: the segment is zero-length *and* on the plane.
    on_line = int(np.argmin(np.abs(timecodes - expected[0])))
    XYZ = np.vstack(
        [XYZ[:on_line], np.repeat(XYZ[on_line : on_line + 1], 5, axis=0), XYZ[on_line:]]
    )
    timecodes = np.concatenate(
        [timecodes[:on_line], timecodes[on_line] + np.arange(1, 6) * 100, timecodes[on_line:] + 500]
    )

    found = gps.find_laps(XYZ, timecodes, marker)

    assert len(found) == len(expected)


def test_find_crossing_idx_with_repeated_reference_points():
    XYZ, _, marker, _ = _circuit(laps=1)
    XYZ = np.vstack([XYZ[:50], np.repeat(XYZ[50:51], 4, axis=0), XYZ[50:]])
    idx, dist = gps.find_crossing_idx(XYZ, np.array(marker))
    assert np.isfinite(idx) and np.isfinite(dist)
    assert dist < 1.0
