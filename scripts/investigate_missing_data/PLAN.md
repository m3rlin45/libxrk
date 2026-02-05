# Plan: Investigate Missing Data in XRK Files

## Objective
Discover all data in XRK files that libxrk is NOT currently extracting, understand what it is, and determine how to use it.

---

## Phase 1: DLL API Gap Analysis

**Goal:** Find all DLL functions we're not calling and data we're missing.

### 1.1 Download Full DLL Header
- Fetch `MatLabXRK.h` from AIM's DLL package to enumerate ALL exported functions
- Current wrapper (`tests/reference_dll/aim_dll_wrapper.py`) only covers ~15 functions
- AIM documentation suggests GPS-specific functions exist (satellite data, computed channels, accuracy metrics)

### 1.2 Enumerate DLL Exports
- Use `objdump -T` or `nm` on the DLL to list all exported symbols
- Create comprehensive function inventory
- Identify functions not in current wrapper

### 1.3 Expand DLL Wrapper
- Add wrappers for ALL DLL functions
- Run against test files and capture all outputs
- Compare DLL channels vs libxrk channels to find gaps

**Files to modify:**
- `tests/reference_dll/aim_dll_wrapper.py` - add missing function wrappers

---

## Phase 2: Raw Binary Message Discovery

**Goal:** Find all message types in XRK files, including those we ignore.

### 2.1 Build Message Scanner Tool
Create `scripts/xrk_message_scanner.py` that:
- Scans raw XRK bytes
- Catalogs ALL message tokens encountered (not just known ones)
- Reports frequency, size distribution, and sample content for each
- Identifies unknown/unhandled tokens

### 2.2 Known Gaps to Investigate
From `src/libxrk/aim_xrk.pyx`:

| Token | Line | Current Handling | Gap |
|-------|------|------------------|-----|
| `CDE` | 414 | Converted to hex string | **Content not interpreted** |
| `ENF` | 476 | Recursively decoded | **Content discarded** |
| `SRC` | 462-474 | Only extracts `idn` | **Rest ignored** |
| `ODO` | 481-490 | Fuel entries skipped | **Fuel mapping unknown** |

### 2.3 Byte Coverage Analysis
- Track bytes consumed vs file size
- Identify any regions we're skipping entirely

---

## Phase 3: Channel Decoder Investigation

**Goal:** Find channels being silently dropped due to unknown decoder types.

### 3.1 Log Unknown Decoder Types
Modify parser to log when `c.unknown[20]` is not in `_decoders` (line 553-556)
- Currently these channels are silently skipped
- Need to know which channels are affected and their decoder values

### 3.2 Current Decoder Registry
Known types (from line 104-120):
```
0, 1, 3, 4, 6, 11, 12, 13, 15, 20, 24
```
Any other value causes channel to be dropped.

### 3.3 Cross-Reference with DLL
- Get all channel names from DLL
- Compare against libxrk channels
- For missing channels, investigate decoder type

---

## Phase 4: Channel Metadata Analysis

**Goal:** Understand the 96 unknown bytes in channel definitions.

### 4.1 Current Known Offsets
From CHS message (128 bytes total):
- Offset 0-1: Channel index
- Offset 24-31: Short name
- Offset 32-55: Long name
- Offset 72: Unit type (maps to `_unit_map`)
- Offset 20 or 84: Decoder type
- Offset 64: Sample rate
- **96 bytes unknown**

### 4.2 Build Metadata Analyzer
Create tool to:
- Dump all 128 bytes for all channels across multiple files
- Cluster analysis to find patterns
- Correlate unknown bytes with channel properties

---

## Phase 5: Specific Data Gaps

### 5.1 Fuel Data (High Value)
From line 481-490:
```python
# Fuel Used channel claims 8.56l used (2046.0-2037.4)
# Fuel Used odo says 70689
# not sure how to map fuel
```
- Collect fuel ODO entries across files
- Correlate with Fuel Used channel values
- Determine conversion formula

### 5.2 GPS Derived Channels
DLL exposes channels libxrk doesn't:
- `GPS_InlineAcc`, `GPS_LateralAcc`, `GPS_Yaw_Rate`
- These may be derived from raw GPS data we already have
- Or may require additional parsing

### 5.3 500 Hz Sample Rate
Line 231 comment: "Not sure how to represent 500 Hz"
- Find files with 500 Hz channels
- Determine M message encoding for this rate

---

## Implementation Steps

1. **Create `scripts/investigate_missing_data/` directory**

2. **Build scanner tool** - `message_scanner.py`
   - Parse XRK without extracting, just catalog messages
   - Output: JSON with all tokens, frequencies, unknown tokens

3. **Build DLL enumeration** - `dll_function_enumeration.py`
   - List all DLL exports
   - Identify unwrapped functions

4. **Build comparison tool** - `channel_comparison.py`
   - DLL channels vs libxrk channels
   - Identify missing channels

5. **Add decoder logging** - temporary modification to `aim_xrk.pyx`
   - Log skipped channels and their decoder types
   - Remove after investigation

6. **Analyze results**
   - Compile list of missing data
   - Prioritize by user value
   - Implement extraction for high-value gaps

---

## Verification

- Run message scanner on all test files
- Compare DLL vs libxrk channel lists
- Document all discovered gaps with examples
- Create issues/tasks for implementing missing features

---

## Critical Files

| File | Purpose |
|------|---------|
| `src/libxrk/aim_xrk.pyx` | Core parser - all message handling |
| `tests/reference_dll/aim_dll_wrapper.py` | DLL wrapper to expand |
| `tests/test_data/86/*.xrk` | Most complex test file (92 channels) |
| `tests/reference_dll/COMPARISON_REPORT.md` | Current known differences |
