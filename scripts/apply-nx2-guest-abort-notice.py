#!/usr/bin/env python3
"""Tell the player when the game itself aborted, instead of a black screen that never changes.

Ocarina of Time (build 170) aborted 20 s after starting (nn::os::LockMutex -> Abort, result
2466-3264): the game stopped, the emulator kept running, and the screen stayed black at 0 FPS
for 10 minutes with no sign of what happened. svcBreak (a guest panic, assert or abort) now
records the game's error code; the app asks for it every 2.5 s (Nx2FlightRecorder) and shows a
dialog with the code and a "Save report" button.
First built in build 174.
Usage: python3 scripts/apply-nx2-guest-abort-notice.py <upstream-root>
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]) / "src"
MARK = "NX2 guest abort notice"

(root / "core/hle/kernel/svc/nx2_guest_abort.h").write_text('''// NX2 guest abort notice: the error code of the game's last svcBreak (0 when none this session).
#pragma once

#include <atomic>

#include "common/common_types.h"

namespace Kernel::Svc {
inline std::atomic<u32> g_nx2_guest_abort{0};
} // namespace Kernel::Svc
''')


def patch(path, edits, includes=()):
    text = path.read_text()
    if MARK in text:
        print(f"{path.name}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{path.name}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    for anchor, line in includes:
        if anchor not in text:
            raise SystemExit(f"{path.name}: include anchor missing: {anchor!r}")
        text = text.replace(anchor, anchor + line, 1)
    path.write_text(text)
    print(f"{path.name}: patched")


patch(root / "core/hle/kernel/svc/svc_exception.cpp", [
    (
        "    bool has_dumped_buffer{};\n"
        "    std::vector<u8> debug_buffer;\n",
        "    bool has_dumped_buffer{};\n"
        "    std::vector<u8> debug_buffer;\n"
        "    u32 nx2_error_code{}; // " + MARK + "\n",
    ),
    (
        "            LOG_CRITICAL(Debug_Emulated, \"debug_buffer_err_code={:X}\", memory.Read32(addr));\n",
        "            nx2_error_code = memory.Read32(addr);\n"
        "            LOG_CRITICAL(Debug_Emulated, \"debug_buffer_err_code={:X}\", nx2_error_code);\n",
    ),
    (
        "        system.CurrentPhysicalCore().LogBacktrace(system.Kernel());\n"
        "    }\n",
        "        system.CurrentPhysicalCore().LogBacktrace(system.Kernel());\n"
        "        // " + MARK + ": the app shows it (0xFFFFFFFF: no error code given).\n"
        "        g_nx2_guest_abort.store(nx2_error_code != 0 ? nx2_error_code : 0xFFFFFFFFU);\n"
        "    }\n",
    ),
], includes=[('#include "core/hle/kernel/svc.h"\n',
              '#include "core/hle/kernel/svc/nx2_guest_abort.h"\n')])

patch(root / "android/app/src/main/jni/native.cpp", [
    (
        "    Nx2PrecacheGamePath() = filepath; // NX2 shader precache\n",
        "    Nx2PrecacheGamePath() = filepath; // NX2 shader precache\n"
        "    Kernel::Svc::g_nx2_guest_abort.store(0); // " + MARK + "\n",
    ),
    (
        "static EmulationSession s_instance;\n",
        "static EmulationSession s_instance;\n"
        "\n"
        "// " + MARK + ": the game's error code after it aborted, 0 while it runs.\n"
        "extern \"C\" JNIEXPORT jint JNICALL\n"
        "Java_io_github_zortos293_lichii_utils_Nx2FlightRecorder_nativeGuestAbort(JNIEnv*, jclass) {\n"
        "    return static_cast<jint>(Kernel::Svc::g_nx2_guest_abort.load());\n"
        "}\n",
    ),
], includes=[('#include "video_core/renderer_base.h"\n',
              '#include "core/hle/kernel/svc/nx2_guest_abort.h"\n')])
print("guest abort notice installed")
