// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <algorithm>
#include <array>
#include <string>
#include <string_view>

#include "common/common_types.h"
#include "common/settings.h"

// Ocarina of Time foliage options in the game's Add-Ons list (scripts/apply-nx2-foliage-addons.py):
// built-ins with no code patch, off until checked, read when the game starts.
namespace Nx2FoliageAddons {

inline constexpr u64 kOcarina = 0x0400486024DEC000ULL;

/// The foliage layers' RomFS switches at "lowest" (as EDEN_NX2_OOT_DETAIL=lowest).
inline constexpr std::string_view kLighter = "Lighter Foliage (built-in)";
/// Skips the emulated mesh draws of the tall grass (kPerformanceHashes).
inline constexpr std::string_view kPerformance = "Performance Grass (built-in)";
/// Skips every emulated mesh draw (as EDEN_NX2_SKIP_MESH=all).
inline constexpr std::string_view kNone = "No Grass or Foliage (built-in)";

/// Mesh programs (stage slot 1 hashes) of Ocarina's tall grass, measured in Hyrule Field on the
/// Odin 3 (claude/fps-2026-11-26-mesh.md): 22.0 -> 25.1 fps with these three skipped.
inline constexpr std::array<u64, 3> kPerformanceHashes{
    0xa1d5ac7abfaf7871ULL, 0xe43a43842fbdc6c0ULL, 0x932cdcbb68ac02bcULL};

/// Whether the off-by-default built-in `name` is checked in `title_id`'s Add-Ons (stored in
/// disabled_addons as "+name", FileSys::EnabledAddonKey). Read without inserting.
[[nodiscard]] inline bool Enabled(u64 title_id, std::string_view name) {
    const auto& addons = Settings::values.disabled_addons;
    const auto it = addons.find(title_id);
    if (it == addons.end()) {
        return false;
    }
    const std::string key = std::string{"+"} + std::string{name};
    return std::ranges::find(it->second, key) != it->second.end();
}

} // namespace Nx2FoliageAddons
