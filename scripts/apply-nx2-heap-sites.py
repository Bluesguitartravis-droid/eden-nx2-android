#!/usr/bin/env python3
"""NX2_HEAP: which code holds the emulator's C++ memory.

Build 194 on the Odin (Ocarina): memory_breakdown showed [anon:mimalloc] (every operator new in
liblichii-android, apply-android-mimalloc.py) at 3.6 GB in the menus and 7.3 GB after loading
Hyrule Field, next to 8 GB of guest memory; Android closed Eden at 19.5 GB. Nothing said which
code the 7 GB belongs to.

This replaces the operator new/delete file with one that still allocates from mimalloc and also
records every block of 64 KiB or more: its size and the code that asked for it (a short
backtrace). Every 10 s a thread logs, when the total moved by 64 MiB or a minute passed:

  NX2_HEAP committed=7412 MiB tracked=6950 MiB in 18320 blocks (>= 64 KiB), dropped 0
  NX2_HEAP #1 2810 MiB 9112x <- _ZN13VideoCommon12TextureCache... <- ...

The bookkeeping uses fixed static tables (no allocation inside operator new) and a spin lock
taken only for blocks of 64 KiB or more. EDEN_NX2_HEAP_SITES=0 (a switch) turns it off.
Run after apply-android-mimalloc.py.
Usage: python3 scripts/apply-nx2-heap-sites.py <upstream-root>
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
jni = root / "src/android/app/src/main/jni"
target = jni / "nx2_mimalloc_new_delete.cpp"
MARK = "NX2_HEAP"
if not target.exists():
    raise SystemExit("run apply-android-mimalloc.py first")
if MARK in target.read_text():
    print("heap sites already applied"); sys.exit(0)

target.write_text(r'''// Eden NX2 Android: route operator new/delete in liblichii-android.so to mimalloc.
// libc++ is linked statically and the library uses -Bsymbolic, so these
// definitions replace libc++'s for every allocation made by the emulator.
//
// NX2_HEAP (apply-nx2-heap-sites.py): blocks of 64 KiB or more are recorded with the code that
// asked for them, and a thread logs the code holding the most memory every 10 s.
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <new>
#include <string>
#include <thread>

#include <dlfcn.h>
#include <unwind.h>

#include <mimalloc.h>

#include "common/logging.h"

namespace {

constexpr size_t kThreshold = size_t{64} << 10;
constexpr size_t kFrames = 10;
constexpr size_t kLiveCapacity = size_t{1} << 18; // live blocks (power of two)
constexpr size_t kSiteCapacity = 4096;             // distinct backtraces
// Smaller blocks: one is sampled about every kSampleBytes allocated on a thread and counted as
// kSampleBytes (an estimate of the memory held through that backtrace). A free checks a counter
// per 64 KiB page first (lock-free), so only pages holding a sampled block take the lock.
constexpr int64_t kSampleBytes = int64_t{256} << 10;
constexpr size_t kPageMarks = size_t{1} << 20;

struct Live {
    uintptr_t ptr; // 0 empty (linear probing, back-shift deletion: no tombstones)
    size_t size;   // bytes for a large block, kSampleBytes for a sampled small one
    uint32_t site;
    bool sampled;
};

struct Site {
    uintptr_t pcs[kFrames];
    uint32_t frames;
    size_t live_bytes;
    size_t live_blocks;
    bool used;
};

Live g_live[kLiveCapacity];
Site g_sites[kSiteCapacity];
std::atomic_flag g_lock = ATOMIC_FLAG_INIT;
size_t g_tracked_bytes = 0;
size_t g_tracked_blocks = 0;
size_t g_dropped = 0;
size_t g_sampled_bytes = 0; // estimate held by sampled small blocks
std::atomic<uint16_t> g_page_marks[kPageMarks];
thread_local bool t_inside = false;
thread_local int64_t t_countdown = kSampleBytes;
thread_local uint64_t t_random = 0;

size_t PageMark(uintptr_t ptr) {
    uintptr_t page = ptr >> 16;
    page ^= page >> 29;
    page *= 0xbf58476d1ce4e5b9ULL;
    return static_cast<size_t>(page >> 44) & (kPageMarks - 1);
}

int64_t NextCountdown() {
    // Spread the samples (xorshift), so a fixed allocation pattern is not always hit or missed.
    if (t_random == 0) {
        t_random = reinterpret_cast<uintptr_t>(&t_random) | 1;
    }
    t_random ^= t_random << 13;
    t_random ^= t_random >> 7;
    t_random ^= t_random << 17;
    return kSampleBytes / 2 + static_cast<int64_t>(t_random % static_cast<uint64_t>(kSampleBytes));
}

// 0 unknown, 1 on, 2 off (EDEN_NX2_HEAP_SITES=0)
std::atomic<int> g_state{0};

bool Enabled() {
    int state = g_state.load(std::memory_order_relaxed);
    if (state == 0) {
        const char* value = std::getenv("EDEN_NX2_HEAP_SITES");
        state = (value != nullptr && value[0] == '0') ? 2 : 1;
        g_state.store(state, std::memory_order_relaxed);
    }
    return state == 1;
}

struct Lock {
    Lock() { while (g_lock.test_and_set(std::memory_order_acquire)) {} }
    ~Lock() { g_lock.clear(std::memory_order_release); }
};

size_t Hash(uintptr_t value) {
    value ^= value >> 33;
    value *= 0xff51afd7ed558ccdULL;
    value ^= value >> 33;
    return static_cast<size_t>(value);
}

struct UnwindState {
    uintptr_t* pcs;
    uint32_t count;
};

_Unwind_Reason_Code UnwindStep(_Unwind_Context* context, void* arg) {
    auto* state = static_cast<UnwindState*>(arg);
    const uintptr_t pc = _Unwind_GetIP(context);
    if (pc == 0) {
        return _URC_END_OF_STACK;
    }
    state->pcs[state->count++] = pc;
    return state->count < kFrames ? _URC_NO_REASON : _URC_END_OF_STACK;
}

uint32_t FindSite(const uintptr_t* pcs, uint32_t frames) {
    uintptr_t key = frames;
    for (uint32_t i = 0; i < frames; ++i) {
        key = key * 31 + pcs[i];
    }
    for (size_t probe = 0; probe < kSiteCapacity; ++probe) {
        const size_t index = (Hash(key) + probe) & (kSiteCapacity - 1);
        Site& site = g_sites[index];
        if (!site.used) {
            site.used = true;
            std::memcpy(site.pcs, pcs, sizeof(uintptr_t) * frames);
            site.frames = frames;
            return static_cast<uint32_t>(index);
        }
        if (site.frames == frames && std::memcmp(site.pcs, pcs, sizeof(uintptr_t) * frames) == 0) {
            return static_cast<uint32_t>(index);
        }
    }
    return UINT32_MAX;
}

void Record(void* p, size_t n) {
    if (p == nullptr || t_inside) {
        return;
    }
    bool sampled = false;
    if (n < kThreshold) {
        t_countdown -= static_cast<int64_t>(n);
        if (t_countdown > 0) {
            return;
        }
        t_countdown = NextCountdown();
        sampled = true;
    }
    if (!Enabled()) {
        return;
    }
    t_inside = true;
    uintptr_t pcs[kFrames];
    UnwindState state{pcs, 0};
    _Unwind_Backtrace(UnwindStep, &state);
    {
        Lock lock;
        const uint32_t site = FindSite(pcs, state.count);
        const uintptr_t key = reinterpret_cast<uintptr_t>(p);
        bool stored = false;
        if (site != UINT32_MAX && g_tracked_blocks < kLiveCapacity / 2) {
            for (size_t probe = 0; probe < kLiveCapacity; ++probe) {
                Live& slot = g_live[(Hash(key) + probe) & (kLiveCapacity - 1)];
                if (slot.ptr == 0) {
                    const size_t weight = sampled ? static_cast<size_t>(kSampleBytes) : n;
                    slot = Live{key, weight, site, sampled};
                    g_sites[site].live_bytes += weight;
                    g_sites[site].live_blocks += 1;
                    if (sampled) {
                        g_sampled_bytes += weight;
                        g_page_marks[PageMark(key)].fetch_add(1, std::memory_order_relaxed);
                    } else {
                        g_tracked_bytes += weight;
                    }
                    g_tracked_blocks += 1;
                    stored = true;
                    break;
                }
            }
        }
        if (!stored) {
            ++g_dropped;
        }
    }
    t_inside = false;
}

void Forget(void* p) {
    if (p == nullptr || g_state.load(std::memory_order_relaxed) != 1) {
        return;
    }
    const uintptr_t key = reinterpret_cast<uintptr_t>(p);
    if (g_page_marks[PageMark(key)].load(std::memory_order_relaxed) == 0 &&
        mi_usable_size(p) < kThreshold) {
        return; // neither large nor in a page holding a sampled block
    }
    constexpr size_t mask = kLiveCapacity - 1;
    Lock lock;
    size_t hole = Hash(key) & mask;
    while (true) {
        if (g_live[hole].ptr == 0) {
            return; // not tracked (under the threshold when asked for, or dropped)
        }
        if (g_live[hole].ptr == key) {
            break;
        }
        hole = (hole + 1) & mask;
    }
    Site& site = g_sites[g_live[hole].site];
    site.live_bytes -= g_live[hole].size;
    site.live_blocks -= 1;
    if (g_live[hole].sampled) {
        g_sampled_bytes -= g_live[hole].size;
        g_page_marks[PageMark(key)].fetch_sub(1, std::memory_order_relaxed);
    } else {
        g_tracked_bytes -= g_live[hole].size;
    }
    g_tracked_blocks -= 1;
    g_live[hole].ptr = 0;
    // Back-shift: move later entries of the run into the hole when their home allows it.
    size_t next = (hole + 1) & mask;
    while (g_live[next].ptr != 0) {
        const size_t home = Hash(g_live[next].ptr) & mask;
        // The entry may move to the hole if its home is not in (hole, next] cyclically.
        const bool between = hole <= next ? (home > hole && home <= next)
                                          : (home > hole || home <= next);
        if (!between) {
            g_live[hole] = g_live[next];
            g_live[next].ptr = 0;
            hole = next;
        }
        next = (next + 1) & mask;
    }
}

// Frames inside the allocator, libc++ and this file say nothing about who holds the memory.
bool Boring(const char* name) {
    if (name == nullptr) {
        return false;
    }
    static const char* const prefixes[] = {"_Znw", "_Zna", "_ZnwmSt", "_ZNSt6__ndk1", "mi_",
                                           "_Unwind", "_ZN12_GLOBAL__N_1"};
    for (const char* prefix : prefixes) {
        if (std::strncmp(name, prefix, std::strlen(prefix)) == 0) {
            return true;
        }
    }
    return false;
}

std::string Describe(uintptr_t pc) {
    Dl_info info{};
    if (dladdr(reinterpret_cast<void*>(pc), &info) == 0 || info.dli_fname == nullptr) {
        char text[32];
        std::snprintf(text, sizeof(text), "%#lx", static_cast<unsigned long>(pc));
        return text;
    }
    const char* slash = std::strrchr(info.dli_fname, '/');
    char text[512];
    std::snprintf(text, sizeof(text), "%s+%#lx %s", slash ? slash + 1 : info.dli_fname,
                  static_cast<unsigned long>(pc - reinterpret_cast<uintptr_t>(info.dli_fbase)),
                  info.dli_sname ? info.dli_sname : "?");
    return text;
}

bool BoringPc(uintptr_t pc) {
    Dl_info info{};
    return dladdr(reinterpret_cast<void*>(pc), &info) != 0 && Boring(info.dli_sname);
}

void Report(bool force) {
    struct Top {
        size_t bytes;
        size_t blocks;
        uint32_t site;
    };
    Top top[12]{};
    size_t tracked = 0;
    size_t sampled = 0;
    size_t blocks = 0;
    size_t dropped = 0;
    Site copies[12];
    {
        Lock lock;
        tracked = g_tracked_bytes;
        sampled = g_sampled_bytes;
        blocks = g_tracked_blocks;
        dropped = g_dropped;
        for (uint32_t index = 0; index < kSiteCapacity; ++index) {
            const Site& site = g_sites[index];
            if (!site.used || site.live_bytes == 0) {
                continue;
            }
            for (size_t slot = 0; slot < 12; ++slot) {
                if (site.live_bytes > top[slot].bytes) {
                    for (size_t move = 11; move > slot; --move) {
                        top[move] = top[move - 1];
                    }
                    top[slot] = Top{site.live_bytes, site.live_blocks, index};
                    break;
                }
            }
        }
        for (size_t slot = 0; slot < 12; ++slot) {
            if (top[slot].bytes != 0) {
                copies[slot] = g_sites[top[slot].site];
            }
        }
    }
    // (mi_process_info's commit figure underflows without MI_STAT; not shown.)
    LOG_INFO(Frontend,
             "NX2_HEAP large={} MiB (blocks >= 64 KiB, exact) small~{} MiB (sampled every ~256 KiB) "
             "in {} entries, dropped {}{}",
             tracked >> 20, sampled >> 20, blocks, dropped, force ? "" : " (moved)");
    for (size_t slot = 0; slot < 12 && top[slot].bytes != 0; ++slot) {
        std::string where;
        int shown = 0;
        for (uint32_t frame = 0; frame < copies[slot].frames && shown < 4; ++frame) {
            const uintptr_t pc = copies[slot].pcs[frame];
            if (BoringPc(pc)) {
                continue;
            }
            where += " <- " + Describe(pc);
            ++shown;
        }
        LOG_INFO(Frontend, "NX2_HEAP #{} {} MiB {} entries{}", slot + 1, top[slot].bytes >> 20,
                 top[slot].blocks, where);
    }
}

struct Reporter {
    Reporter() {
        std::thread([] {
            // After the app has set up its log.
            std::this_thread::sleep_for(std::chrono::seconds(20));
            size_t last = 0;
            int quiet = 0;
            while (true) {
                std::this_thread::sleep_for(std::chrono::seconds(10));
                if (g_state.load(std::memory_order_relaxed) == 2) {
                    return;
                }
                size_t tracked = 0;
                {
                    Lock lock;
                    tracked = g_tracked_bytes;
                }
                {
                    Lock lock;
                    tracked += g_sampled_bytes;
                }
                const size_t moved = tracked > last ? tracked - last : last - tracked;
                if (moved >= (size_t{64} << 20) || ++quiet >= 6) {
                    Report(moved < (size_t{64} << 20));
                    last = tracked;
                    quiet = 0;
                }
            }
        }).detach();
    }
};
Reporter g_reporter;

} // namespace

void operator delete(void* p) noexcept { Forget(p); mi_free(p); }
void operator delete[](void* p) noexcept { Forget(p); mi_free(p); }
void operator delete(void* p, const std::nothrow_t&) noexcept { Forget(p); mi_free(p); }
void operator delete[](void* p, const std::nothrow_t&) noexcept { Forget(p); mi_free(p); }
void* operator new(std::size_t n) noexcept(false) { void* p = mi_new(n); Record(p, n); return p; }
void* operator new[](std::size_t n) noexcept(false) { void* p = mi_new(n); Record(p, n); return p; }
void* operator new(std::size_t n, const std::nothrow_t&) noexcept {
    void* p = mi_new_nothrow(n); Record(p, n); return p;
}
void* operator new[](std::size_t n, const std::nothrow_t&) noexcept {
    void* p = mi_new_nothrow(n); Record(p, n); return p;
}
void operator delete(void* p, std::size_t n) noexcept { Forget(p); mi_free_size(p, n); }
void operator delete[](void* p, std::size_t n) noexcept { Forget(p); mi_free_size(p, n); }
void operator delete(void* p, std::align_val_t al) noexcept {
    Forget(p); mi_free_aligned(p, static_cast<size_t>(al));
}
void operator delete[](void* p, std::align_val_t al) noexcept {
    Forget(p); mi_free_aligned(p, static_cast<size_t>(al));
}
void operator delete(void* p, std::size_t n, std::align_val_t al) noexcept {
    Forget(p); mi_free_size_aligned(p, n, static_cast<size_t>(al));
}
void operator delete[](void* p, std::size_t n, std::align_val_t al) noexcept {
    Forget(p); mi_free_size_aligned(p, n, static_cast<size_t>(al));
}
void operator delete(void* p, std::align_val_t al, const std::nothrow_t&) noexcept {
    Forget(p); mi_free_aligned(p, static_cast<size_t>(al));
}
void operator delete[](void* p, std::align_val_t al, const std::nothrow_t&) noexcept {
    Forget(p); mi_free_aligned(p, static_cast<size_t>(al));
}
void* operator new(std::size_t n, std::align_val_t al) noexcept(false) {
    void* p = mi_new_aligned(n, static_cast<size_t>(al)); Record(p, n); return p;
}
void* operator new[](std::size_t n, std::align_val_t al) noexcept(false) {
    void* p = mi_new_aligned(n, static_cast<size_t>(al)); Record(p, n); return p;
}
void* operator new(std::size_t n, std::align_val_t al, const std::nothrow_t&) noexcept {
    void* p = mi_new_aligned_nothrow(n, static_cast<size_t>(al)); Record(p, n); return p;
}
void* operator new[](std::size_t n, std::align_val_t al, const std::nothrow_t&) noexcept {
    void* p = mi_new_aligned_nothrow(n, static_cast<size_t>(al)); Record(p, n); return p;
}
''')
print("NX2_HEAP allocation sites installed")
