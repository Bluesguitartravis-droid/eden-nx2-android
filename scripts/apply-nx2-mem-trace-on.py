#!/usr/bin/env python3
"""Memory trace on by default, every 10 s (Ocarina grew 2.7 -> 17 GB and was killed).

The texture cache's existing EDEN_NX2_MEM_TRACE report (cache images, device usage,
staging pools, VMA heaps, process commit) now runs unless EDEN_NX2_MEM_TRACE=0, and the
10 s watchdog adds NX2_PROC: /proc/self/status RSS split (anon / file / shmem = guest
memory) and swap, plus the KGSL per-process GPU memory counters when readable.
First built in build 157 (build 156 compiled but could not publish).
Usage: python3 scripts/apply-nx2-mem-trace-on.py <upstream-root>
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
tc = root / "src/video_core/texture_cache/texture_cache.h"
t = tc.read_text()
old = '    static const bool mem_trace = std::getenv("EDEN_NX2_MEM_TRACE") != nullptr;\n'
new = ('    static const bool mem_trace = [] {\n'
       '        const char* value = std::getenv("EDEN_NX2_MEM_TRACE");\n'
       '        return value == nullptr || value[0] != \'0\';\n'
       '    }();\n')
if old in t:
    t = t.replace(old, new, 1)
elif new not in t:
    raise SystemExit("texture_cache.h: mem_trace switch not found")
old = "mem_trace && now - mem_trace_time >= std::chrono::seconds{2}) {"
if old in t:
    t = t.replace(old, "mem_trace && now - mem_trace_time >= std::chrono::seconds{10}) {", 1)
elif "mem_trace_time >= std::chrono::seconds{10}" not in t:
    raise SystemExit("texture_cache.h: mem_trace interval not found")
tc.write_text(t)

core = root / "src/core/core.cpp"
c = core.read_text()
anchor = '                                LOG_INFO(Core, "NX2_CPU cur={} max={}", nx2_cur, nx2_max);\n'
if "NX2_PROC" not in c:
    if c.count(anchor) != 1:
        raise SystemExit("core.cpp: NX2_CPU line not found (run apply-nx2-cpu-clocks.py first)")
    c = c.replace(anchor, anchor + r'''                                {
                                    std::string nx2_proc;
                                    if (std::FILE* f = std::fopen("/proc/self/status", "r")) {
                                        char line[160];
                                        while (std::fgets(line, sizeof(line), f) != nullptr) {
                                            for (const char* key : {"VmRSS:", "RssAnon:", "RssFile:",
                                                                    "RssShmem:", "VmSwap:"}) {
                                                const size_t len = std::strlen(key);
                                                if (std::strncmp(line, key, len) == 0) {
                                                    const long kib = std::strtol(line + len, nullptr, 10);
                                                    nx2_proc += std::string{key, len - 1} + "=" +
                                                                std::to_string(kib / 1024) + " ";
                                                }
                                            }
                                        }
                                        std::fclose(f);
                                    }
                                    const std::string kgsl =
                                        "/sys/class/kgsl/kgsl/proc/" + std::to_string(getpid()) + "/";
                                    LOG_INFO(Core, "NX2_PROC MiB: {}gpumem_mapped={} gpumem_unmapped={}",
                                             nx2_proc,
                                             read_sys((kgsl + "gpumem_mapped").c_str()),
                                             read_sys((kgsl + "gpumem_unmapped").c_str()));
                                }
''', 1)
    for inc in ("#include <cstring>\n", "#include <unistd.h>\n"):
        if inc not in c:
            c = c.replace("#include <thread>\n", "#include <thread>\n" + inc, 1)
    core.write_text(c)
print("Memory trace on (10 s) and NX2_PROC installed")
