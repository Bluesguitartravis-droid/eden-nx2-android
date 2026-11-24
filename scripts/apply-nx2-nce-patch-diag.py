#!/usr/bin/env python3
"""NCE diagnostics for the counter reads the patcher misses (Ocarina: MRS X11, CNTPCT_EL0 traps at
Owl.nss+0x3C4620C once a frame; the game also runs ~1.6x fast on NCE, as if code there read the
host's 19.2 MHz CNTFRQ_EL0 natively, which does not trap, against a 31.25 MHz counter).

- Patcher::PatchText logs each module: image size, text offset/size, split mode, and how many
  SVCs, counter reads (CNTPCT/CNTVCT/CNTFRQ) and TPIDR accesses it patched.
- The first trapped counter read records the 32 instruction words around it; the 10 s NCE report
  logs them once (`NX2_NCE trapped counter read at <pc>: <words>`), with any CNTFRQ/CNTVCT/CNTPCT
  reads among them marked."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-patch-diag: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


replace(src / "common/nx2_nce_stats.h", "inline std::atomic<bool> open_pages{true};\n",
        "inline std::atomic<bool> open_pages{true};\n"
        "// The first trapped counter read: its pc and the 32 words around it (pc - 64 .. pc + 60).\n"
        "inline std::atomic<std::uint64_t> trap_dump_pc{};\n"
        "inline std::uint32_t trap_dump_words[32]{};\n"
        "inline std::atomic<bool> trap_dump_logged{};\n")

nce = src / "core/arm/nce/arm_nce.cpp"
replace(nce, """            Common::Nx2NceStats::counter_traps.fetch_add(1, std::memory_order_relaxed);
            return true;""", """            if (Common::Nx2NceStats::trap_dump_pc.load(std::memory_order_relaxed) == 0) {
                const auto* const words = reinterpret_cast<const u32*>(nx2_ctx.pc - 64);
                for (size_t i = 0; i < 32; ++i) {
                    Common::Nx2NceStats::trap_dump_words[i] = words[i];
                }
                Common::Nx2NceStats::trap_dump_pc.store(nx2_ctx.pc, std::memory_order_release);
            }
            Common::Nx2NceStats::counter_traps.fetch_add(1, std::memory_order_relaxed);
            return true;""")

core = src / "core/core.cpp"
replace(core, """                                    if (const u64 nx2_ctr =
                                            Common::Nx2NceStats::counter_traps.exchange(0)) {""",
        """                                    if (const u64 nx2_dump_pc =
                                            Common::Nx2NceStats::trap_dump_pc.load(std::memory_order_acquire);
                                        nx2_dump_pc != 0 &&
                                        !Common::Nx2NceStats::trap_dump_logged.exchange(true)) {
                                        std::string words;
                                        for (size_t i = 0; i < 32; ++i) {
                                            const u32 w = Common::Nx2NceStats::trap_dump_words[i];
                                            const u32 reg = w & ~0x1fU;
                                            const char* mark = reg == 0xd53be000U   ? "[CNTFRQ]"
                                                               : reg == 0xd53be020U ? "[CNTPCT]"
                                                               : reg == 0xd53be040U ? "[CNTVCT]"
                                                                                    : "";
                                            words += fmt::format(" {}{:08x}{}", i == 16 ? ">" : "", w, mark);
                                        }
                                        LOG_WARNING(Core, "NX2_NCE trapped counter read at {:#x}:{}",
                                                    nx2_dump_pc, words);
                                    }
                                    if (const u64 nx2_ctr =
                                            Common::Nx2NceStats::counter_traps.exchange(0)) {""")

patcher = src / "core/arm/nce/patcher.cpp"
replace(patcher, """    // Loop through instructions, patching as needed.
    for (u32 i = ModuleCodeIndex; i < static_cast<u32>(text_words.size()); i++) {
        const u32 inst = text_words[i];
""", """    // NX2: what was patched (scripts/apply-nx2-nce-patch-diag.py)
    size_t nx2_svcs = 0;
    size_t nx2_counters = 0;
    size_t nx2_counter_words = 0;
    for (u32 i = ModuleCodeIndex; i < static_cast<u32>(text_words.size()); i++) {
        if (auto mrs = MRS{text_words[i]}; mrs.Verify() && IsCounterRegister(mrs.GetSystemReg())) {
            ++nx2_counter_words;
        }
    }
    struct Nx2PatchLog {
        size_t image_size, text_offset, text_size;
        bool split;
        size_t* svcs;
        size_t* counters;
        size_t counter_words;
        ~Nx2PatchLog() {
            LOG_INFO(Core_ARM,
                     "NX2_NCE patcher: module image {:#x}, text {:#x}+{:#x}, split {}: {} SVCs, "
                     "{} of {} counter reads patched",
                     image_size, text_offset, text_size, split, *svcs, *counters, counter_words);
        }
    } nx2_patch_log{image_size, code.offset, code.size, use_split, &nx2_svcs, &nx2_counters,
                    nx2_counter_words};

    // Loop through instructions, patching as needed.
    for (u32 i = ModuleCodeIndex; i < static_cast<u32>(text_words.size()); i++) {
        const u32 inst = text_words[i];
""")
replace(patcher, """        // SVC
        if (auto svc = SVC{inst}; svc.Verify()) {
            bool pre_buffer = false;""", """        // SVC
        if (auto svc = SVC{inst}; svc.Verify()) {
            ++nx2_svcs;
            bool pre_buffer = false;""")
replace(patcher, """        if (auto mrs = MRS{inst}; mrs.Verify() && IsCounterRegister(mrs.GetSystemReg())) {
            bool pre_buffer = false;
            const auto ret = AddRelocations(pre_buffer);""", """        if (auto mrs = MRS{inst}; mrs.Verify() && IsCounterRegister(mrs.GetSystemReg())) {
            ++nx2_counters;
            bool pre_buffer = false;
            const auto ret = AddRelocations(pre_buffer);""")
text = patcher.read_text()
if '#include "common/logging.h"' not in text:
    patcher.write_text(text.replace('#include <numeric>\n', '#include <numeric>\n#include "common/logging.h"\n', 1))
print("nce-patch-diag: patcher module log and trapped-read dump added")
