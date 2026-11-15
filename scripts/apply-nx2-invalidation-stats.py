#!/usr/bin/env python3
"""Profile: count the CPU-write invalidations (NCE faults on GPU-cached pages) that hit pages the
GPU wrote and so wait for a flush on the GPU thread, and the time they wait. Since build 131 the
NCE fault handler costs 4-5x more per fault (DKB 700-1,000 ms/s): this shows whether these waits
are the cost. Requires apply-nx2-thread-cpu.py."""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/video_core/gpu.cpp"
t = f.read_text()
old = """                gpu_thread.TickGPU(is_async);
                WaitForSyncOperation(fence);
            }
            return;
        }
        gpu_thread.InvalidateRegion(addr, size);"""
new = """                const auto nx2_wait_start = std::chrono::steady_clock::now();
                gpu_thread.TickGPU(is_async);
                WaitForSyncOperation(fence);
                Common::Nx2NceStats::sync_invalidations.fetch_add(1, std::memory_order_relaxed);
                Common::Nx2NceStats::sync_invalidation_ns.fetch_add(
                    static_cast<u64>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                         std::chrono::steady_clock::now() - nx2_wait_start)
                                         .count()),
                    std::memory_order_relaxed);
            }
            return;
        }
        gpu_thread.InvalidateRegion(addr, size);"""
if "nx2_wait_start" in t:
    print("invalidation stats already applied"); sys.exit(0)
if old not in t:
    raise SystemExit("gpu.cpp: InvalidateRegion shape changed")
t = t.replace(old, new, 1)
t = '#include <chrono>\n#include "common/nx2_nce_stats.h"\n' + t
f.write_text(t)
print("GPU-written invalidation waits counted")
