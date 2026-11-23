#!/usr/bin/env python3
"""EDEN_NX2_CPU_BACKEND=nce|dynarmic (measurement aid, read at each game boot from the switches
file): overrides cpu_backend for that boot, so the two can be compared by relaunching the game,
without restarting Eden or touching the per-game settings.

The SVC trace (242) shows Ocarina's main thread running ~30 ms and waiting ~17 ms on the game's
own worker jobs every ~78 ms frame: guest CPU time is on the critical path. Ocarina's per-game
config follows the build default, which is Dynarmic since Lichii 523e5048; its speed on NCE was
never measured on this base (the 225 "NCE" boots ran on Dynarmic)."""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/core/device_memory.cpp"
text = path.read_text()
old = """u64 DeviceMemory::PrepareNativeExecution(bool has_39bit_address_space) {
    const bool requested = Settings::values.cpu_backend.GetValue() == Settings::CpuBackend::Nce;
"""
new = """u64 DeviceMemory::PrepareNativeExecution(bool has_39bit_address_space) {
    // NX2 (scripts/apply-nx2-cpu-backend-live.py)
    if (const std::string nx2_backend = VideoCommon::Nx2LiveSwitch("EDEN_NX2_CPU_BACKEND");
        !nx2_backend.empty()) {
        if (nx2_backend == "nce") {
            Settings::values.cpu_backend.SetValue(Settings::CpuBackend::Nce);
        } else if (nx2_backend == "dynarmic") {
            Settings::values.cpu_backend.SetValue(Settings::CpuBackend::Dynarmic);
        }
        LOG_INFO(Core, "NX2 cpu backend from the switches file: {}", nx2_backend);
    }
    const bool requested = Settings::values.cpu_backend.GetValue() == Settings::CpuBackend::Nce;
"""
if text.count(old) != 1:
    sys.exit("cpu-backend-live: PrepareNativeExecution not found")
text = text.replace(old, new)
text = text.replace('#include "core/device_memory.h"\n',
                    '#include "core/device_memory.h"\n#include "video_core/nx2_live_switch.h"\n#include <string>\n', 1)
path.write_text(text)
print("cpu-backend-live: EDEN_NX2_CPU_BACKEND added")
