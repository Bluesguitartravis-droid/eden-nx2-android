#!/usr/bin/env python3
"""NCE: execute small guest writes to pages that also hold GPU-written bytes on the emulated
memory, instead of handing the whole page to the GPU thread.

Under NCE a guest write to a GPU-cached page faults, and the handler can only say "this page
was written". When the page also holds bytes the GPU wrote, those have to be kept, which only the
GPU thread can do: GPU::InvalidateRegion queues a flush behind the GPU thread's work and the
guest thread waits for it. That is three thread handoffs, about 270 us each time. Donkey Kong
Bananza does it ~2,200 times a second (builds 137-143: "gpu-written waits"), so its render thread
stands still for 55-65 % of every second; Mario Kart World loses ~30 %.

Dynarmic does not have the problem: it knows each write's address and size, so the write is
collected (Memory::HandleRasterizerWrite -> GPU::OnCPUWrite) and the GPU thread later copies
just those bytes into its buffer, without anyone waiting.

Give NCE the same path for such pages: run the faulting store through NCE's own interpreter
(the one used for alignment faults), whose memory writes go through Core::Memory and are
collected exactly like Dynarmic's. The page stays protected, so each further write to it faults
again (a few microseconds). A burst of writes to one page (more than 24 within 4 ms, e.g. a
memcpy) is cheaper the old way, so the handler then falls back to it and remembers the page as
bulk-written for two seconds. Stores the interpreter does not know (atomics, DC ZVA, ST1) also
fall back.

Off by default since build 146 (DKB was slower with it: its writes are mostly bursts);
EDEN_NX2_NCE_COLLECT_WRITES=1 turns it on. The NX2_NCE line gains "collected N/s, bursts,
not emulated"."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src"


def patch(rel, old, new, marker):
    p = root / rel
    t = p.read_text()
    if marker in t:
        return False
    if old not in t:
        raise SystemExit(f"{rel}: anchor not found: {old[:80]!r}")
    p.write_text(t.replace(old, new, 1))
    return True


# Counters.
(root / "common/nx2_nce_writes.h").write_text("""// NX2 (NCE): guest writes to pages that also hold GPU-written bytes (arm_nce.cpp,
// Memory::Nx2CollectNceWrite).
#pragma once
#include <atomic>
#include <cstdint>

namespace Common::Nx2NceWrites {
/// Stores executed on the emulated memory and collected for the GPU thread.
inline std::atomic<std::uint64_t> collected{};
/// Bursts of writes to one page, handed to the GPU thread as a whole page.
inline std::atomic<std::uint64_t> bursts{};
/// Faulting instructions the interpreter could not execute.
inline std::atomic<std::uint64_t> not_emulated{};
} // namespace Common::Nx2NceWrites
""")

# Rasterizer: does a CPU write to this page have to go to the GPU thread?
patch("video_core/rasterizer_interface.h",
      "    virtual bool OnCPUWrite(PAddr addr, u64 size) = 0;\n",
      """    virtual bool OnCPUWrite(PAddr addr, u64 size) = 0;

    /// NX2 (NCE): whether the page holds GPU-written bytes, so that OnCPUWrite collects a write
    /// to it for the GPU thread (and TryCacheInvalidation sends the whole page there).
    virtual bool Nx2PageHoldsGpuWrites([[maybe_unused]] DAddr page) {
        return false;
    }
""",
      "Nx2PageHoldsGpuWrites")
patch("video_core/renderer_vulkan/vk_rasterizer.h",
      "    bool TryCacheInvalidation(DAddr addr, u64 size) override;\n",
      "    bool TryCacheInvalidation(DAddr addr, u64 size) override;\n"
      "    bool Nx2PageHoldsGpuWrites(DAddr page) override;\n",
      "Nx2PageHoldsGpuWrites")
# Buffer cache: the test OnCPUWrite makes before it collects a write. (TryCacheInvalidation gives
# up on gpu_modified_ranges, which can also hold ranges whose page the tracker no longer marks:
# OnCPUWrite would apply a write there on the calling thread, where the GPU's bytes on the page
# cannot be kept. Those pages keep going to the GPU thread.)
patch("video_core/buffer_cache/buffer_cache_base.h",
      "    bool OnCPUWrite(DAddr device_addr, u64 size);\n",
      "    bool OnCPUWrite(DAddr device_addr, u64 size);\n\n"
      "    /// NX2 (NCE): whether OnCPUWrite collects a write on this page for the GPU thread.\n"
      "    [[nodiscard]] bool Nx2CollectsCpuWrites(DAddr page);\n",
      "Nx2CollectsCpuWrites")
patch("video_core/buffer_cache/buffer_cache.h",
      "template <class P>\nbool BufferCache<P>::OnCPUWrite(DAddr device_addr, u64 size) {\n",
      """template <class P>
bool BufferCache<P>::Nx2CollectsCpuWrites(DAddr page) {
    return IsRegionRegistered(page, DEVICE_PAGESIZE) &&
           memory_tracker.IsRegionGpuModified(page, DEVICE_PAGESIZE);
}

template <class P>
bool BufferCache<P>::OnCPUWrite(DAddr device_addr, u64 size) {
""",
      "BufferCache<P>::Nx2CollectsCpuWrites")

patch("video_core/renderer_vulkan/vk_rasterizer.cpp",
      "bool RasterizerVulkan::TryCacheInvalidation(DAddr addr, u64 size) {\n",
      """bool RasterizerVulkan::Nx2PageHoldsGpuWrites(DAddr page) {
    std::scoped_lock lock{buffer_cache.mutex};
    return buffer_cache.Nx2CollectsCpuWrites(Common::AlignDown(page, Core::DEVICE_PAGESIZE));
}

bool RasterizerVulkan::TryCacheInvalidation(DAddr addr, u64 size) {
""",
      "RasterizerVulkan::Nx2PageHoldsGpuWrites")

# GPU.
patch("video_core/gpu.h",
      "    bool OnCPUWrite(DAddr addr, u64 size);\n",
      "    bool OnCPUWrite(DAddr addr, u64 size);\n\n"
      "    /// NX2 (NCE): see RasterizerInterface::Nx2PageHoldsGpuWrites.\n"
      "    bool Nx2PageHoldsGpuWrites(DAddr page);\n",
      "Nx2PageHoldsGpuWrites")
patch("video_core/gpu.cpp",
      """    bool OnCPUWrite(DAddr addr, u64 size) {
        return renderer->ReadRasterizer()->OnCPUWrite(addr, size);
    }
""",
      """    bool OnCPUWrite(DAddr addr, u64 size) {
        return renderer->ReadRasterizer()->OnCPUWrite(addr, size);
    }

    bool Nx2PageHoldsGpuWrites(DAddr page) {
        return renderer->ReadRasterizer()->Nx2PageHoldsGpuWrites(page);
    }
""",
      "bool Nx2PageHoldsGpuWrites(DAddr page) {")
patch("video_core/gpu.cpp",
      """bool GPU::OnCPUWrite(DAddr addr, u64 size) {
    return impl->OnCPUWrite(addr, size);
}
""",
      """bool GPU::OnCPUWrite(DAddr addr, u64 size) {
    return impl->OnCPUWrite(addr, size);
}

bool GPU::Nx2PageHoldsGpuWrites(DAddr page) {
    return impl->Nx2PageHoldsGpuWrites(page);
}
""",
      "bool GPU::Nx2PageHoldsGpuWrites")

# Memory: the per-core decision.
patch("core/memory.h",
      "    bool InvalidateNCE(Common::ProcessAddress vaddr, size_t size);\n",
      """    bool InvalidateNCE(Common::ProcessAddress vaddr, size_t size);

    /// NX2 (NCE): whether the fault handler should execute the faulting store at this page on
    /// the emulated memory (collected for the GPU thread) rather than invalidate the page.
    bool Nx2CollectNceWrite(Common::ProcessAddress page);

    /// NX2 (NCE): the store could not be executed; invalidate this page the usual way for a while.
    void Nx2NceWriteNotEmulated(Common::ProcessAddress page);
""",
      "Nx2CollectNceWrite")
patch("core/memory.cpp",
      "    std::mutex sys_core_guard;\n",
      """    std::mutex sys_core_guard;

    /// NX2 (NCE): one core's writes to pages that also hold GPU-written bytes.
    struct alignas(64) Nx2NceWrites {
        static constexpr u32 BurstLimit = 24;                ///< Stores to one page...
        static constexpr u64 BurstWindowNs = 4'000'000;      ///< ...within this long are a burst
        static constexpr u64 BulkNs = 2'000'000'000;         ///< A burst's page stays bulk this long
        u64 page{~u64{0}}; ///< The page being collected
        u64 since{};       ///< When its first store faulted
        u32 stores{};
        std::array<std::pair<u64, u64>, 64> bulk{}; ///< Page and until when, by page % size
    };
    std::array<Nx2NceWrites, Core::Hardware::NUM_CPU_CORES> nx2_nce_writes{};
""",
      "struct alignas(64) Nx2NceWrites")
patch("core/memory.cpp",
      "bool Memory::InvalidateNCE(Common::ProcessAddress vaddr, size_t size) {\n",
      """namespace {
u64 Nx2NowNs() {
    return static_cast<u64>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                std::chrono::steady_clock::now().time_since_epoch())
                                .count());
}
} // Anonymous namespace

bool Memory::Nx2CollectNceWrite(Common::ProcessAddress vaddr) {
    static const bool enabled = [] {
        // Off unless asked for: build 145 measured Donkey Kong Bananza slower with it (its writes
        // to these pages are mostly bursts: 33,000 emulated stores/s and still 1,400 waits/s).
        const char* value = std::getenv("EDEN_NX2_NCE_COLLECT_WRITES");
        return value != nullptr && value[0] == '1';
    }();
    const size_t core = system.GetCurrentHostThreadID();
    if (!enabled || core >= Core::Hardware::NUM_CPU_CORES) {
        return false;
    }
    using State = Impl::Nx2NceWrites;
    State& state = impl->nx2_nce_writes[core];
    const u64 page = GetInteger(vaddr) >> LICHII_PAGEBITS;
    const u64 now = Nx2NowNs();
    auto& bulk = state.bulk[page % state.bulk.size()];
    if (state.page == page && now - state.since < State::BurstWindowNs) {
        if (++state.stores <= State::BurstLimit) {
            return true;
        }
        // A burst: cheaper to hand the page over once and write the rest natively.
        bulk = {page, now + State::BulkNs};
        state.page = ~u64{0};
        Common::Nx2NceWrites::bursts.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    if (bulk.first == page && now < bulk.second) {
        return false;
    }
    bool rasterizer = false;
    const u8* const ptr = impl->GetPointerImpl(
        GetInteger(vaddr), [] {}, [&] { rasterizer = true; });
    if (!rasterizer || ptr == nullptr) {
        return false;
    }
    if (!impl->gpu_device_memory) [[unlikely]] {
        impl->gpu_device_memory = &system.Host1x().MemoryManager();
    }
    if (!impl->gpu_device_memory->IsPhysicalPointer(ptr)) [[unlikely]] {
        return false;
    }
    bool held = false;
    {
        // The last core's scratch buffer is shared with the threads that are not cores.
        constexpr size_t sys_core = Core::Hardware::NUM_CPU_CORES - 1;
        std::unique_lock<std::mutex> guard{impl->sys_core_guard, std::defer_lock};
        if (core == sys_core) [[unlikely]] {
            guard.lock();
        }
        auto& gpu = system.GPU();
        impl->gpu_device_memory->ApplyOpOnPointer(
            ptr, impl->scratch_buffers[core],
            [&](DAddr address) { held = held || gpu.Nx2PageHoldsGpuWrites(address); });
    }
    if (!held) {
        return false;
    }
    state.page = page;
    state.since = now;
    state.stores = 1;
    return true;
}

void Memory::Nx2NceWriteNotEmulated(Common::ProcessAddress vaddr) {
    const size_t core = system.GetCurrentHostThreadID();
    if (core >= Core::Hardware::NUM_CPU_CORES) {
        return;
    }
    Impl::Nx2NceWrites& state = impl->nx2_nce_writes[core];
    const u64 page = GetInteger(vaddr) >> LICHII_PAGEBITS;
    state.bulk[page % state.bulk.size()] = {page, Nx2NowNs() + Impl::Nx2NceWrites::BulkNs};
    state.page = ~u64{0};
    Common::Nx2NceWrites::not_emulated.fetch_add(1, std::memory_order_relaxed);
}

bool Memory::InvalidateNCE(Common::ProcessAddress vaddr, size_t size) {
""",
      "bool Memory::Nx2CollectNceWrite")
p = root / "core/memory.cpp"
t = p.read_text()
for inc in ('#include "common/nx2_nce_writes.h"\n', "#include <chrono>\n", "#include <array>\n"):
    if inc not in t:
        t = inc + t
p.write_text(t)

# The fault handler.
patch("core/arm/nce/arm_nce.cpp",
      """    if (memory.InvalidateNCE(addr, Memory::LICHII_PAGESIZE)) {
        // We handled the access successfully and are returning to guest code.
        return true;
    }
""",
      """    // NX2: a write to a page that also holds GPU-written bytes. Invalidating the page waits for
    // the GPU thread; executing the store on the emulated memory collects it as Dynarmic's are.
    if (memory.Nx2CollectNceWrite(addr)) {
        auto& nx2_host_ctx = static_cast<ucontext_t*>(raw_context)->uc_mcontext;
        if (nx2_host_ctx.pc != reinterpret_cast<u64>(info->si_addr)) {
            auto* nx2_fpctx = GetFloatingPointState(nx2_host_ctx);
            if (const auto next_pc =
                    MatchAndExecuteOneInstruction(memory, &nx2_host_ctx, nx2_fpctx)) {
                nx2_host_ctx.pc = *next_pc;
                Common::Nx2NceWrites::collected.fetch_add(1, std::memory_order_relaxed);
                return true;
            }
        }
        memory.Nx2NceWriteNotEmulated(addr);
    }
    if (memory.InvalidateNCE(addr, Memory::LICHII_PAGESIZE)) {
        // We handled the access successfully and are returning to guest code.
        return true;
    }
""",
      "memory.Nx2CollectNceWrite(addr)")
p = root / "core/arm/nce/arm_nce.cpp"
t = p.read_text()
if '#include "common/nx2_nce_writes.h"\n' not in t:
    p.write_text('#include "common/nx2_nce_writes.h"\n' + t)

# The NX2_NCE line (apply-nx2-thread-cpu.py, test builds).
core = root / "core/core.cpp"
c = core.read_text()
old_line = """                                    LOG_INFO(Core, "NX2_NCE faults {:.0f}/s handler {:.1f} ms/s unmapped {:.0f}/s "
                                                   "gpu-written waits {:.0f}/s {:.1f} ms/s",
                                             faults / window_s, fault_ns / 1e6 / window_s,
                                             unmapped / window_s, sync_inv / window_s,
                                             sync_ns / 1e6 / window_s);"""
new_line = """                                    LOG_INFO(Core, "NX2_NCE faults {:.0f}/s handler {:.1f} ms/s unmapped {:.0f}/s "
                                                   "gpu-written waits {:.0f}/s {:.1f} ms/s "
                                                   "collected {:.0f}/s bursts {:.0f}/s not emulated {:.0f}/s",
                                             faults / window_s, fault_ns / 1e6 / window_s,
                                             unmapped / window_s, sync_inv / window_s,
                                             sync_ns / 1e6 / window_s,
                                             Common::Nx2NceWrites::collected.exchange(0) / window_s,
                                             Common::Nx2NceWrites::bursts.exchange(0) / window_s,
                                             Common::Nx2NceWrites::not_emulated.exchange(0) / window_s);"""
if "Common::Nx2NceWrites::collected.exchange" not in c:
    if old_line in c:
        c = c.replace(old_line, new_line, 1)
        if '#include "common/nx2_nce_writes.h"\n' not in c:
            c = '#include "common/nx2_nce_writes.h"\n' + c
        core.write_text(c)
    else:
        print("NX2_NCE line not present; collected-write counters are not logged")
print("NCE collects small writes to GPU-written pages")
