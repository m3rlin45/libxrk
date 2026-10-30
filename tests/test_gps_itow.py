"""GPS_ITOW / GPS_Week expose the receiver clock from the NAV-SOL records."""

from pathlib import Path

import numpy as np
import pytest

from libxrk import GPS_CHANNEL_NAMES, aim_xrk

TEST_DATA_DIR = Path(__file__).parent / "test_data"
FILES = [
    TEST_DATA_DIR / "aim_official" / "test.xrk",
    TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk",
    TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk",
]
MS_PER_WEEK = 604_800_000


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_itow_and_week_channels(path: Path) -> None:
    log = aim_xrk(str(path), progress=None)

    assert "GPS_ITOW" in GPS_CHANNEL_NAMES and "GPS_Week" in GPS_CHANNEL_NAMES
    itow_tbl = log.channels["GPS_ITOW"]
    week_tbl = log.channels["GPS_Week"]
    itow = itow_tbl.column("GPS_ITOW").to_numpy()
    week = week_tbl.column("GPS_Week").to_numpy()
    tc = itow_tbl.column("timecodes").to_numpy()

    assert itow_tbl.schema.field("GPS_ITOW").metadata[b"units"] == b"ms"
    assert len(itow) == len(week) == len(log.channels["GPS Speed"])
    assert itow.min() >= 0 and itow.max() < MS_PER_WEEK
    assert week.min() > 0

    # The receiver clock and the logger clock tick at the same rate, so the
    # per-sample step agrees (ignoring the weekly wrap and dropouts).
    d_itow = np.diff(itow)
    d_tc = np.diff(tc)
    steady = (d_itow > 0) & (d_tc > 0) & (d_tc < 1000)
    assert steady.mean() > 0.9
    assert abs(np.median(d_itow[steady]) - np.median(d_tc[steady])) <= 2
