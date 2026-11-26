#!/usr/bin/env python3
"""EDEN_NX2_LIST_INDIRECT_ANY_LIMIT=1 (live, read from the switches file every 2 s): take Ocarina's
GPU-written dispatch sizes (Nx2ListIndirect) on devices whose compute grid limit is below 2^31 groups
wide. The Adreno 830 reports 65535x65535x65535, so Lichii's check turned the path off on the Odin,
and the GPU thread's front waited for the culling to run and its copy to come back instead
(the "list writers" waits). The culling's group counts are far below the device limits; an indirect
dispatch past them would be out of spec, which EDEN_NX2_LIST_INDIRECT_CHECK can watch for."""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
text = path.read_text()
old = '''    if (static std::atomic<bool> logged{}; !fits && !logged.exchange(true)) {
        LOG_WARNING(Render_Vulkan,
                    "Nx2 list indirect off: the device dispatches at most {}x{}x{} groups",
                    max_dim[0], max_dim[1], max_dim[2]);
    }
    return fits;'''
new = '''    if (!fits && VideoCommon::Nx2LiveSwitch("EDEN_NX2_LIST_INDIRECT_ANY_LIMIT") == "1") {
        if (static std::atomic<bool> logged_any{}; !logged_any.exchange(true)) {
            LOG_INFO(Render_Vulkan,
                     "Nx2 list indirect on below the usual grid limits (EDEN_NX2_LIST_INDIRECT_ANY_LIMIT): "
                     "the device dispatches at most {}x{}x{} groups",
                     max_dim[0], max_dim[1], max_dim[2]);
        }
        return true;
    }
    if (static std::atomic<bool> logged{}; !fits && !logged.exchange(true)) {
        LOG_WARNING(Render_Vulkan,
                    "Nx2 list indirect off: the device dispatches at most {}x{}x{} groups",
                    max_dim[0], max_dim[1], max_dim[2]);
    }
    return fits;'''
if text.count(old) != 1:
    sys.exit(f"apply-nx2-list-indirect-any-limit: expected one check, found {text.count(old)}")
path.write_text(text.replace(old, new))
print("apply-nx2-list-indirect-any-limit: EDEN_NX2_LIST_INDIRECT_ANY_LIMIT added")
