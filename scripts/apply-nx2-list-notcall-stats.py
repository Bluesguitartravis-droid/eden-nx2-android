#!/usr/bin/env python3
"""The GPU-written command lists Nx2ListIndirect does not take ("not all of a compute macro call's
parameters", ~700 a second in Ocarina at the drawbridge), counted by what the list is: the
engine on the list's subchannel, the method the parser stood at, the list's size in words, and
how many words the method still expected. Logged with the list indirect line (EDEN_NX2_PROFILE=1 or
EDEN_NX2_LIST_INDIRECT_CHECK) as "list indirect kept lists: ...", the 16 most common."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"


def patch(name, pairs):
    path = root / name
    text = path.read_text()
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit(f"apply-nx2-list-notcall-stats: expected one {old[:70]!r} in {name}, found {text.count(old)}")
        text = text.replace(old, new)
    path.write_text(text)


patch("nx2_list_indirect_stats.h", [
    ('''#include <span>
#include <string>
''', '''#include <span>
#include <string>
#include <tuple>
#include <vector>
'''),
    ('''    std::map<u32, std::array<u64, 2>> taken; ///< By macro method: lists, words
''', '''    std::map<u32, std::array<u64, 2>> taken; ///< By macro method: lists, words
    /// Lists not taken as a compute macro call's parameters, by (engine type + 1, or 0 for none;
    /// method; list words, up to 4096; words the method still expected, up to 4096): lists, words.
    std::map<std::tuple<u32, u32, u32, u32>, std::array<u64, 2>> not_call;
'''),
    ('''/// The front: a list of `words` words the call of compute macro method `method` takes.''',
     '''/// The front: a GPU-written list of `words` words that is not all of a compute macro call's
/// parameters, met with the parser at `method` of engine `engine` (-1: none) expecting `expected`.
inline void NoteNotCall(int engine, u32 method, u32 words, u32 expected) {
    if (Enabled()) {
        auto& entry = Get().not_call[{static_cast<u32>(engine + 1), method, std::min(words, 4096U),
                                      std::min(expected, 4096U)}];
        ++entry[0];
        entry[1] += words;
    }
}

/// The front: a list of `words` words the call of compute macro method `method` takes.'''),
    ('''    counters.taken.clear();
    counters.kept = {};''', '''    if (!counters.not_call.empty()) {
        static constexpr std::array<const char*, 7> engine_names{
            "none", "timer", "compute", "3D", "2D", "DMA", "inline-to-memory"};
        std::vector<std::pair<std::tuple<u32, u32, u32, u32>, std::array<u64, 2>>> rows(
            counters.not_call.begin(), counters.not_call.end());
        std::ranges::sort(rows, [](const auto& a, const auto& b) { return a.second[0] > b.second[0]; });
        std::string top;
        for (size_t i = 0; i < rows.size() && i < 16; ++i) {
            const auto& [key, entry] = rows[i];
            const auto [engine, method, words, expected] = key;
            top += fmt::format("{}{} {:#x} x{} words (method expects {}) {} lists",
                               top.empty() ? "" : "; ",
                               engine < engine_names.size() ? engine_names[engine] : "?", method,
                               words, expected, entry[0]);
        }
        LOG_INFO(HW_GPU, "list indirect kept lists ({} kinds): {}", rows.size(), top);
    }
    counters.not_call.clear();
    counters.taken.clear();
    counters.kept = {};'''),
])

patch("dma_pusher.cpp", [(
    '''        subchannel_type[dma_state.subchannel] != Engines::EngineTypes::KeplerCompute) {
        Nx2ListIndirect::Stats::NoteKept(Kept::NotCall);
        return false;
    }''', '''        subchannel_type[dma_state.subchannel] != Engines::EngineTypes::KeplerCompute) {
        Nx2ListIndirect::Stats::NoteKept(Kept::NotCall);
        Nx2ListIndirect::Stats::NoteNotCall(
            subchannels[dma_state.subchannel] != nullptr
                ? static_cast<int>(subchannel_type[dma_state.subchannel])
                : -1,
            dma_state.method, count, dma_state.method_count);
        return false;
    }''')])
print("apply-nx2-list-notcall-stats: kept GPU-written lists counted by kind")
