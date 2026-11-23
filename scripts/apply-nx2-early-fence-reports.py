#!/usr/bin/env python3
"""NX2_FENCE_REPORTS line every 10 s, and EDEN_NX2_EARLY_FENCE_REPORTS (test switch, off).

A fence-flagged query report (semaphore release or counter report with the fence bit) is written
only after the host GPU finished the work before it (fence_manager.h delays such fences even at low
GPU accuracy, for Ocarina's white flashes). If the game's CPU waits on one each frame, the game can
never run ahead of the GPU: on the Odin no stage is saturated (guest cores 17-28 %, GPU back 49 %,
GPU ~72 % busy) at ~14-17 fps.

EDEN_NX2_EARLY_FENCE_REPORTS=1 drops the fence bit from payload reports (their value is known when
sent; written at once like other payload releases at low accuracy); =2 from every report (counters
then follow the non-fence path). Measures the ceiling; expect visual errors with 2."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/video_core/query_cache/query_cache.h"
t = f.read_text()
if "EDEN_NX2_EARLY_FENCE_REPORTS" in t:
    print("query_cache.h: already patched")
    sys.exit(0)
old = """    const bool is_fence = True(flags & QueryPropertiesFlags::IsAFence);
    const bool mirrored = True(flags & QueryPropertiesFlags::Nx2Mirror);"""
new = """    // scripts/apply-nx2-early-fence-reports.py
    static const int nx2_early_fence_reports = [] {
        const char* const value = std::getenv("EDEN_NX2_EARLY_FENCE_REPORTS");
        return value != nullptr ? std::atoi(value) : 0;
    }();
    static std::atomic<u64> nx2_fenced_payload{}, nx2_fenced_counter{}, nx2_unfenced{};
    static std::atomic<s64> nx2_fence_report_at{};
    const bool fence_bit = True(flags & QueryPropertiesFlags::IsAFence);
    (fence_bit ? (counter_type == QueryType::Payload ? nx2_fenced_payload : nx2_fenced_counter)
               : nx2_unfenced)
        .fetch_add(1, std::memory_order_relaxed);
    if (const s64 now = std::chrono::duration_cast<std::chrono::seconds>(
                            std::chrono::steady_clock::now().time_since_epoch())
                            .count();
        now - nx2_fence_report_at.load(std::memory_order_relaxed) >= 10) {
        nx2_fence_report_at.store(now, std::memory_order_relaxed);
        LOG_INFO(HW_GPU, "NX2_FENCE_REPORTS 10s: fenced payload {}, fenced counters {}, unfenced {} "
                 "(EDEN_NX2_EARLY_FENCE_REPORTS={})",
                 nx2_fenced_payload.exchange(0), nx2_fenced_counter.exchange(0),
                 nx2_unfenced.exchange(0), nx2_early_fence_reports);
    }
    const bool is_fence =
        fence_bit && !(nx2_early_fence_reports >= 2 ||
                       (nx2_early_fence_reports == 1 && counter_type == QueryType::Payload));
    const bool mirrored = True(flags & QueryPropertiesFlags::Nx2Mirror);"""
if t.count(old) != 1:
    sys.exit("query_cache.h: CounterReport's is_fence not found")
t = t.replace(old, new, 1)
for inc in ("#include <atomic>", "#include <chrono>", "#include <cstdlib>"):
    if inc not in t:
        t = t.replace("#pragma once\n", "#pragma once\n" + inc + "\n", 1)
f.write_text(t)
print("query_cache.h: NX2_FENCE_REPORTS counts; EDEN_NX2_EARLY_FENCE_REPORTS=1/2 test switch")
