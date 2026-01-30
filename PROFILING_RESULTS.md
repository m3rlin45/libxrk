# libxrk Performance Profiling Results

## Summary

Profiling identified `get_channels_as_table()` as the main bottleneck. An optimization was implemented that provides **3.8-6.9x speedup**.

## Before/After Comparison

| File | Size | Before | After | Speedup |
|------|------|--------|-------|---------|
| small | 3.0 MB | 0.212s | 0.032s | **6.6x** |
| medium | 7.9 MB | 0.443s | 0.064s | **6.9x** |
| large | 39.8 MB | 1.849s | 0.491s | **3.8x** |

## All Operations (After Optimization)

| Operation | small | medium | large |
|-----------|-------|--------|-------|
| aim_xrk | 0.032s | 0.066s | 0.113s |
| get_channels_as_table | 0.032s | 0.064s | 0.491s |
| resample_to_channel | 0.003s | 0.009s | 0.032s |
| filter_by_lap | 0.004s | 0.005s | 0.018s |

## Root Cause and Fix

### Problem
The original k-way merge used Python's `heapq.merge()` with `to_pylist()`:

```python
# Old implementation (1.45s for large file)
timecode_iterators = [
    channel_table.column("timecodes").to_pylist()  # 0.73s
    for channel_table in self.channels.values()
]
merged = heapq.merge(*timecode_iterators)  # 0.72s combined
unique_timecodes = [k for k, _ in groupby(merged)]
```

### Solution
Replaced with numpy-based approach:

```python
# New implementation (0.21s for large file)
timecode_arrays = [
    channel_table.column("timecodes").to_numpy()  # ~0s
    for channel_table in self.channels.values()
]
union_timecodes = pa.array(
    np.unique(np.concatenate(timecode_arrays)), type=pa.int64()
)
```

### Why This Works
- `to_numpy()` is essentially free (zero-copy from Arrow)
- `np.concatenate()` + `np.unique()` are optimized C implementations
- While O(N log N) vs O(N) theoretically, the practical speedup is **6.5x** due to lower constant factors

## Current Bottleneck (After Optimization)

For the large file, the breakdown is now:
- `np.unique()`: 0.19s (39%)
- `np.interp()` in resampling: 0.22s (45%)
- Other: 0.08s (16%)

The remaining time is dominated by C-optimized numpy operations, leaving limited room for further Python-level optimization.

## Profiling Scripts

- `scripts/profile_libxrk.py` - Main profiling script for all operations
- `scripts/profile_get_channels.py` - Detailed breakdown of `get_channels_as_table()`
- `scripts/profile_resample.py` - Detailed breakdown of `resample_to_channel()`

Run with:
```bash
poetry run python scripts/profile_libxrk.py [--detailed] [--files small medium large]
```
