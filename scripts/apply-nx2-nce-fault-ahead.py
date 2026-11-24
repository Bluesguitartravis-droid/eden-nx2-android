#!/usr/bin/env python3
"""NCE write faults on GPU-cached pages: breakdown, sample log, and invalidating ahead.

Ocarina on NCE takes ~5,000 faults/s on GPU-cached pages (handler ~260 ms/s): each invalidates the
page, the GPU caches it again, the next write faults again.

- The 10 s NCE report gains a line: faults by kind (read / write / unknown, from the ESR record),
  faults on the same page as the core's previous invalidating fault and on the next page, and the
  invalidations' count, pages and time.
- EDEN_NX2_NCE_FAULT_LOG=<n> (live, n <= 64; each new value starts again): the next n faults'
  address, pc and kind, logged from the 10 s report.
- EDEN_NX2_NCE_FAULT_AHEAD=<pages> (live, 0 = off): when an invalidating fault is on the page after
  the core's previous one (a write stream), invalidate up to <pages> following pages too in the same
  call, as long as they are GPU-cached, device-contiguous and hold no GPU writes."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-fault-ahead: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


replace(src / "common/nx2_nce_stats.h", "inline std::atomic<bool> open_pages{true};\n",
        """inline std::atomic<bool> open_pages{true};
// Fault breakdown and EDEN_NX2_NCE_FAULT_AHEAD / _LOG (scripts/apply-nx2-nce-fault-ahead.py).
inline std::atomic<std::uint64_t> read_faults{};
inline std::atomic<std::uint64_t> write_faults{};
inline std::atomic<std::uint64_t> unknown_faults{};
inline std::atomic<std::uint64_t> same_page_faults{};
inline std::atomic<std::uint64_t> next_page_faults{};
inline std::atomic<std::uint64_t> invalidations{};
inline std::atomic<std::uint64_t> invalidated_pages{};
inline std::atomic<std::uint64_t> invalidate_ns{};
inline std::atomic<std::uint32_t> fault_ahead{};
struct FaultSample {
    std::atomic<std::uint64_t> addr;
    std::atomic<std::uint64_t> pc;
    std::atomic<std::uint32_t> kind;
    std::atomic<bool> ready;
};
constexpr std::uint32_t FaultSampleCount = 64;
inline FaultSample fault_samples[FaultSampleCount];
// Index of the next sample to fill; FaultSampleCount or more = not sampling.
inline std::atomic<std::uint32_t> fault_sample_next{FaultSampleCount};
""")

nce = src / "core/arm/nce/arm_nce.cpp"
replace(nce, """    auto& memory = guest_ctx->parent->m_running_thread->GetOwnerProcess()->GetMemory();
    // NX2: a write to a page that also holds GPU-written bytes.""",
        """    auto& memory = guest_ctx->parent->m_running_thread->GetOwnerProcess()->GetMemory();
    // NX2: fault breakdown and samples (scripts/apply-nx2-nce-fault-ahead.py)
    {
        auto& nx2_ctx = static_cast<ucontext_t*>(raw_context)->uc_mcontext;
        const auto* const nx2_esr = FindSignalRecord<esr_context>(nx2_ctx, ESR_MAGIC);
        u32 nx2_kind = 0;
        if (nx2_esr != nullptr && (nx2_esr->esr >> 26) == 0x24) {
            nx2_kind = (nx2_esr->esr & (u64{1} << 6)) ? 2 : 1;
        }
        (nx2_kind == 2   ? Common::Nx2NceStats::write_faults
         : nx2_kind == 1 ? Common::Nx2NceStats::read_faults
                         : Common::Nx2NceStats::unknown_faults)
            .fetch_add(1, std::memory_order_relaxed);
        if (Common::Nx2NceStats::fault_sample_next.load(std::memory_order_relaxed) <
            Common::Nx2NceStats::FaultSampleCount) {
            const u32 nx2_slot = Common::Nx2NceStats::fault_sample_next.fetch_add(1);
            if (nx2_slot < Common::Nx2NceStats::FaultSampleCount) {
                auto& sample = Common::Nx2NceStats::fault_samples[nx2_slot];
                sample.addr.store(reinterpret_cast<u64>(info->si_addr), std::memory_order_relaxed);
                sample.pc.store(nx2_ctx.pc, std::memory_order_relaxed);
                sample.kind.store(nx2_kind, std::memory_order_relaxed);
                sample.ready.store(true, std::memory_order_release);
            }
        }
    }
    // NX2: a write to a page that also holds GPU-written bytes.""")

memory = src / "core/memory.cpp"
replace(memory, """    /// NX2 (NCE): a write fault on a cached page that also holds GPU-written bytes. Instead of""",
        """    /// NX2 (NCE): the one device address of a host page, if it has exactly one.
    std::optional<DAddr> Nx2SingleDeviceAddress(const u8* ptr) {
        constexpr size_t sys_core = Core::Hardware::NUM_CPU_CORES - 1;
        const size_t core = (std::min)(system.GetCurrentHostThreadID(), sys_core);
        std::unique_lock<std::mutex> guard{sys_core_guard, std::defer_lock};
        if (core == sys_core) [[unlikely]] {
            guard.lock();
        }
        std::optional<DAddr> result;
        size_t count = 0;
        gpu_device_memory->ApplyOpOnPointer(ptr, scratch_buffers[core], [&](DAddr address) {
            result = address;
            ++count;
        });
        return count == 1 ? result : std::nullopt;
    }

    /// NX2 (NCE, EDEN_NX2_NCE_FAULT_AHEAD): bytes from the faulting page on that can be
    /// invalidated in one call: following pages that are GPU-cached, device-contiguous with it and
    /// hold no GPU writes, up to `ahead` of them.
    size_t Nx2InvalidateAheadSize(u64 vaddr, const u8* ptr, u32 ahead) {
        if (!gpu_device_memory) [[unlikely]] {
            gpu_device_memory = &system.Host1x().MemoryManager();
        }
        const auto base = Nx2SingleDeviceAddress(ptr);
        size_t size = LICHII_PAGESIZE;
        if (!base) {
            return size;
        }
        for (u32 k = 1; k <= ahead; ++k) {
            const u64 next = vaddr + u64{k} * LICHII_PAGESIZE;
            if (!AddressSpaceContains(*current_page_table, next, LICHII_PAGESIZE)) {
                break;
            }
            bool cached = false;
            const u8* const next_ptr = GetPointerImpl(next, [] {}, [&] { cached = true; });
            if (!cached || next_ptr == nullptr) {
                break;
            }
            const auto device = Nx2SingleDeviceAddress(next_ptr);
            if (!device || *device != *base + u64{k} * LICHII_PAGESIZE ||
                Nx2HostPageHoldsGpuWrites(next_ptr)) {
                break;
            }
            size += LICHII_PAGESIZE;
        }
        return size;
    }

    std::array<u64, Core::Hardware::NUM_CPU_CORES> nx2_last_fault_page{};

    /// NX2 (NCE): a write fault on a cached page that also holds GPU-written bytes. Instead of""")

replace(memory, """            impl->Nx2OpenPageForWrite(GetInteger(vaddr), ptr)) {
            return true;
        }
        impl->InvalidateGPUMemory(ptr, size);""", """            impl->Nx2OpenPageForWrite(GetInteger(vaddr), ptr)) {
            return true;
        }
        // NX2: breakdown and EDEN_NX2_NCE_FAULT_AHEAD (scripts/apply-nx2-nce-fault-ahead.py)
        size_t nx2_size = size;
        const size_t nx2_core = system.GetCurrentHostThreadID();
        if (size == LICHII_PAGESIZE && ptr != nullptr &&
            nx2_core < Core::Hardware::NUM_CPU_CORES) {
            const u64 nx2_page = GetInteger(vaddr) >> LICHII_PAGEBITS;
            u64& nx2_last = impl->nx2_last_fault_page[nx2_core];
            if (nx2_page == nx2_last) {
                Common::Nx2NceStats::same_page_faults.fetch_add(1, std::memory_order_relaxed);
            } else if (nx2_page == nx2_last + 1) {
                Common::Nx2NceStats::next_page_faults.fetch_add(1, std::memory_order_relaxed);
                if (const u32 nx2_ahead =
                        Common::Nx2NceStats::fault_ahead.load(std::memory_order_relaxed)) {
                    nx2_size = impl->Nx2InvalidateAheadSize(
                        GetInteger(vaddr) & ~LICHII_PAGEMASK, ptr, nx2_ahead);
                }
            }
            nx2_last = nx2_page + nx2_size / LICHII_PAGESIZE - 1;
        }
        const auto nx2_start = std::chrono::steady_clock::now();
        impl->InvalidateGPUMemory(ptr, nx2_size);
        Common::Nx2NceStats::invalidations.fetch_add(1, std::memory_order_relaxed);
        Common::Nx2NceStats::invalidated_pages.fetch_add(nx2_size / LICHII_PAGESIZE,
                                                        std::memory_order_relaxed);
        Common::Nx2NceStats::invalidate_ns.fetch_add(
            static_cast<u64>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                 std::chrono::steady_clock::now() - nx2_start)
                                 .count()),
            std::memory_order_relaxed);""")
text = memory.read_text()
for inc in ("#include <chrono>\n", "#include <optional>\n", '#include "common/nx2_nce_stats.h"\n'):
    if inc not in text:
        first = text.index("#include ")
        text = text[:first] + inc + text[first:]
memory.write_text(text)

core = src / "core/core.cpp"
replace(core, """                                    if (const u64 nx2_dl =
                                            Common::Nx2NceStats::open_downloads.exchange(0)) {""",
        """                                    {
                                        namespace S = Common::Nx2NceStats;
                                        const u64 inv = S::invalidations.exchange(0);
                                        LOG_INFO(Core,
                                                 "NX2_NCE fault kinds: reads {:.0f}/s writes {:.0f}/s "
                                                 "unknown {:.0f}/s; same page {:.0f}/s next page "
                                                 "{:.0f}/s; invalidations {:.0f}/s ({:.0f} pages/s) "
                                                 "{:.1f} ms/s; ahead {}",
                                                 S::read_faults.exchange(0) / window_s,
                                                 S::write_faults.exchange(0) / window_s,
                                                 S::unknown_faults.exchange(0) / window_s,
                                                 S::same_page_faults.exchange(0) / window_s,
                                                 S::next_page_faults.exchange(0) / window_s,
                                                 inv / window_s,
                                                 S::invalidated_pages.exchange(0) / window_s,
                                                 S::invalidate_ns.exchange(0) / 1e6 / window_s,
                                                 S::fault_ahead.load());
                                        S::fault_ahead.store(static_cast<u32>(std::strtoul(
                                            VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_FAULT_AHEAD")
                                                .c_str(),
                                            nullptr, 10)));
                                        static std::string nx2_log_value;
                                        static u32 nx2_log_printed = S::FaultSampleCount;
                                        const std::string log_value =
                                            VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_FAULT_LOG");
                                        if (log_value != nx2_log_value) {
                                            nx2_log_value = log_value;
                                            const u32 n = (std::min)(
                                                static_cast<u32>(std::strtoul(log_value.c_str(),
                                                                              nullptr, 10)),
                                                S::FaultSampleCount);
                                            for (auto& sample : S::fault_samples) {
                                                sample.ready.store(false);
                                            }
                                            nx2_log_printed = S::FaultSampleCount - n;
                                            S::fault_sample_next.store(nx2_log_printed);
                                        }
                                        std::string samples;
                                        while (nx2_log_printed < S::FaultSampleCount &&
                                               S::fault_samples[nx2_log_printed].ready.load(
                                                   std::memory_order_acquire)) {
                                            const auto& sample = S::fault_samples[nx2_log_printed];
                                            const u32 kind = sample.kind.load();
                                            samples += fmt::format(
                                                " {:#x}@{:#x}{}", sample.addr.load(),
                                                sample.pc.load(),
                                                kind == 2 ? "W" : kind == 1 ? "R" : "?");
                                            ++nx2_log_printed;
                                        }
                                        if (!samples.empty()) {
                                            LOG_INFO(Core, "NX2_NCE fault samples (addr@pc):{}",
                                                     samples);
                                        }
                                    }
                                    if (const u64 nx2_dl =
                                            Common::Nx2NceStats::open_downloads.exchange(0)) {""")
text = core.read_text()
if '#include "video_core/nx2_live_switch.h"' not in text:
    first = text.index("#include ")
    core.write_text(text[:first] + '#include "video_core/nx2_live_switch.h"\n' + text[first:])
print("nce-fault-ahead: fault breakdown, EDEN_NX2_NCE_FAULT_LOG and EDEN_NX2_NCE_FAULT_AHEAD added")
