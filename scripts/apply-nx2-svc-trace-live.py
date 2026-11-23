#!/usr/bin/env python3
"""EDEN_NX2_SVC_TRACE_LIVE=<n> (measurement aid, live): each time this line of the switches file
changes to a new count, log every SVC of the next n presents with its arguments and results (as
EDEN_NX2_SVC_TRACE=<first>:<last> does, which is read only at start). The log lines' times give a
frame-time profile of what the game's threads wait on, without restarting Eden."""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/core/hle/kernel/svc.cpp"
text = path.read_text()


def replace(old, new):
    global text
    if text.count(old) != 1:
        sys.exit(f"svc-trace-live: expected 1 match, found {text.count(old)}:\n{old}")
    text = text.replace(old, new)


replace('#include "core/hle/service/kernel_helpers.h"\n',
        '#include "core/hle/service/kernel_helpers.h"\n#include "video_core/nx2_live_switch.h"\n'
        '#include <atomic>\n#include <chrono>\n#include <mutex>\n#include <string>\n')
replace("""    const u64 presents = Core::g_nx2_present_count.load(std::memory_order_relaxed);
    const bool svc_trace = presents >= trace_range.first && presents <= trace_range.second;
""", """    const u64 presents = Core::g_nx2_present_count.load(std::memory_order_relaxed);
    // NX2 live SVC trace (scripts/apply-nx2-svc-trace-live.py)
    static std::atomic<u64> nx2_live_first{1};
    static std::atomic<u64> nx2_live_last{0};
    static std::atomic<s64> nx2_live_next_check{0};
    {
        const s64 now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                               std::chrono::steady_clock::now().time_since_epoch())
                               .count();
        s64 next = nx2_live_next_check.load(std::memory_order_relaxed);
        if (now_ms >= next &&
            nx2_live_next_check.compare_exchange_strong(next, now_ms + 2000,
                                                        std::memory_order_relaxed)) {
            static std::mutex nx2_live_mutex;
            static std::string nx2_live_value;
            std::scoped_lock lock{nx2_live_mutex};
            const std::string value = VideoCommon::Nx2LiveSwitch("EDEN_NX2_SVC_TRACE_LIVE");
            if (value != nx2_live_value) {
                nx2_live_value = value;
                const u64 count = std::strtoull(value.c_str(), nullptr, 10);
                if (count != 0) {
                    nx2_live_first.store(presents + 1, std::memory_order_relaxed);
                    nx2_live_last.store(presents + count, std::memory_order_relaxed);
                    LOG_INFO(Kernel_SVC, "NX2 live SVC trace: presents {} to {}", presents + 1,
                             presents + count);
                }
            }
        }
    }
    const bool svc_trace = (presents >= trace_range.first && presents <= trace_range.second) ||
                           (presents >= nx2_live_first.load(std::memory_order_relaxed) &&
                            presents <= nx2_live_last.load(std::memory_order_relaxed));
""")
path.write_text(text)
print("svc-trace-live: EDEN_NX2_SVC_TRACE_LIVE added")
