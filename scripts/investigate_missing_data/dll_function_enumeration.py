#!/usr/bin/env python3
"""
DLL Function Enumeration - Lists all DLL functions and identifies gaps.

Compares known DLL functions against our wrapper to find missing functionality.
"""

# Complete list of functions from MatLabXRK.h
DLL_FUNCTIONS = {
    # Metadata Functions
    "get_library_date": {"category": "metadata", "wrapped": False, "purpose": "Get DLL build date"},
    "get_library_time": {"category": "metadata", "wrapped": False, "purpose": "Get DLL build time"},
    # File Management
    "open_file": {"category": "file", "wrapped": False, "purpose": "Open file (ANSI path)"},
    "open_file_w": {"category": "file", "wrapped": True, "purpose": "Open file (Unicode path)"},
    "close_file_n": {"category": "file", "wrapped": False, "purpose": "Close file by name"},
    "close_file_i": {"category": "file", "wrapped": True, "purpose": "Close file by index"},
    # Session Information
    "get_vehicle_name": {"category": "metadata", "wrapped": True, "purpose": "Get vehicle name"},
    "get_track_name": {"category": "metadata", "wrapped": True, "purpose": "Get track/venue name"},
    "get_racer_name": {"category": "metadata", "wrapped": True, "purpose": "Get driver/racer name"},
    "get_championship_name": {
        "category": "metadata",
        "wrapped": True,
        "purpose": "Get championship/series name",
    },
    "get_venue_type_name": {"category": "metadata", "wrapped": True, "purpose": "Get venue type"},
    "get_date_and_time": {
        "category": "metadata",
        "wrapped": False,
        "purpose": "Get session date/time (returns struct tm*)",
    },
    # Lap Functions
    "get_laps_count": {"category": "laps", "wrapped": True, "purpose": "Get number of laps"},
    "get_lap_info": {
        "category": "laps",
        "wrapped": True,
        "purpose": "Get lap start time and duration",
    },
    # Standard Channels - Full session
    "get_channels_count": {"category": "channels", "wrapped": True, "purpose": "Get channel count"},
    "get_channel_name": {"category": "channels", "wrapped": True, "purpose": "Get channel name"},
    "get_channel_units": {"category": "channels", "wrapped": True, "purpose": "Get channel units"},
    "get_channel_samples_count": {
        "category": "channels",
        "wrapped": True,
        "purpose": "Get sample count",
    },
    "get_channel_samples": {
        "category": "channels",
        "wrapped": True,
        "purpose": "Get channel timecodes and values",
    },
    # Standard Channels - Per lap
    "get_lap_channel_samples_count": {
        "category": "channels",
        "wrapped": False,
        "purpose": "Get sample count for specific lap",
    },
    "get_lap_channel_samples": {
        "category": "channels",
        "wrapped": False,
        "purpose": "Get channel data for specific lap",
    },
    # GPS Channels (DERIVED) - Full session
    "get_GPS_channels_count": {
        "category": "gps_derived",
        "wrapped": False,
        "purpose": "Get GPS derived channel count (GPS_InlineAcc, GPS_LateralAcc, GPS_Yaw_Rate, etc.)",
    },
    "get_GPS_channel_name": {
        "category": "gps_derived",
        "wrapped": False,
        "purpose": "Get GPS derived channel name",
    },
    "get_GPS_channel_units": {
        "category": "gps_derived",
        "wrapped": False,
        "purpose": "Get GPS derived channel units",
    },
    "get_GPS_channel_samples_count": {
        "category": "gps_derived",
        "wrapped": False,
        "purpose": "Get GPS derived channel sample count",
    },
    "get_GPS_channel_samples": {
        "category": "gps_derived",
        "wrapped": False,
        "purpose": "Get GPS derived channel data",
    },
    # GPS Channels (DERIVED) - Per lap
    "get_lap_GPS_channel_samples_count": {
        "category": "gps_derived",
        "wrapped": False,
        "purpose": "Get GPS derived sample count for specific lap",
    },
    "get_lap_GPS_channel_samples": {
        "category": "gps_derived",
        "wrapped": False,
        "purpose": "Get GPS derived data for specific lap",
    },
    # GPS Raw Channels - Full session
    "get_GPS_raw_channels_count": {
        "category": "gps_raw",
        "wrapped": False,
        "purpose": "Get raw GPS channel count",
    },
    "get_GPS_raw_channel_name": {
        "category": "gps_raw",
        "wrapped": False,
        "purpose": "Get raw GPS channel name",
    },
    "get_GPS_raw_channel_units": {
        "category": "gps_raw",
        "wrapped": False,
        "purpose": "Get raw GPS channel units",
    },
    "get_GPS_raw_channel_samples_count": {
        "category": "gps_raw",
        "wrapped": False,
        "purpose": "Get raw GPS sample count",
    },
    "get_GPS_raw_channel_samples": {
        "category": "gps_raw",
        "wrapped": False,
        "purpose": "Get raw GPS channel data",
    },
    # GPS Raw Channels - Per lap
    "get_lap_GPS_raw_channel_samples_count": {
        "category": "gps_raw",
        "wrapped": False,
        "purpose": "Get raw GPS sample count for specific lap",
    },
    "get_lap_GPS_raw_channel_samples": {
        "category": "gps_raw",
        "wrapped": False,
        "purpose": "Get raw GPS data for specific lap",
    },
}


def print_enumeration():
    """Print enumeration of all DLL functions."""
    print("=" * 80)
    print("AIM MatLabXRK DLL Function Enumeration")
    print("=" * 80)
    print()

    # Count by category
    categories: dict[str, list[str]] = {}
    for func, info in DLL_FUNCTIONS.items():
        cat = str(info["category"])
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(func)

    wrapped = sum(1 for f in DLL_FUNCTIONS.values() if f["wrapped"])
    unwrapped = len(DLL_FUNCTIONS) - wrapped

    print(f"Total functions: {len(DLL_FUNCTIONS)}")
    print(f"Currently wrapped: {wrapped}")
    print(f"NOT wrapped (missing): {unwrapped}")
    print()

    for cat in ["metadata", "file", "laps", "channels", "gps_derived", "gps_raw"]:
        funcs = categories.get(cat, [])
        print(f"\n{cat.upper()} FUNCTIONS ({len(funcs)}):")
        print("-" * 60)
        for func in funcs:
            info = DLL_FUNCTIONS[func]
            status = "✓ WRAPPED" if info["wrapped"] else "✗ MISSING"
            print(f"  {status:12s} {func}")
            print(f"               └─ {info['purpose']}")

    print("\n" + "=" * 80)
    print("GAP ANALYSIS")
    print("=" * 80)

    print("\n## Critical Missing Functions:")
    print()

    # GPS Derived channels are high value
    print("### GPS Derived Channels (HIGH PRIORITY)")
    print("The DLL exposes computed GPS channels that libxrk doesn't:")
    print("  - GPS_InlineAcc (longitudinal acceleration from GPS)")
    print("  - GPS_LateralAcc (lateral acceleration from GPS)")
    print("  - GPS_Yaw_Rate (rotation rate from GPS heading)")
    print("  - Possibly others")
    print()
    print("Functions needed:")
    for func in categories.get("gps_derived", []):
        if not DLL_FUNCTIONS[func]["wrapped"]:
            print(f"  - {func}")

    print()
    print("### GPS Raw Channels (MEDIUM PRIORITY)")
    print("May expose raw GPS sensor data not in regular GPS messages:")
    print("  - Satellite count, HDOP, fix quality, etc.")
    print()
    print("Functions needed:")
    for func in categories.get("gps_raw", []):
        if not DLL_FUNCTIONS[func]["wrapped"]:
            print(f"  - {func}")

    print()
    print("### Per-Lap Data Functions (LOW PRIORITY)")
    print("Allow querying data for specific laps (useful but we can filter ourselves):")
    for func in categories.get("channels", []):
        if not DLL_FUNCTIONS[func]["wrapped"]:
            print(f"  - {func}")

    print()
    print("### Metadata Functions (LOW PRIORITY)")
    for func in categories.get("metadata", []):
        if not DLL_FUNCTIONS[func]["wrapped"]:
            print(f"  - {func}")


def generate_wrapper_code():
    """Generate Python ctypes wrapper code for missing functions."""
    print("\n" + "=" * 80)
    print("SUGGESTED WRAPPER CODE")
    print("=" * 80)
    print()

    code = '''
# GPS Derived Channel Functions
# Add to _setup_functions() method:

# get_GPS_channels_count(int idx) -> int
self._dll.get_GPS_channels_count.argtypes = [c_int]
self._dll.get_GPS_channels_count.restype = c_int

# get_GPS_channel_name(int idx, int ch) -> const char*
self._dll.get_GPS_channel_name.argtypes = [c_int, c_int]
self._dll.get_GPS_channel_name.restype = c_char_p

# get_GPS_channel_units(int idx, int ch) -> const char*
self._dll.get_GPS_channel_units.argtypes = [c_int, c_int]
self._dll.get_GPS_channel_units.restype = c_char_p

# get_GPS_channel_samples_count(int idx, int ch) -> int
self._dll.get_GPS_channel_samples_count.argtypes = [c_int, c_int]
self._dll.get_GPS_channel_samples_count.restype = c_int

# get_GPS_channel_samples(int idx, int ch, double* times, double* values, int cnt) -> int
self._dll.get_GPS_channel_samples.argtypes = [c_int, c_int, POINTER(c_double), POINTER(c_double), c_int]
self._dll.get_GPS_channel_samples.restype = c_int

# GPS Raw Channel Functions

# get_GPS_raw_channels_count(int idx) -> int
self._dll.get_GPS_raw_channels_count.argtypes = [c_int]
self._dll.get_GPS_raw_channels_count.restype = c_int

# get_GPS_raw_channel_name(int idx, int ch) -> const char*
self._dll.get_GPS_raw_channel_name.argtypes = [c_int, c_int]
self._dll.get_GPS_raw_channel_name.restype = c_char_p

# get_GPS_raw_channel_units(int idx, int ch) -> const char*
self._dll.get_GPS_raw_channel_units.argtypes = [c_int, c_int]
self._dll.get_GPS_raw_channel_units.restype = c_char_p

# get_GPS_raw_channel_samples_count(int idx, int ch) -> int
self._dll.get_GPS_raw_channel_samples_count.argtypes = [c_int, c_int]
self._dll.get_GPS_raw_channel_samples_count.restype = c_int

# get_GPS_raw_channel_samples(int idx, int ch, double* times, double* values, int cnt) -> int
self._dll.get_GPS_raw_channel_samples.argtypes = [c_int, c_int, POINTER(c_double), POINTER(c_double), c_int]
self._dll.get_GPS_raw_channel_samples.restype = c_int

# Metadata functions

# get_date_and_time(int idx) -> struct tm*
# Note: This returns a pointer to a C struct tm, which is complex to handle in ctypes
# self._dll.get_date_and_time.argtypes = [c_int]
# self._dll.get_date_and_time.restype = POINTER(struct_tm)

# get_library_date() -> const char*
self._dll.get_library_date.argtypes = []
self._dll.get_library_date.restype = c_char_p

# get_library_time() -> const char*
self._dll.get_library_time.argtypes = []
self._dll.get_library_time.restype = c_char_p
'''

    print(code)

    methods = '''

# Add these methods to AimDll class:

def get_GPS_channels_count(self, idx: int) -> int:
    """Get number of GPS derived channels."""
    return int(self._dll.get_GPS_channels_count(idx))

def get_GPS_channel_name(self, idx: int, channel: int) -> str:
    """Get GPS derived channel name."""
    name = self._dll.get_GPS_channel_name(idx, channel)
    return name.decode("latin-1") if name else ""

def get_GPS_channel_units(self, idx: int, channel: int) -> str:
    """Get GPS derived channel units."""
    units = self._dll.get_GPS_channel_units(idx, channel)
    return units.decode("latin-1") if units else ""

def get_GPS_channel_samples_count(self, idx: int, channel: int) -> int:
    """Get number of samples in GPS derived channel."""
    return int(self._dll.get_GPS_channel_samples_count(idx, channel))

def get_GPS_channel_samples(self, idx: int, channel: int) -> tuple[list[float], list[float]]:
    """Get GPS derived channel samples."""
    count = self.get_GPS_channel_samples_count(idx, channel)
    if count <= 0:
        return ([], [])
    times = (c_double * count)()
    values = (c_double * count)()
    result = self._dll.get_GPS_channel_samples(idx, channel, times, values, count)
    if result == 0:
        return ([], [])
    return (list(times), list(values))

def get_GPS_raw_channels_count(self, idx: int) -> int:
    """Get number of raw GPS channels."""
    return int(self._dll.get_GPS_raw_channels_count(idx))

def get_GPS_raw_channel_name(self, idx: int, channel: int) -> str:
    """Get raw GPS channel name."""
    name = self._dll.get_GPS_raw_channel_name(idx, channel)
    return name.decode("latin-1") if name else ""

def get_GPS_raw_channel_units(self, idx: int, channel: int) -> str:
    """Get raw GPS channel units."""
    units = self._dll.get_GPS_raw_channel_units(idx, channel)
    return units.decode("latin-1") if units else ""

def get_GPS_raw_channel_samples_count(self, idx: int, channel: int) -> int:
    """Get number of samples in raw GPS channel."""
    return int(self._dll.get_GPS_raw_channel_samples_count(idx, channel))

def get_GPS_raw_channel_samples(self, idx: int, channel: int) -> tuple[list[float], list[float]]:
    """Get raw GPS channel samples."""
    count = self.get_GPS_raw_channel_samples_count(idx, channel)
    if count <= 0:
        return ([], [])
    times = (c_double * count)()
    values = (c_double * count)()
    result = self._dll.get_GPS_raw_channel_samples(idx, channel, times, values, count)
    if result == 0:
        return ([], [])
    return (list(times), list(values))

def get_library_date(self) -> str:
    """Get DLL build date."""
    date = self._dll.get_library_date()
    return date.decode("latin-1") if date else ""

def get_library_time(self) -> str:
    """Get DLL build time."""
    time = self._dll.get_library_time()
    return time.decode("latin-1") if time else ""
'''

    print(methods)


if __name__ == "__main__":
    print_enumeration()
    generate_wrapper_code()
