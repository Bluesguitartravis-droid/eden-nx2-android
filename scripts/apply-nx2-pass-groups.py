#!/usr/bin/env python3
"""Frame trace: one DKB gpu_pass_groups line per sampled frame that totals every pass segment by
extent and draw count (0, 1, 2-9, 10+), plus the segments after a submission's last pass. The
existing report lists only the 16 costliest segments; at the Odin's Hyrule Field ~220 of ~240
passes were below that and added up to ~20 of ~52 ms."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan/vk_scheduler.cpp"
t = f.read_text()
if "gpu_pass_groups" in t:
    print("vk_scheduler.cpp: already patched")
    sys.exit(0)
old = """    std::ranges::sort(segments, std::greater<>{}, &Segment::ms);
    LOG_INFO(Render_Vulkan, "DKB gpu_passes renderer_frame={} passes={}{} timed_ms={:.3f}","""
new = """    std::ranges::sort(segments, std::greater<>{}, &Segment::ms);
    {
        // NX2 pass groups (scripts/apply-nx2-pass-groups.py)
        struct Group {
            u32 count{};
            double ms{};
            u64 draws{};
            u64 dispatches{};
        };
        std::map<std::string, Group> groups;
        for (const auto& [ms, pass, tail] : segments) {
            const u32 draws = pass->draws;
            const char* const bucket =
                draws == 0 ? "0" : draws == 1 ? "1" : draws < 10 ? "2-9" : "10+";
            const std::string key =
                tail ? std::string{"tail"}
                     : fmt::format("{}x{}/{}", pass->extent.width, pass->extent.height, bucket);
            auto& group = groups[key];
            ++group.count;
            group.ms += ms;
            group.draws += draws;
            group.dispatches += pass->dispatches;
        }
        std::vector<std::pair<std::string, Group>> sorted(groups.begin(), groups.end());
        std::ranges::sort(sorted, std::greater<>{}, [](const auto& entry) { return entry.second.ms; });
        std::string text;
        for (const auto& [key, group] : sorted) {
            text += fmt::format(" {}:{}x/{:.2f}ms/{}d/{}c", key, group.count, group.ms, group.draws,
                                group.dispatches);
        }
        LOG_INFO(Render_Vulkan, "DKB gpu_pass_groups renderer_frame={} (extent/draws:passes/ms/draws/"
                 "dispatches){}", trace_sample_frame, text);
    }
    LOG_INFO(Render_Vulkan, "DKB gpu_passes renderer_frame={} passes={}{} timed_ms={:.3f}","""
if t.count(old) != 1:
    sys.exit("vk_scheduler.cpp: ReportPassTimes sort not found")
t = t.replace(old, new, 1)
if "#include <map>" not in t:
    t = t.replace("#include <algorithm>", "#include <algorithm>\n#include <map>", 1)
f.write_text(t)
print("vk_scheduler.cpp: DKB gpu_pass_groups line per traced frame")
