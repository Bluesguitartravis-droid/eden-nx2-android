#!/usr/bin/env python3
"""GPU-written draw lists without the split's front waiting (both switches live, both off unless set).

Ocarina's culling writes a draw's vertex count and first vertex as the two parameters of 3D macro
0xe62, which only forwards them (draw begin 0, vertex_buffer.first, vertex_buffer.count, draw end 0):
about 1,000 lists a second that Nx2ListIndirect keeps ("3D 0xe62 x2 words"). The front waits for the
culling to run on the back before it parses them, and the ring entries before them wait for the same
work (list writers, ~145 ms of every second), while the back starves (~290 ms).

EDEN_NX2_LIST_DRAW_BACK=1: a list that is all of the parameters of a 3D macro call whose dry run
(Nx2ListIndirect::RunTagged) only forwards them that way is sent to the back as one call with the
words' device address. The back reads the words at the list's place in the stream (the buffer cache's
list copy, waiting for its tick itself; guest memory for a synced page; a page flush otherwise) and
makes the four writes and the deferred draw as the macro's writes and FinishFrontMacro would. The
front keeps vertex_buffer.first and .count as back-owned registers (as after FrontRunOnBack).

EDEN_NX2_TRUST_RING_PAGES=1: a command list page read 2,048 times without being GPU-written once
the queued writers had run is read without WaitForSyncedWriters. The back checks every such read at
its place; a miss logs an error and turns the trust off until Eden restarts.

Counters: "list draw 10 s: ..." in the log while either switch is set."""
from pathlib import Path
import shutil
import sys

here = Path(__file__).resolve().parent
root = Path(sys.argv[1]).resolve() / "src/video_core"


def patch(name, pairs):
    path = root / name
    text = path.read_text()
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit(f"apply-nx2-list-draw-back: expected one {old[:70]!r} in {name}, found {text.count(old)}")
        text = text.replace(old, new)
    path.write_text(text)


shutil.copyfile(here / "nx2_list_draw.h", root / "nx2_list_draw.h")

# --- GpuSplit: whether a render follower plays the back's draws (its draw.end path is skipped).
patch("gpu_split.h", [
    ('''    [[nodiscard]] u64 LastMappingChange() const {
        return (std::min)(last_mapping_change, write_position);
    }
''', '''    [[nodiscard]] u64 LastMappingChange() const {
        return (std::min)(last_mapping_change, write_position);
    }
    /// Whether GPURender follows the back (EDEN_NX2_RENDER_RING and the switches built on it).
    [[nodiscard]] bool Nx2HasRenderFollower() const {
        return render_follower.has_value();
    }
'''),
])

# --- Rasterizer: the back reads a GPU-written list's words at the list's place.
patch("rasterizer_interface.h", [
    ('''    /// The back: the words of `slots`, three each, read back from the host GPU (waits for it).
''', '''    /// The back, at the place in the stream of a GPU-written list at `addr` (on one page): its
    /// words into `out`, as the work run before left them. Returns how: 1 from the list copy,
    /// 2 from it after waiting for the host GPU, 3 from guest memory (a synced page), 4 from
    /// guest memory after flushing the page, 0 not read (`out` unchanged).
    virtual u32 Nx2ReadListWordsOnBack([[maybe_unused]] DAddr addr,
                                       [[maybe_unused]] std::span<u8> out) {
        return 0;
    }
    /// The back: the words of `slots`, three each, read back from the host GPU (waits for it).
'''),
])
patch("renderer_vulkan/vk_rasterizer.h", [
    ('''    void Nx2ReadListSlots(std::span<const u32> slots, std::span<u32> words) override;
''', '''    void Nx2ReadListSlots(std::span<const u32> slots, std::span<u32> words) override;
    u32 Nx2ReadListWordsOnBack(DAddr addr, std::span<u8> out) override;
'''),
])
patch("renderer_vulkan/vk_rasterizer.cpp", [
    ('''void RasterizerVulkan::Nx2ReadListSlots(std::span<const u32> slots, std::span<u32> words) {
    buffer_cache_runtime.Nx2Slots().Read(slots, words);
}
''', '''void RasterizerVulkan::Nx2ReadListSlots(std::span<const u32> slots, std::span<u32> words) {
    buffer_cache_runtime.Nx2Slots().Read(slots, words);
}

u32 RasterizerVulkan::Nx2ReadListWordsOnBack(DAddr addr, std::span<u8> out) {
    RENDER_LEND_RESULT(Nx2ReadListWordsOnBack(addr, out))
    // The back ran the work before the list: a page that work wrote has its copy recorded (and
    // submitted with the writer, or by the wait here), as DmaPusher::ReadGpuWrittenCommands
    // finds it once the front waited for the writers.
    using Read = BufferCache::Nx2ListRead;
    u64 tick = 0;
    Read read = buffer_cache.Nx2ReadListCopy(addr, out, true, &tick);
    u32 how = 1;
    if (read == Read::Pending) {
        scheduler.Wait(tick); // Submits a deferred or open tick first
        read = buffer_cache.Nx2ReadListCopy(addr, out, true, &tick);
        how = 2;
    }
    if (read == Read::Copied) {
        return how;
    }
    if (read == Read::Synced) {
        // No GPU work wrote the page since it was last written out: guest memory holds it.
        device_memory.ReadBlockUnsafe(addr, out.data(), out.size());
        return 3;
    }
    // No copy that holds the words: write the page out, as FlushGpuWrittenCommands does.
    const std::array<DAddr, 1> pages{addr & ~DAddr{0xfff}};
    FlushGpuWrittenCommandListPages(pages);
    device_memory.ReadBlockUnsafe(addr, out.data(), out.size());
    return 4;
}
'''),
])

# --- Maxwell3D: the front arms a list call, FrontRunMacro sends it to the back.
patch("engines/maxwell_3d.h", [
    ('''    void FrontCallMultiMethod(Core::System& system, VideoCommon::GpuSplit& split, u32 method,
                              const u32* base_start, u32 amount, u32 methods_pending);
''', '''    void FrontCallMultiMethod(Core::System& system, VideoCommon::GpuSplit& split, u32 method,
                              const u32* base_start, u32 amount, u32 methods_pending);
    /// EDEN_NX2_LIST_DRAW_BACK (nx2_list_draw.h), the front before it parses a GPU-written list
    /// of `count` words at `device` that is all the parameters of the call of macro `method`:
    /// whether the back takes the call (the macro only forwards two words as a draw's first
    /// vertex and count). If so the next call run must be that one (FrontListCallDone after it).
    bool FrontPrepareListCall(Core::System& system, VideoCommon::GpuSplit& split, u32 method,
                              u32 count, u64 device);
    void FrontListCallDone();
'''),
    ('''    void FrontRunMacro(Core::System& system, VideoCommon::GpuSplit& split, FrontState& state);
''', '''    void FrontRunMacro(Core::System& system, VideoCommon::GpuSplit& split, FrontState& state);
    bool FrontRunListCall(Core::System& system, VideoCommon::GpuSplit& split, FrontState& state,
                          u32 method);
    void BackRunListCall(Core::System& system, u64 device, u32 word0, u32 word1);
'''),
])
patch("engines/maxwell_3d.cpp", [
    ('''#include "video_core/nx2_list_indirect.h"
#include "video_core/nx2_list_indirect_stats.h"
''', '''#include "video_core/nx2_list_draw.h"
#include "video_core/nx2_list_indirect.h"
#include "video_core/nx2_list_indirect_stats.h"
'''),
    ('''    std::atomic<u32> back_escapes{}; ///< Back macros that wrote outside their write set
    u32 back_escapes_seen{};
};
''', '''    std::atomic<u32> back_escapes{}; ///< Back macros that wrote outside their write set
    u32 back_escapes_seen{};

    /// EDEN_NX2_LIST_DRAW_BACK: the call FrontPrepareListCall armed, whose parameters (parsed
    /// from guest memory as it was) the back reads again at `device`.
    struct ListCall {
        bool active{};
        u32 method{};
        u32 count{};
        u64 device{};
    };
    ListCall list_call;
    /// By start in the macro code of list_shape_generation: whether the macro only forwards its
    /// two parameters as a draw (begin 0, first = word 1, count = word 0, end 0).
    std::vector<std::pair<u32, bool>> list_shapes;
    u64 list_shape_generation{~u64{0}};
};
'''),
    ('''    const u32 start = positions[((method - MacroRegistersStart) >> 1) % positions.size()];
    VideoCommon::Nx2Profile::CountMethod(method, false);
    namespace ListTrace = VideoCommon::ListMacroTrace;
''', '''    const u32 start = positions[((method - MacroRegistersStart) >> 1) % positions.size()];
    VideoCommon::Nx2Profile::CountMethod(method, false);
    if (fs.list_call.active && FrontRunListCall(system, split, fs, method)) [[unlikely]] {
        return;
    }
    namespace ListTrace = VideoCommon::ListMacroTrace;
'''),
    ('''// A macro whose code reads memory into its FIFO (MME_DMA_READ_FIFOED) and writes only registers
// BackWritable allows runs on the back, at its point of the method stream: it reads the words as
''', '''bool Maxwell3D::FrontPrepareListCall(Core::System& system, VideoCommon::GpuSplit& split,
                                     u32 method, u32 count, u64 device) {
    auto& counters = Nx2ListDraw::Get();
    auto& fs = Front(system, split);
    if (fs.executing_macro != 0 || !fs.parameters.empty() || fs.list_call.active ||
        fs.control == Regs::ShadowRamControl::Replay) {
        counters.refused_state.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    const auto& engine = GetNativeMacroEngine();
    const auto positions = GetNativeMacroPositions();
    const u32 start = positions[((method - MacroRegistersStart) >> 1) % positions.size()];
    if (fs.list_shape_generation != engine.native_code_generation) {
        fs.list_shapes.clear();
        fs.list_shape_generation = engine.native_code_generation;
    }
    auto shape = std::ranges::find(fs.list_shapes, start, &std::pair<u32, bool>::first);
    if (shape == fs.list_shapes.end()) {
        // A dry run with both parameters tagged: it must read no register and no data RAM word
        // (its path then depends on nothing but its code), and emit exactly the four writes.
        const std::array<u32, 2> params{Nx2ListIndirect::Placeholder(0, 0),
                                        Nx2ListIndirect::Placeholder(0, 1)};
        const std::array<u8, 2> tagged{1, 1};
        bool read_any = false;
        Nx2ListIndirect::TaggedRun run;
        std::vector<TuringMacroInterpreter::Decoded> decoded;
        Nx2ListIndirect::RunTagged(
            std::span<const u32>{engine.native_code}, start, std::span<const u32>{params},
            std::span<const u8>{tagged},
            [&read_any](u32) -> std::pair<u32, bool> {
                read_any = true;
                return {0U, false};
            },
            [&read_any](u32) -> std::pair<u32, bool> {
                read_any = true;
                return {0U, false};
            },
            decoded, run);
        const auto is = [&run](size_t index, u32 address, u32 value, bool is_tagged) {
            const auto& emit = run.emits[index];
            return emit.address == address && emit.value == value && emit.tagged == is_tagged;
        };
        const bool forwards =
            run.status == Nx2ListIndirect::TaggedRun::Outcome::Done && !read_any &&
            run.data_ram.empty() && run.consumed == 2 && run.emits.size() == 4 &&
            is(0, MAXWELL3D_REG_INDEX(draw.begin), 0, false) &&
            is(1, MAXWELL3D_REG_INDEX(vertex_buffer.first), params[1], true) &&
            is(2, MAXWELL3D_REG_INDEX(vertex_buffer.count), params[0], true) &&
            is(3, MAXWELL3D_REG_INDEX(draw.end), 0, false);
        LOG_INFO(HW_GPU, "Nx2 list draw: 3D macro {:#x} at {:#x} {} its two parameters as a draw",
                 method, start, forwards ? "only forwards" : "does not just forward");
        fs.list_shapes.emplace_back(start, forwards);
        shape = fs.list_shapes.end() - 1;
    }
    if (!shape->second || count != 2) {
        counters.refused_shape.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    fs.list_call = {true, method, count, device};
    return true;
}

void Maxwell3D::FrontListCallDone() {
    if (front && front->list_call.active) [[unlikely]] {
        front->list_call.active = false;
        Nx2ListDraw::Get().mismatched.fetch_add(1, std::memory_order_relaxed);
        LOG_ERROR(HW_GPU, "Nx2 list draw: the armed call of macro {:#x} did not run",
                  front->list_call.method);
    }
}

// The armed call, at its last parameter. Returns whether it went to the back; else its
// parameters are the list's words, read as before, and the caller runs it.
bool Maxwell3D::FrontRunListCall(Core::System& system, VideoCommon::GpuSplit& split,
                                 FrontState& fs, u32 method) {
    using Control = Regs::ShadowRamControl;
    constexpr u32 begin = MAXWELL3D_REG_INDEX(draw.begin);
    constexpr u32 end = MAXWELL3D_REG_INDEX(draw.end);
    constexpr u32 first = MAXWELL3D_REG_INDEX(vertex_buffer.first);
    constexpr u32 vertex_count = MAXWELL3D_REG_INDEX(vertex_buffer.count);
    auto& counters = Nx2ListDraw::Get();
    const FrontState::ListCall call = fs.list_call;
    fs.list_call.active = false;
    if (method != call.method || call.count != 2 || fs.parameters.size() != call.count ||
        fs.control == Control::Replay) {
        // Not the call that was armed (it never is): the words now, as the old path reads them.
        counters.mismatched.fetch_add(1, std::memory_order_relaxed);
        LOG_ERROR(HW_GPU, "Nx2 list draw: armed macro {:#x} x{}, ran {:#x} x{}", call.method,
                  call.count, method, fs.parameters.size());
        if (call.count == 2 && fs.parameters.size() >= 2) {
            std::array<u32, 2> words{fs.parameters[fs.parameters.size() - 2],
                                     fs.parameters[fs.parameters.size() - 1]};
            split.Run(
                [this, device = call.device, out = words.data()] {
                    rasterizer->Nx2ReadListWordsOnBack(
                        device, std::span<u8>{reinterpret_cast<u8*>(out), 2 * sizeof(u32)});
                },
                VideoCommon::DrainSite::ListWriters);
            fs.parameters[fs.parameters.size() - 2] = words[0];
            fs.parameters[fs.parameters.size() - 1] = words[1];
        }
        return false;
    }
    // What FrontWrite does for the macro's four writes, without sending them: draw begin and
    // end hold 0 on both sides, first and count are the back's until the front writes them
    // (FrontTakeOver) or needs them (FrontRegister), as after FrontRunOnBack.
    const bool tracked = fs.control == Control::Track || fs.control == Control::TrackWithFilter;
    FrontTakeOver(split, fs, begin);
    FrontTakeOver(split, fs, end);
    FrontTrackInstanceLoop(fs, begin, 0);
    fs.regs[begin] = 0;
    fs.regs[end] = 0;
    if (tracked) {
        fs.shadow[begin] = 0;
        fs.shadow[end] = 0;
    }
    split.Defer(
        [this, system_ptr = &system, device = call.device, word0 = fs.parameters[0],
         word1 = fs.parameters[1]] { BackRunListCall(*system_ptr, device, word0, word1); },
        0, VideoCommon::GpuSplit::Writes::Caches);
    const u64 position = split.Position();
    for (const u32 written : {first, vertex_count}) {
        fs.back_regs[written] = position;
        if (tracked) {
            fs.back_shadow[written] = position;
        }
        fs.back_owned[written] = true;
    }
    // The back consumed its sink and ran DrawDeferred (FinishFrontMacro's part).
    fs.sink_pending = false;
    fs.draw_pending = false;
    fs.parameters.clear();
    counters.taken.fetch_add(1, std::memory_order_relaxed);
    if (FrontVerifyEnabled() && (++fs.calls & 4095) == 0) [[unlikely]] {
        FrontVerify(split, fs);
    }
    return true;
}

// The back, at the list's place: the words as the work before left them, then the macro's
// writes (each as a MacroWrite entry runs: sink consumed, CallMethod) and FinishFrontMacro.
void Maxwell3D::BackRunListCall(Core::System& system, u64 device, u32 word0, u32 word1) {
    auto& counters = Nx2ListDraw::Get();
    ConsumeSink(system);
    std::array<u32, 2> words{word0, word1};
    const auto start = std::chrono::steady_clock::now();
    const u32 how = rasterizer->Nx2ReadListWordsOnBack(
        device, std::span<u8>{reinterpret_cast<u8*>(words.data()), sizeof(words)});
    counters.back_wait_ns.fetch_add(
        static_cast<u64>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                             std::chrono::steady_clock::now() - start)
                             .count()),
        std::memory_order_relaxed);
    counters.back_kind[how < counters.back_kind.size() ? how : 0].fetch_add(
        1, std::memory_order_relaxed);
    if (words[0] == 0) {
        counters.back_zero.fetch_add(1, std::memory_order_relaxed);
    }
    CallMethod(system, MAXWELL3D_REG_INDEX(draw.begin), 0, true);
    ConsumeSink(system);
    CallMethod(system, MAXWELL3D_REG_INDEX(vertex_buffer.first), words[1], true);
    ConsumeSink(system);
    CallMethod(system, MAXWELL3D_REG_INDEX(vertex_buffer.count), words[0], true);
    ConsumeSink(system);
    CallMethod(system, MAXWELL3D_REG_INDEX(draw.end), 0, true);
    ConsumeSink(system);
    draw_manager.DrawDeferred(*this);
}

// A macro whose code reads memory into its FIFO (MME_DMA_READ_FIFOED) and writes only registers
// BackWritable allows runs on the back, at its point of the method stream: it reads the words as
'''),
])

# --- DmaPusher: the list entry, and the ring entries before it.
patch("dma_pusher.h", [
    ('''    bool TakeGpuWrittenListWords(const CommandListHeader& header);
''', '''    bool TakeGpuWrittenListWords(const CommandListHeader& header);
    /// EDEN_NX2_LIST_DRAW_BACK: a GPU-written list that is all of a forwarding 3D draw macro
    /// call's parameters goes to the back with its address (nx2_list_draw.h).
    bool TakeGpuWrittenDrawList(const CommandListHeader& header);
'''),
])
patch("dma_pusher.cpp", [
    ('''#include "video_core/nx2_list_indirect.h"
#include "video_core/nx2_list_indirect_stats.h"
''', '''#include "video_core/nx2_list_draw.h"
#include "video_core/nx2_list_indirect.h"
#include "video_core/nx2_list_indirect_stats.h"
'''),
    ('''            if (safe_reads) {
                split.WaitForSync();
            } else {
                split.WaitForSyncedWriters();
            }
            gpu_written =
                IsGpuWrittenCommands(dma_state.dma_get, size_t{header.size} * sizeof(u32));
''', '''            if (safe_reads) {
                split.WaitForSync();
            } else {
                // EDEN_NX2_TRUST_RING_PAGES: pages read TrustReads times with no GPU write found
                // after this wait are read without it; the back checks each such read.
                namespace ListDraw = Tegra::Nx2ListDraw;
                auto& list_draw = ListDraw::Get();
                std::array<u64, 2> ring_pages{};
                u32 ring_count = 0;
                if ((ListDraw::Switches() & 2) != 0 && header.size != 0 &&
                    !list_draw.trust_broken.load(std::memory_order_relaxed)) {
                    ListDraw::MaybeLog();
                    const GPUVAddr list_begin = dma_state.dma_get;
                    const GPUVAddr list_last = list_begin + u64{header.size} * sizeof(u32) - 1;
                    const GPUVAddr first_page = list_begin & ~GPUVAddr{0xfff};
                    const GPUVAddr last_page = list_last & ~GPUVAddr{0xfff};
                    if (last_page - first_page <= 0x1000 &&
                        split.Ran(split.LastMappingChange())) {
                        const auto device_first = memory_manager.GpuToCpuAddress(first_page);
                        const auto device_last = memory_manager.GpuToCpuAddress(last_page);
                        if (device_first && device_last) {
                            ring_pages = {*device_first & ~u64{0xfff}, *device_last & ~u64{0xfff}};
                            ring_count = 2;
                        }
                    }
                }
                bool trusted = ring_count != 0;
                auto& known_pages = ListDraw::Pages();
                for (u32 i = 0; i < ring_count && trusted; ++i) {
                    const auto it = known_pages.find(ring_pages[i]);
                    trusted = it != known_pages.end() && it->second != ListDraw::Distrusted &&
                              it->second >= ListDraw::TrustReads;
                }
                if (trusted) {
                    // At this entry's place the back ran every writer before it.
                    split.Defer(
                        [page0 = ring_pages[0], page1 = ring_pages[1]] {
                            if (Tegra::Nx2IsGpuWritten(page0)) {
                                Tegra::Nx2ListDraw::NoteTrustMiss(page0);
                            } else if (Tegra::Nx2IsGpuWritten(page1)) {
                                Tegra::Nx2ListDraw::NoteTrustMiss(page1);
                            }
                        },
                        VideoCommon::GpuSplit::FastDrawNeutral,
                        VideoCommon::GpuSplit::Writes::Noted);
                    list_draw.trust_skips.fetch_add(1, std::memory_order_relaxed);
                } else {
                    split.WaitForSyncedWriters();
                    if (ring_count != 0) {
                        if (known_pages.size() > 65536) {
                            known_pages.clear();
                        }
                        for (u32 i = 0; i < ring_count; ++i) {
                            u32& reads = known_pages[ring_pages[i]];
                            if (Tegra::Nx2IsGpuWritten(ring_pages[i])) {
                                reads = ListDraw::Distrusted;
                            } else if (reads != ListDraw::Distrusted &&
                                       reads < ListDraw::TrustReads) {
                                ++reads;
                            }
                            if (ring_pages[1] == ring_pages[0]) {
                                break;
                            }
                        }
                        list_draw.trust_waits.fetch_add(1, std::memory_order_relaxed);
                    }
                }
            }
            gpu_written =
                IsGpuWrittenCommands(dma_state.dma_get, size_t{header.size} * sizeof(u32));
'''),
    ('''        } else if (bool pending = false;
                   !caches_flushed &&
                   (ReadGpuWrittenCommands(dma_state.dma_get, static_cast<u32>(header.size),
                                           &pending) ||
                    pending)) {
''', '''        } else if (!caches_flushed && gpu_written && TakeGpuWrittenDrawList(header)) {
            // The back reads the words at this place; the front parses guest memory's as stand-ins
            // that only reach Maxwell3D::FrontRunListCall.
            const std::span<const CommandHeader> commands{command_headers.data(), header.size};
            auto* const maxwell3d =
                static_cast<Engines::Maxwell3D*>(subchannels[dma_state.subchannel]);
            command_resume_offset +=
                ProcessCommands(commands.subspan(command_resume_offset), command_resume_offset,
                                "GPU draw list", submitted_copy);
            maxwell3d->FrontListCallDone();
        } else if (bool pending = false;
                   !caches_flushed &&
                   (ReadGpuWrittenCommands(dma_state.dma_get, static_cast<u32>(header.size),
                                           &pending) ||
                    pending)) {
'''),
    ('''bool DmaPusher::FlushCachedCommands(GPUVAddr address, size_t size, bool live) {
''', '''bool DmaPusher::TakeGpuWrittenDrawList(const CommandListHeader& header) {
    namespace ListDraw = Tegra::Nx2ListDraw;
    if ((ListDraw::Switches() & 1) == 0) {
        return false;
    }
    ListDraw::MaybeLog();
    auto& counters = ListDraw::Get();
    auto& split = VideoCommon::GpuSplit::Get();
    if (rasterizer == nullptr || command_resume_offset != 0 || !split.Active() ||
        !split.FrontMacros() || split.Nx2HasRenderFollower()) {
        return false;
    }
    // All of a 3D macro call's parameters, from its first: its header ended the entry before.
    const u32 count = static_cast<u32>(header.size);
    if (count != 2 || dma_state.method_count != count ||
        dma_state.method < MacroRegistersStart || (dma_state.method & 1) != 0 ||
        subchannels[dma_state.subchannel] == nullptr ||
        subchannel_type[dma_state.subchannel] != Engines::EngineTypes::Maxwell3D) {
        return false;
    }
    auto* const maxwell3d = static_cast<Engines::Maxwell3D*>(subchannels[dma_state.subchannel]);
    if (!maxwell3d->GetNativeMacroEngine().native_turing) {
        return false;
    }
    // As TakeGpuWrittenListWords: the back must find the same mappings, and the words on one
    // GPU-written page (BufferCache::Nx2ReadListCopy reads within a page).
    const u64 bytes = u64{count} * sizeof(u32);
    const auto device = memory_manager.GpuToCpuAddress(dma_state.dma_get);
    const auto last = memory_manager.GpuToCpuAddress(dma_state.dma_get + bytes - 1);
    if (!split.Ran(split.LastMappingChange()) || !device || !last ||
        *last != *device + bytes - 1 || (*device & 0xfff) + bytes > 0x1000 ||
        !Tegra::Nx2IsGpuWritten(*device)) {
        counters.refused_place.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    if (!maxwell3d->FrontPrepareListCall(system, split, dma_state.method, count, *device)) {
        return false;
    }
    // From the page's next write on the buffer cache copies it for the back's read.
    Tegra::Nx2MarkCommandListPage(*device);
    command_headers.resize_destructive(count);
    memory_manager.ReadBlockUnsafe(dma_state.dma_get, command_headers.data(), bytes);
    return true;
}

bool DmaPusher::FlushCachedCommands(GPUVAddr address, size_t size, bool live) {
'''),
])
print("apply-nx2-list-draw-back: applied")
