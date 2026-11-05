#!/usr/bin/env python3
"""Android: A/B switches without new builds, and GPU load in the log.

1. nx2_switches.txt: Lichii reads ~120 EDEN_NX2_* environment variables (tensor
   emulation, fence batching, frames ahead, ...), which an Android app cannot
   set. Lines "NAME=VALUE" in
   /storage/emulated/0/Android/data/dev.eden.eden_emulator/files/nx2_switches.txt
   are applied with setenv() from a constructor that runs before the library's
   other static initializers. Close the app fully after editing it.
2. NX2_GPU: every 10 s while a game runs, log the Adreno's busy percentage,
   clock and temperature from /sys/class/kgsl (when readable).
Requires apply-nx2-hang-watchdog.py first (the 10 s log rides its thread).
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
jni = root / "src/android/app/src/main/jni"
(jni / "nx2_switches.cpp").write_text(r'''// Eden NX2 Android: apply nx2_switches.txt (NAME=VALUE lines) as environment variables.
#include <android/log.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace {
constexpr const char* kSwitchesPath =
    "/storage/emulated/0/Android/data/dev.eden.eden_emulator/files/nx2_switches.txt";

__attribute__((constructor(101))) void Nx2ApplySwitches() {
    FILE* file = std::fopen(kSwitchesPath, "r");
    if (file == nullptr) {
        return;
    }
    std::string applied;
    char line[512];
    while (std::fgets(line, sizeof(line), file) != nullptr) {
        std::string text{line};
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r' || text.back() == ' ')) {
            text.pop_back();
        }
        const auto start = text.find_first_not_of(" \t");
        if (start == std::string::npos || text[start] == '#') {
            continue;
        }
        text = text.substr(start);
        const auto eq = text.find('=');
        const std::string name = eq == std::string::npos ? text : text.substr(0, eq);
        const std::string value = eq == std::string::npos ? "1" : text.substr(eq + 1);
        if (name.empty()) {
            continue;
        }
        setenv(name.c_str(), value.c_str(), 1);
        applied += name + "=" + value + " ";
    }
    std::fclose(file);
    setenv("EDEN_NX2_ANDROID_SWITCHES_APPLIED", applied.c_str(), 1);
    __android_log_print(ANDROID_LOG_INFO, "NX2_SWITCHES", "%s", applied.c_str());
}
} // namespace
''')
cm = jni / "CMakeLists.txt"
t = cm.read_text()
if "nx2_switches.cpp" not in t:
    anchor = "    native_post_processing.cpp\n"
    if anchor not in t:
        raise SystemExit("jni/CMakeLists.txt: source list shape changed")
    t = t.replace(anchor, anchor + "    nx2_switches.cpp\n", 1)
    cm.write_text(t)

core = root / "src/core/core.cpp"
c = core.read_text()
hook = "                    const s64 now = Common::Nx2Watchdog::NowMs();\n"
if "NX2_GPU busy" not in c:
    if hook not in c:
        raise SystemExit("core.cpp: NX2_HANG watchdog not found")
    c = c.replace(hook, hook + r'''                    {
                        static s64 last_gpu_log = 0;
                        if (now - last_gpu_log >= 10000) {
                            last_gpu_log = now;
                            const auto read_sys = [](const char* path) -> std::string {
                                std::FILE* f = std::fopen(path, "r");
                                if (f == nullptr) {
                                    return "?";
                                }
                                char buf[64]{};
                                const size_t n = std::fread(buf, 1, sizeof(buf) - 1, f);
                                std::fclose(f);
                                std::string s{buf, n};
                                while (!s.empty() && (s.back() == '\n' || s.back() == ' ')) {
                                    s.pop_back();
                                }
                                return s;
                            };
                            LOG_INFO(Core, "NX2_GPU busy={}% clk={} max={} temp={} switches=[{}]",
                                     read_sys("/sys/class/kgsl/kgsl-3d0/gpu_busy_percentage"),
                                     read_sys("/sys/class/kgsl/kgsl-3d0/gpuclk"),
                                     read_sys("/sys/class/kgsl/kgsl-3d0/max_gpuclk"),
                                     read_sys("/sys/class/kgsl/kgsl-3d0/temp"),
                                     std::getenv("EDEN_NX2_ANDROID_SWITCHES_APPLIED")
                                         ? std::getenv("EDEN_NX2_ANDROID_SWITCHES_APPLIED")
                                         : "");
                        }
                    }
''', 1)
    if "#include <cstdio>" not in c:
        c = c.replace("#include <thread>\n", "#include <thread>\n#include <cstdio>\n#include <cstdlib>\n", 1)
    core.write_text(c)
print("nx2_switches.txt loader and NX2_GPU stats installed")
