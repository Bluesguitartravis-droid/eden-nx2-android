// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include <fmt/format.h>

#include "common/common_types.h"
#include "common/logging.h"
#include "video_core/nx2_live_switch.h"

// Emulated mesh draws by pipeline (scripts/apply-nx2-mesh-layout.py), to find which of a game's
// task/mesh programs (grass, leaves, ...) costs the most, and to switch them off one at a time.
//
// EDEN_NX2_MESH_STATS=1 (live): every 10 s, the emulated mesh pipelines drawn, most workgroups
// first: their shader hashes (as sN:<hash>, N the stage slot), draws, workgroups and output layout.
// EDEN_NX2_SKIP_MESH (live, read about once a second): "all", or hashes in hex as that log prints
// them (without the "sN:"), comma-separated. Emulated mesh draws of a pipeline with one of them
// are skipped.
namespace VideoCommon::Nx2MeshStats {

struct Shape {
    u32 max_vertices{};
    u32 vertex_lines{};
    u32 max_primitives{};
    u32 block_words{};
    bool task{};
};

struct Entry {
    std::array<u64, 6> hashes{};
    Shape shape{};
    u64 draws{};
    u64 groups{};
    u64 skipped{};
    u64 indirect{};
};

struct State {
    std::mutex mutex;
    std::unordered_map<const void*, Entry> entries;
    std::chrono::steady_clock::time_point last_log{};
    std::chrono::steady_clock::time_point last_check{};
    u32 calls{};
    bool stats{};
    bool skip_all{};
    std::vector<u64> skip;
};

inline State& Get() {
    static State state;
    return state;
}

/// With the state locked: reads the switches again about once a second.
inline void Refresh(State& state, std::chrono::steady_clock::time_point now) {
    if ((state.calls++ & 63) != 0 && state.last_check != std::chrono::steady_clock::time_point{}) {
        return;
    }
    if (now - state.last_check < std::chrono::seconds{1}) {
        return;
    }
    state.last_check = now;
    state.stats = VideoCommon::Nx2LiveSwitch("EDEN_NX2_MESH_STATS") == "1";
    const std::string list = VideoCommon::Nx2LiveSwitch("EDEN_NX2_SKIP_MESH");
    state.skip.clear();
    state.skip_all = list == "all" || list == "1";
    if (state.skip_all) {
        return;
    }
    size_t begin = 0;
    while (begin < list.size()) {
        size_t end = list.find(',', begin);
        if (end == std::string::npos) {
            end = list.size();
        }
        std::string item = list.substr(begin, end - begin);
        item.erase(std::remove(item.begin(), item.end(), ' '), item.end());
        if (const size_t colon = item.find(':'); colon != std::string::npos) {
            item.erase(0, colon + 1); // "s1:<hash>" as the log prints it
        }
        if (!item.empty()) {
            char* parse_end = nullptr;
            const u64 value = std::strtoull(item.c_str(), &parse_end, 16);
            if (parse_end != nullptr && *parse_end == '\0' && value != 0) {
                state.skip.push_back(value);
            }
        }
        begin = end + 1;
    }
}

/// With the state locked: the log, every 10 s while EDEN_NX2_MESH_STATS=1.
inline void MaybeLog(State& state, std::chrono::steady_clock::time_point now) {
    if (state.last_log == std::chrono::steady_clock::time_point{}) {
        state.last_log = now;
        return;
    }
    if (now - state.last_log < std::chrono::seconds{10}) {
        return;
    }
    state.last_log = now;
    if (!state.stats) {
        state.entries.clear();
        return;
    }
    std::vector<const Entry*> rows;
    u64 draws = 0;
    u64 groups = 0;
    for (const auto& [pipeline, entry] : state.entries) {
        rows.push_back(&entry);
        draws += entry.draws;
        groups += entry.groups;
    }
    std::ranges::sort(rows, [](const Entry* a, const Entry* b) { return a->groups > b->groups; });
    LOG_INFO(Render_Vulkan,
             "Mesh stats 10 s: {} emulated mesh pipelines, {} draws, {} workgroups (skip: {})",
             rows.size(), draws, groups,
             state.skip_all ? std::string{"all"} : fmt::format("{} hashes", state.skip.size()));
    for (size_t i = 0; i < rows.size() && i < 16; ++i) {
        const Entry& entry = *rows[i];
        std::string hashes;
        for (size_t stage = 0; stage < entry.hashes.size(); ++stage) {
            if (entry.hashes[stage] != 0) {
                hashes += fmt::format("{}s{}:{:016x}", hashes.empty() ? "" : "+", stage,
                                      entry.hashes[stage]);
            }
        }
        LOG_INFO(Render_Vulkan,
                 "Mesh stats #{}: {} | {} draws ({} skipped, {} indirect), {} workgroups | "
                 "{} vertices x {} lines, {} primitives, {} words per workgroup{}",
                 i + 1, hashes, entry.draws, entry.skipped, entry.indirect, entry.groups,
                 entry.shape.max_vertices, entry.shape.vertex_lines, entry.shape.max_primitives,
                 entry.shape.block_words, entry.shape.task ? ", task program" : "");
    }
    state.entries.clear();
}

/// An emulated mesh draw of `pipeline` with `groups` workgroups (task workgroups with a task
/// program; 0 when indirect). Returns whether EDEN_NX2_SKIP_MESH skips it.
inline bool Note(const void* pipeline, const std::array<u64, 6>& hashes, const Shape& shape,
                 u64 groups, bool indirect) {
    State& state = Get();
    std::scoped_lock lock{state.mutex};
    const auto now = std::chrono::steady_clock::now();
    Refresh(state, now);
    bool skip = state.skip_all;
    for (size_t i = 0; i < hashes.size() && !skip && !state.skip.empty(); ++i) {
        skip = hashes[i] != 0 && std::ranges::find(state.skip, hashes[i]) != state.skip.end();
    }
    Entry& entry = state.entries[pipeline];
    entry.hashes = hashes;
    entry.shape = shape;
    ++entry.draws;
    entry.groups += groups;
    entry.skipped += skip ? 1 : 0;
    entry.indirect += indirect ? 1 : 0;
    MaybeLog(state, now);
    return skip;
}

} // namespace VideoCommon::Nx2MeshStats
