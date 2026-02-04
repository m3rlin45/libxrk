# DLL vs libxrk Lap Time Comparison Report

Generated: 2024-02-02 (Updated after fix)

## Summary

After fixing the lap parsing logic to prefer LAP messages over GPS detection, libxrk now matches the official AIM DLL **exactly** for files with LAP message data.

## Results

### SFJ file - EXACT MATCH ✓

| Metric | DLL | libxrk | Difference |
|--------|-----|--------|------------|
| Lap Count | 13 | 13 | 0 |
| All lap times | - | - | **0ms** (exact match) |

### 86 file - EXACT MATCH ✓

| Metric | DLL | libxrk | Difference |
|--------|-----|--------|------------|
| Lap Count | 16 | 16 | 0 |
| All lap times | - | - | **0ms** (exact match) |

### test.xrk (aim_official) - GPS FALLBACK

| Metric | DLL | libxrk | Difference |
|--------|-----|--------|------------|
| Lap Count | 11 | 12 | +1 extra lap |

This file has **no valid LAP messages**, so libxrk falls back to GPS-based lap detection. The DLL appears to use a different internal algorithm for this case.

**Note:** This is expected behavior - GPS detection is a fallback for files without explicit lap data. The important fix is that files WITH LAP messages now match exactly.

---

## Fix Applied

### Changes to `src/libxrk/aim_xrk.pyx`

1. **Prefer LAP messages when available** (matches DLL behavior):
   ```python
   # Before: GPS detection was used when GPS data existed
   if lat_ch and lon_ch:
       # GPS-based detection
   else:
       # LAP message parsing

   # After: LAP messages are checked first
   if _tokdec('LAP') in msg_by_type:
       # LAP message parsing (matches DLL)
   elif lat_ch and lon_ch:
       # GPS-based detection (fallback only)
   ```

2. **Normalize lap numbers to 0-based indexing**:
   ```python
   # LAP messages may use 1-based numbering
   # Normalize to 0-based to match DLL output
   if lap_nums:
       min_lap = min(lap_nums)
       lap_nums = [n - min_lap for n in lap_nums]
   ```

### Test Updates

Updated expected lap times in tests to match official DLL values:
- `tests/test_sfj_xrk.py`: Updated 6 lap boundary times (1ms rounding + 99ms last lap)
- `tests/test_86_xrk.py`: Updated several lap boundary times (1ms rounding + 96ms last lap)

---

## Verification Commands

```bash
# Run DLL comparison (requires Wine on Linux)
poetry run python tests/reference_dll/full_comparison.py

# Run all tests
poetry run poe check
```

## Files Modified

| File | Change |
|------|--------|
| `src/libxrk/aim_xrk.pyx` | Prefer LAP messages, normalize lap numbers |
| `tests/test_sfj_xrk.py` | Update expected lap times to match DLL |
| `tests/test_86_xrk.py` | Update expected lap times to match DLL |
