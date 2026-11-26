// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <string>
#include <unordered_map>

#include "common/common_types.h"
#include "common/logging.h"
#include "video_core/nx2_live_switch.h"

// GPU-written draw lists without the split's front waiting (scripts/apply-nx2-list-draw-back.py).
//
// Ocarina of Time's culling writes a draw's vertex count and first vertex as the two parameters
// of 3D macro 0xe62, which only forwards them (draw begin, vertex_buffer.first, vertex_buffer.count,
// draw end). The front had to wait for the culling to run on the back and on the host GPU before
// it could parse them, about 1,000 times a second, and the ring entries before such a list waited
// for the same work (GpuSplit::WaitForSyncedWriters).
//
// EDEN_NX2_LIST_DRAW_BACK=1 (live): the front sends such a call to the back with the words'
// address; the back reads them at the list's place in the stream and issues the four writes.
// EDEN_NX2_TRUST_RING_PAGES=1 (live): a command list page read `TrustReads` times without ever
// being GPU-written is read without waiting for the queued writers; the back checks each such
// read at its place, and one miss turns the trust off until Eden restarts.
namespace Tegra::Nx2ListDraw {

inline constexpr u32 TrustReads = 2048;
inline constexpr u32 Distrusted = ~0U;

struct Counters {
    std::atomic<u64> taken{};         ///< Front: calls sent to the back
    std::atomic<u64> refused_shape{}; ///< Front: the macro is not the forwarding draw
    std::atomic<u64> refused_state{}; ///< Front: a call was open, or replay shadow RAM
    std::atomic<u64> refused_place{}; ///< Front: mappings, page crossing, not GPU-written
    std::atomic<u64> mismatched{};    ///< Front: an armed call met another call (read at once)
    std::array<std::atomic<u64>, 5> back_kind{}; ///< Back: by Nx2ReadListWordsOnBack's result
    std::atomic<u64> back_zero{};     ///< Back: draws with no vertices
    std::atomic<u64> back_wait_ns{};  ///< Back: time reading the words
    std::atomic<u64> trust_skips{};   ///< Front: list waits skipped on trusted pages
    std::atomic<u64> trust_waits{};   ///< Front: list waits kept (page not trusted yet)
    std::atomic<u64> trust_misses{};  ///< Back: a trusted page was GPU-written after all
    std::atomic<bool> trust_broken{};
};

inline Counters& Get() {
    static Counters counters;
    return counters;
}

/// Front thread: bit 0 EDEN_NX2_LIST_DRAW_BACK=1, bit 1 EDEN_NX2_TRUST_RING_PAGES=1; read again
/// about once a second.
inline u32 Switches() {
    static u32 flags{};
    static u32 calls{};
    static std::chrono::steady_clock::time_point checked{};
    if ((calls++ & 255) == 0) {
        const auto now = std::chrono::steady_clock::now();
        if (now - checked >= std::chrono::seconds{1}) {
            checked = now;
            flags = (VideoCommon::Nx2LiveSwitch("EDEN_NX2_LIST_DRAW_BACK") == "1" ? 1U : 0U) |
                    (VideoCommon::Nx2LiveSwitch("EDEN_NX2_TRUST_RING_PAGES") == "1" ? 2U : 0U);
        }
    }
    return flags;
}

/// Front thread: the command list pages read so far, by device page: reads that found the page
/// not GPU-written once the queued writers had run, or Distrusted.
inline std::unordered_map<u64, u32>& Pages() {
    static std::unordered_map<u64, u32> pages;
    return pages;
}

/// Back thread: a read the front made without waiting was of a page GPU work wrote.
inline void NoteTrustMiss(u64 page) {
    auto& counters = Get();
    counters.trust_misses.fetch_add(1, std::memory_order_relaxed);
    if (!counters.trust_broken.exchange(true)) {
        LOG_ERROR(HW_GPU,
                  "Nx2 list draw: command list page {:#x} was read without waiting and GPU work "
                  "wrote it; EDEN_NX2_TRUST_RING_PAGES is off until Eden restarts",
                  page);
    }
}

/// Front thread, every 10 s with anything to tell.
inline void MaybeLog() {
    static std::chrono::steady_clock::time_point last{};
    static u32 calls{};
    if ((calls++ & 1023) != 0) {
        return;
    }
    const auto now = std::chrono::steady_clock::now();
    if (last == std::chrono::steady_clock::time_point{}) {
        last = now;
        return;
    }
    if (now - last < std::chrono::seconds{10}) {
        return;
    }
    last = now;
    auto& c = Get();
    const auto take = [](std::atomic<u64>& value) {
        return value.exchange(0, std::memory_order_relaxed);
    };
    const u64 taken = take(c.taken);
    const u64 shape = take(c.refused_shape);
    const u64 state = take(c.refused_state);
    const u64 place = take(c.refused_place);
    const u64 mismatched = take(c.mismatched);
    std::array<u64, 5> kinds{};
    for (size_t i = 0; i < kinds.size(); ++i) {
        kinds[i] = take(c.back_kind[i]);
    }
    const u64 zero = take(c.back_zero);
    const u64 wait_ns = take(c.back_wait_ns);
    const u64 skips = take(c.trust_skips);
    const u64 waits = take(c.trust_waits);
    const u64 misses = take(c.trust_misses);
    if (taken + shape + state + place + mismatched + skips + misses == 0 && Switches() == 0) {
        return;
    }
    LOG_INFO(HW_GPU,
             "list draw 10 s: {} GPU-written draw calls sent to the back (refused: {} not the "
             "forwarding draw, {} open call or replay, {} placement; {} mismatched) | back read "
             "them: {} from a finished copy, {} after waiting for the copy, {} from memory "
             "(synced page), {} after flushing the page, {} unread; {} with no vertices; {:.1f} ms "
             "reading | ring pages: {} list waits skipped, {} kept, {} misses{}, {} pages known",
             taken, shape, state, place, mismatched, kinds[1], kinds[2], kinds[3], kinds[4],
             kinds[0], zero, static_cast<double>(wait_ns) / 1e6, skips, waits, misses,
             c.trust_broken.load(std::memory_order_relaxed) ? " (trust off)" : "",
             Pages().size());
}

} // namespace Tegra::Nx2ListDraw
