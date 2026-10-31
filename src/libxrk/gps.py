# Copyright 2024, Scott Smith.  MIT License (see LICENSE).

"""GPS channel names.

GPS processing itself -- decoding the NAV-SOL records into the 14 GPS
channels, the 65533 ms timing fix, the GPS in-lap classification and GPS lap
detection -- is the Rust crate ``crates/libxrk-gps``. Both backends run it:
the Rust parser links the crate, and the Cython parser calls the same code
through ``libxrk._aim_xrk_rs.process_gps``. See ``spec/docs/companion.md``
sections 5-7.
"""

from ._aim_xrk_rs import GPS_CHANNEL_NAMES

__all__ = ["GPS_CHANNEL_NAMES"]
