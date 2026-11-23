#!/usr/bin/env python3
"""EDEN_NX2_SKIP_SHADERS=<hex prefix>[,<hex prefix>...] (measurement aid, live): drop every draw
whose pipeline has a shader hash starting with one of the prefixes (any stage, as in the frame
trace's s1:/s5: hashes), and every compute dispatch whose shader hash does. Read again from the
switches file every 2 s, so set_switches turns effects off and on without restarting Eden.
NX2_SKIP_SHADERS every 10 s counts what it dropped. Used to price the game's effects one by one."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"

header = root / "nx2_live_switch.h"
header.write_text('''// SPDX-License-Identifier: GPL-3.0-or-later
// Switches read again from the Lab's switches file while Eden runs (scripts/apply-nx2-skip-shaders.py).
#pragma once

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include "common/common_types.h"

namespace VideoCommon {

/// The value of NAME in the switches file (or the environment until the file names it), read
/// again at most every 2 s. Empty when unset.
inline std::string Nx2LiveSwitch(const char* name) {
    struct Entry {
        std::string name;
        std::string value;
        std::chrono::steady_clock::time_point checked{};
    };
    static std::mutex mutex;
    static std::vector<Entry> entries;
    std::scoped_lock lock{mutex};
    Entry* entry = nullptr;
    for (auto& candidate : entries) {
        if (candidate.name == name) {
            entry = &candidate;
        }
    }
    if (entry == nullptr) {
        const char* const env = std::getenv(name);
        entry = &entries.emplace_back(Entry{name, env != nullptr ? env : ""});
    }
    const auto now = std::chrono::steady_clock::now();
    if (now - entry->checked >= std::chrono::seconds{2}) {
        entry->checked = now;
        if (std::FILE* const file = std::fopen(
                "/storage/emulated/0/Android/data/dev.eden.eden_emulator/files/nx2_switches.txt", "r")) {
            const std::string key = std::string{name} + "=";
            std::string found;
            char line[1024];
            while (std::fgets(line, sizeof(line), file) != nullptr) {
                if (std::strncmp(line, key.c_str(), key.size()) == 0) {
                    found = line + key.size();
                    while (!found.empty() && (found.back() == '\\n' || found.back() == '\\r')) {
                        found.pop_back();
                    }
                }
            }
            std::fclose(file);
            entry->value = found;
        }
    }
    return entry->value;
}

/// EDEN_NX2_SKIP_SHADERS: whether a shader hash starts with one of the listed hex prefixes.
inline bool Nx2SkipShaderHash(u64 hash) {
    static std::mutex mutex;
    static std::string parsed_from;
    static std::vector<std::pair<u64, u32>> prefixes;
    static std::atomic<bool> active{false};
    static std::atomic<s64> refreshed_at{-1};
    // Fast path: one atomic load per call while nothing is listed; the list is looked at again
    // every 2 s (the draw path calls this ~40,000 times a second).
    const s64 now = std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now().time_since_epoch())
                        .count();
    if (now - refreshed_at.load(std::memory_order_relaxed) < 2000 &&
        !active.load(std::memory_order_relaxed)) {
        return false;
    }
    std::scoped_lock lock{mutex};
    if (now - refreshed_at.load(std::memory_order_relaxed) >= 2000) {
        refreshed_at.store(now, std::memory_order_relaxed);
        const std::string value = Nx2LiveSwitch("EDEN_NX2_SKIP_SHADERS");
        if (value != parsed_from) {
            parsed_from = value;
            prefixes.clear();
            size_t start = 0;
            while (start < value.size()) {
                size_t end = value.find(',', start);
                if (end == std::string::npos) {
                    end = value.size();
                }
                const std::string item = value.substr(start, end - start);
                if (!item.empty() && item.size() <= 16) {
                    prefixes.emplace_back(std::strtoull(item.c_str(), nullptr, 16),
                                          static_cast<u32>(item.size()) * 4);
                }
                start = end + 1;
            }
        }
        active.store(!prefixes.empty(), std::memory_order_relaxed);
    }
    for (const auto& [prefix, bits] : prefixes) {
        if (bits != 0 && (hash >> (64 - bits)) == prefix) {
            return true;
        }
    }
    return false;
}

} // namespace VideoCommon
''')

f = root / "renderer_vulkan/vk_rasterizer.cpp"
t = f.read_text()
if "Nx2SkipShaderHash" in t:
    print("vk_rasterizer.cpp: already patched")
    sys.exit(0)

def edit(old, new):
    global t
    if t.count(old) != 1:
        sys.exit(f"vk_rasterizer.cpp: expected one match for {old[:70]!r}, found {t.count(old)}")
    t = t.replace(old, new, 1)

t = t.replace('#include "video_core/renderer_vulkan/vk_rasterizer.h"',
              '#include "video_core/renderer_vulkan/vk_rasterizer.h"\n#include "video_core/nx2_live_switch.h"', 1)
edit("""template <typename Func>
void RasterizerVulkan::PrepareDraw(bool is_indexed""", """namespace {
std::atomic<u64> nx2_skipped_draws{};
std::atomic<u64> nx2_skipped_dispatches{};
void Nx2ReportSkips() {
    static std::atomic<s64> last{};
    const s64 now = std::chrono::duration_cast<std::chrono::seconds>(
                        std::chrono::steady_clock::now().time_since_epoch())
                        .count();
    if (now - last.load(std::memory_order_relaxed) < 10) {
        return;
    }
    last.store(now, std::memory_order_relaxed);
    const u64 draws = nx2_skipped_draws.exchange(0);
    const u64 dispatches = nx2_skipped_dispatches.exchange(0);
    if (draws != 0 || dispatches != 0) {
        LOG_INFO(Render_Vulkan, "NX2_SKIP_SHADERS 10s: {} draws, {} dispatches dropped ({})", draws,
                 dispatches, VideoCommon::Nx2LiveSwitch("EDEN_NX2_SKIP_SHADERS"));
    }
}
} // Anonymous namespace

template <typename Func>
void RasterizerVulkan::PrepareDraw(bool is_indexed""")
edit("""    if (!pipeline) {
        report_failure("no pipeline");""", """    if (pipeline != nullptr) {
        // NX2 skip shaders (scripts/apply-nx2-skip-shaders.py)
        Nx2ReportSkips();
        for (const u64 hash : pipeline->ShaderHashes()) {
            if (hash != 0 && VideoCommon::Nx2SkipShaderHash(hash)) {
                nx2_skipped_draws.fetch_add(1, std::memory_order_relaxed);
                return;
            }
        }
    }
    if (!pipeline) {
        report_failure("no pipeline");""")
edit("""    if (!pipeline) {
        if (kepler_compute->IsNativeMode()) throw Shader::RuntimeError("Native compute pipeline unavailable");
        return;
    }""", """    if (!pipeline) {
        if (kepler_compute->IsNativeMode()) throw Shader::RuntimeError("Native compute pipeline unavailable");
        return;
    }
    if (VideoCommon::Nx2SkipShaderHash(pipeline->ShaderHash())) {
        nx2_skipped_dispatches.fetch_add(1, std::memory_order_relaxed);
        Nx2ReportSkips();
        return;
    }""")
f.write_text(t)
print("vk_rasterizer.cpp: live EDEN_NX2_SKIP_SHADERS for draws and dispatches")
