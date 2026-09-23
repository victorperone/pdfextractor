# OCR RGB Budget Gate — Crash Fix for Large Recovery Scale Variants

## Background

During validation against a corporate PDF, the extraction process consistently
terminated with Windows exit code `-1073741819`
(`STATUS_ACCESS_VIOLATION`, `0xC0000005`) — a fatal native crash inside
Paddle's C++ inference runtime.  No Python exception was raised; Python's
`faulthandler` captured the call stack immediately before the crash.

## Root Cause

The pipeline uses `OcrRegionRefiner` to recover regions where native text is
absent or of poor quality.  When a region is flagged as `native_text_missing`
or `visible_ink_present`, the refiner applies **three scale factors**
`(1.0×, 1.5×, 2.0×)` to the base crop and sends each variant independently to
PaddleOCR.

For the crashing document, the base crop was **868 × 1 334 px** (already
larger than the detection model's nominal `limit_side_len=960`).  The three
variants therefore produced:

| Scale | Dimensions     | Uncompressed RGB size | Result          |
|-------|----------------|-----------------------|-----------------|
| 1.0×  | 868 × 1 334    | 3.3 MiB               | OK — 39 s       |
| 1.5×  | 1 302 × 2 001  | 7.5 MiB               | OK — 62 s       |
| 2.0×  | 1 736 × 2 668  | 13.3 MiB              | **CRASH**       |

### Why upscaling worsens the crash

PaddleOCR's detection model (`PP-OCRv5_server_det`) caps the **detection
tensor** to `limit_side_len=960 px` on the long side.  All three variants
therefore produce a similar ~625 × 960 detection tensor.

However, after detection the bounding boxes are **un-projected back to the
original (unresized) image** and text crops for the recognition model are
extracted from the full-size source.  Recognition crops from a 1 736 × 2 668
source are proportionally larger than crops from an 868 × 1 334 source.

Instrumentation (process RSS, elapsed time, GC object count) confirmed:

- Elapsed time scaled with **input image size**, not detection tensor size
  (39 s → 62 s, proportional to pixel area).
- Process RSS grew **+21.7 MiB** (1.0×) and **+31.0 MiB** (1.5×) without
  being fully released before the next call.
- After two consecutive large calls the C++ runtime state was degraded enough
  that the third call (13.3 MiB) triggered an access violation.
- System memory pressure was **not a factor**: 21.9 GiB were still available
  when the crash occurred.

## Fix

### 1 — RGB budget gate in `recovery.py`

A configurable upper bound (default **8 MiB**) is applied to the estimated
uncompressed RGB size of each scale variant **before** the variant image is
created or sent to Paddle.

```python
# Override with: PDFEXTRACTOR_OCR_RGB_BUDGET_MIB=<value>
_OCR_RGB_BUDGET_MIB: float = float(
    os.environ.get("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB", "8.0")
)
```

`plan_ocr_scales()` partitions the requested scale factors into **allowed**
and **blocked** lists using the exact same rounding as `resize_image()`:

```python
allowed, blocked = plan_ocr_scales(
    width=868, height=1334,
    scale_factors=(1.0, 1.5, 2.0),
    max_rgb_mib=8.0,
)
# → allowed: [1.0×, 1.5×]
# → blocked: [2.0×]  (13.3 MiB > 8.0 MiB)
```

Blocked variants are **never created** and **never sent to Paddle**.

If even the 1.0× baseline variant exceeds the budget (e.g. a very large base
crop), the region is skipped with an explicit `OCR_SCALE_ALL_BLOCKED` log
entry rather than being silently discarded.  This case requires separate
investigation (region subdivision or per-region downscale) and is not handled
automatically.

### 2 — Detection limit aligned with budget in `paddle.py`

The `PP-OCRv5_server_det` detection model previously capped all inputs to
960 px on the long side, making the 1.0×, 1.5× and 2.0× variants produce
**identical** detection tensors (~625 × 960) regardless of input size.
Upscaling therefore had no effect on detection quality.

The detection limit is now derived from the RGB budget so that variants within
budget are processed at full resolution:

```python
# For the default 8 MiB budget → det_limit_side_len = 2016
det_limit = max(960, round(math.sqrt(budget_bytes / 3) * 1.2 / 32) * 32)
options["text_det_limit_side_len"] = det_limit
```

With `det_limit=2016`:

| Scale | Dimensions    | Detection tensor   |
|-------|---------------|--------------------|
| 1.0×  | 868 × 1 334   | 868 × 1 334 (full) |
| 1.5×  | 1 302 × 2 001 | 1 302 × 2 001 (full) |
| 2.0×  | blocked       | never reached      |

Both variants within budget are now detected at their natural resolution,
making the scale difference meaningful for text detection quality.

## Debug Log Entries

When `PDFEXTRACTOR_OCR_DEBUG_LOG` is set, the following entries are written to
the log file for every recovered region:

```text
REGION_SELECTED  page=<n> bbox=<x0>,<y0>,<x1>,<y1>
                 base_width=<w> base_height=<h>
                 requested_scales=<1.0,1.5,2.0>
                 quality_reasons=[<reason1,reason2>]

OCR_SCALE_PLAN   page=<n> limit_rgb_mib=8.0
                 allowed_scales=<1.0,1.5>
                 blocked_scales=<2.0>

OCR_SCALE_BLOCKED  page=<n> scale=2.0
                   width=1736 height=2668
                   estimated_rgb_mib=13.251
                   reason=rgb_budget_exceeded

OCR_SCALE_ALL_BLOCKED  page=<n> ...  action=recovery_skipped
```

These entries appear in the same file as `CALL_START` / `CALL_END` and
`VERSIONS` entries from `paddle.py` so the full decision chain is visible in
one log.

## Validation

After applying the fix, the previously crashing page (`TC_007315_2023_page_007`)
extracted successfully:

```
ExitCode    : 0
Tempo       : 00:01:56  (was: 00:12:20 → crash)
OutputExiste: True
```

The log confirmed that `scale=2.0` was blocked and no `CALL_START` was emitted
for the 13.3 MiB variant.

## Configuration Reference

| Environment variable              | Default | Purpose                                                    |
|-----------------------------------|---------|------------------------------------------------------------|
| `PDFEXTRACTOR_OCR_RGB_BUDGET_MIB` | `8.0`   | Max uncompressed RGB size (MiB) per OCR variant            |
| `PDFEXTRACTOR_OCR_DEBUG_LOG`      | (unset) | Path to append debug log; omit to disable instrumentation  |

Both variables are read at **module import time** (`recovery.py` and
`paddle.py` respectively) and remain constant for the lifetime of the process.
To change the budget, restart the process with the new value set before import.
