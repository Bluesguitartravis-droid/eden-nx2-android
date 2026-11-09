#!/usr/bin/env python3
"""Profile: every 10 s (with NX2_GPU), log the CPU time of the busiest host threads
(/proc/self/task, by thread name) and how many NCE memory faults the guest took and how long
their handler ran. With the GPU ~45% busy and both GPU threads 25-40% idle, DKB still runs at
14-20 fps with the speed limit off: this shows which threads are the limit (game cores, a
service thread, audio...). Requires apply-nx2-switches-and-gpu-stats.py first."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

hdr = root / "src/common/nx2_nce_stats.h"
hdr.write_text('''// NX2 profiling: NCE guest memory fault counters (written in the fault handler).
#pragma once
#include <atomic>
#include <cstdint>

namespace Common::Nx2NceStats {
inline std::atomic<std::uint64_t> faults{};
inline std::atomic<std::uint64_t> fault_ns{};
inline std::atomic<std::uint64_t> unmapped{};
// GPU-cached pages a fault hit that the GPU had written: the fault waits for the GPU thread.
inline std::atomic<std::uint64_t> sync_invalidations{};
inline std::atomic<std::uint64_t> sync_invalidation_ns{};
} // namespace Common::Nx2NceStats
''')

nce = root / "src/core/arm/nce/arm_nce.cpp"
t = nce.read_text()
old = "bool ArmNce::HandleGuestAccessFault(GuestContext* guest_ctx, void* raw_info, void* raw_context) {\n"
new = old + """    const auto nx2_fault_start = std::chrono::steady_clock::now();
    struct Nx2FaultTimer {
        std::chrono::steady_clock::time_point start;
        ~Nx2FaultTimer() {
            Common::Nx2NceStats::faults.fetch_add(1, std::memory_order_relaxed);
            Common::Nx2NceStats::fault_ns.fetch_add(
                static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                               std::chrono::steady_clock::now() - start)
                                               .count()),
                std::memory_order_relaxed);
        }
    } nx2_fault_timer{nx2_fault_start};
"""
if "nx2_fault_timer" not in t:
    if old not in t:
        raise SystemExit("HandleGuestAccessFault not found")
    t = t.replace(old, new, 1)
    marker = "    // An access to unmapped memory: execute it on the emulated memory"
    if marker in t:
        t = t.replace(marker, "    Common::Nx2NceStats::unmapped.fetch_add(1, std::memory_order_relaxed);\n" + marker, 1)
    t = '#include <chrono>\n#include "common/nx2_nce_stats.h"\n' + t
    nce.write_text(t)

core = root / "src/core/core.cpp"
c = core.read_text()
anchor = '                            LOG_INFO(Core, "NX2_GPU busy={} clk={} max={} temp={} switches=[{}]",'
report = r'''                            {
                                // Busiest host threads over the last window, by name.
                                static std::unordered_map<int, std::pair<std::string, u64>> prev;
                                static s64 prev_ms = 0;
                                const long hz = sysconf(_SC_CLK_TCK);
                                std::vector<std::pair<double, std::string>> busy;
                                std::unordered_map<int, std::pair<std::string, u64>> cur;
                                if (DIR* dir = opendir("/proc/self/task")) {
                                    while (dirent* e = readdir(dir)) {
                                        const int tid = std::atoi(e->d_name);
                                        if (tid <= 0) {
                                            continue;
                                        }
                                        const std::string path =
                                            fmt::format("/proc/self/task/{}/stat", tid);
                                        std::FILE* f = std::fopen(path.c_str(), "r");
                                        if (!f) {
                                            continue;
                                        }
                                        char buf[512]{};
                                        const size_t n = std::fread(buf, 1, sizeof(buf) - 1, f);
                                        std::fclose(f);
                                        const std::string s{buf, n};
                                        const auto open = s.find('(');
                                        const auto close = s.rfind(')');
                                        if (open == std::string::npos || close == std::string::npos) {
                                            continue;
                                        }
                                        const std::string name = s.substr(open + 1, close - open - 1);
                                        // Fields after ") ": state(3) ... utime(14) stime(15).
                                        unsigned long long ut = 0, st = 0;
                                        std::sscanf(s.c_str() + close + 2,
                                                    "%*c %*d %*d %*d %*d %*d %*u %*u %*u %*u %*u %llu %llu",
                                                    &ut, &st);
                                        const u64 ticks = ut + st;
                                        cur[tid] = {name, ticks};
                                        if (const auto it = prev.find(tid); it != prev.end() && prev_ms) {
                                            const double ms =
                                                static_cast<double>(ticks - it->second.second) * 1000.0 / hz;
                                            if (ms > 0) {
                                                busy.emplace_back(ms, fmt::format("{}:{}", name, tid));
                                            }
                                        }
                                    }
                                    closedir(dir);
                                }
                                const double window_s = prev_ms ? (now - prev_ms) / 1000.0 : 0.0;
                                prev = std::move(cur);
                                prev_ms = now;
                                if (window_s > 0) {
                                    std::sort(busy.rbegin(), busy.rend());
                                    std::string line;
                                    for (size_t i = 0; i < busy.size() && i < 14; ++i) {
                                        line += fmt::format(" {}={:.0f}", busy[i].second,
                                                            busy[i].first / window_s);
                                    }
                                    LOG_INFO(Core, "NX2_THREADS ms/s:{}", line);
                                    const u64 faults = Common::Nx2NceStats::faults.exchange(0);
                                    const u64 fault_ns = Common::Nx2NceStats::fault_ns.exchange(0);
                                    const u64 unmapped = Common::Nx2NceStats::unmapped.exchange(0);
                                    const u64 sync_inv = Common::Nx2NceStats::sync_invalidations.exchange(0);
                                    const u64 sync_ns = Common::Nx2NceStats::sync_invalidation_ns.exchange(0);
                                    LOG_INFO(Core, "NX2_NCE faults {:.0f}/s handler {:.1f} ms/s unmapped {:.0f}/s "
                                                   "gpu-written waits {:.0f}/s {:.1f} ms/s",
                                             faults / window_s, fault_ns / 1e6 / window_s,
                                             unmapped / window_s, sync_inv / window_s,
                                             sync_ns / 1e6 / window_s);
                                }
                            }
'''
if "NX2_THREADS" not in c:
    if anchor not in c:
        raise SystemExit("NX2_GPU log not found (run apply-nx2-switches-and-gpu-stats.py first)")
    c = c.replace(anchor, report + anchor, 1)
    c = ('#include <dirent.h>\n#include <unistd.h>\n#include <algorithm>\n#include <unordered_map>\n'
         '#include <vector>\n#include "common/nx2_nce_stats.h"\n') + c
    core.write_text(c)
print("thread CPU and NCE fault profiling applied")
