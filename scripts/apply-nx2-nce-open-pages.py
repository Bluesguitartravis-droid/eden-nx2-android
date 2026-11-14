#!/usr/bin/env python3
"""NCE: a guest write to a GPU-cached page that also holds GPU-written bytes waited for the GPU
thread to download those bytes first (GPU::InvalidateRegion's sync operation): 3,300-4,000 waits a
second in Donkey Kong Bananza, 220-520 ms of every second, 1,300 a second in Mario Kart World.
Dynarmic never waits: it knows each write's exact bytes, collects them, and the GPU thread later
claims those bytes and downloads the GPU's around them (OnCacheInvalidations).

NCE only knows the page. So: keep a copy of the page as it is at the fault, open the page for
writing and return at once. When the GPU thread next gathers guest writes it closes the page again,
compares it with the copy, and hands the changed bytes over as the exact writes. The pages stay
locked against being opened again until those writes are applied.

Limits: a store that writes the value already there is not seen (the GPU's byte wins there), and
the GPU's bytes on the page reach guest memory at the next gather instead of at the fault.
Not used with reactive flushing. EDEN_NX2_NCE_OPEN_PAGES=0 turns it off."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src"


def patch(rel, old, new, marker):
    p = root / rel
    t = p.read_text()
    if marker in t:
        return
    if old not in t:
        raise SystemExit(f"{rel}: anchor not found: {old[:70]!r}")
    p.write_text(t.replace(old, new, 1))


def include(rel, lines):
    p = root / rel
    t = p.read_text()
    for line in lines:
        line = f"#include {line}\n"
        if line not in t:
            first = t.index("#include ")
            t = t[:first] + line + t[first:]
    p.write_text(t)


mem = "core/memory.cpp"

# Protection changes and the page type they go with are one step.
patch(mem,
      """    void RasterizerMarkRegionCached(u64 vaddr, u64 size, bool cached) {
        if (vaddr == 0 || !AddressSpaceContains(*current_page_table, vaddr, size)) {
            return;
        }
""",
      """    void RasterizerMarkRegionCached(u64 vaddr, u64 size, bool cached) {
        if (vaddr == 0 || !AddressSpaceContains(*current_page_table, vaddr, size)) {
            return;
        }
        std::scoped_lock nx2_protect_lock{nx2_protect_mutex};
""",
      "std::scoped_lock nx2_protect_lock{nx2_protect_mutex};")

patch(mem,
      """    Core::System& system;
    Tegra::MaxwellDeviceMemoryManager* gpu_device_memory{};
""",
      """    /// NX2 (NCE): whether a device page of this host page holds GPU-written bytes.
    bool Nx2HostPageHoldsGpuWrites(const u8* ptr) {
        constexpr size_t sys_core = Core::Hardware::NUM_CPU_CORES - 1;
        const size_t core = (std::min)(system.GetCurrentHostThreadID(), sys_core);
        std::unique_lock<std::mutex> guard{sys_core_guard, std::defer_lock};
        if (core == sys_core) [[unlikely]] {
            guard.lock();
        }
        bool held = false;
        auto& gpu = system.GPU();
        gpu_device_memory->ApplyOpOnPointer(ptr, scratch_buffers[core], [&](DAddr address) {
            held = held || gpu.Nx2PageHoldsGpuWrites(address);
        });
        return held;
    }

    /// NX2 (NCE): a write fault on a cached page that also holds GPU-written bytes. Instead of
    /// waiting for the GPU thread to download them, keeps a copy of the page and opens it for
    /// writing; Nx2GatherOpenPages finds what was written.
    bool Nx2OpenPageForWrite(u64 vaddr, u8* ptr) {
        static const bool enabled = [] {
            const char* value = std::getenv("EDEN_NX2_NCE_OPEN_PAGES");
            return value == nullptr || value[0] != '0';
        }();
        if (!enabled || ptr == nullptr || !current_page_table->fastmem_arena ||
            Settings::values.use_reactive_flushing.GetValue()) {
            return false;
        }
        if (!gpu_device_memory) [[unlikely]] {
            gpu_device_memory = &system.Host1x().MemoryManager();
        }
        if (!gpu_device_memory->IsPhysicalPointer(ptr) || !Nx2HostPageHoldsGpuWrites(ptr)) {
            return false;
        }
        // Held by the GPU thread while it applies the writes of the pages it closed.
        std::scoped_lock lock{nx2_open_mutex};
        const u64 page = vaddr >> LICHII_PAGEBITS;
        const auto it = nx2_open_pages.find(page);
        if (it != nx2_open_pages.end() && it->second.ptr == ptr) {
            // Protected again meanwhile (another cache entry on the page).
            Common::Nx2NceWrites::bursts.fetch_add(1, std::memory_order_relaxed);
        } else {
            // Applied while this thread waited for the lock?
            if (nx2_open_pages.size() >= Nx2MaxOpenPages || !Nx2HostPageHoldsGpuWrites(ptr)) {
                return false;
            }
            Nx2OpenPage& entry = nx2_open_pages[page];
            entry.ptr = ptr;
            entry.before = std::make_unique<u8[]>(LICHII_PAGESIZE);
            std::memcpy(entry.before.get(), ptr, LICHII_PAGESIZE);
            nx2_open_count.store(nx2_open_pages.size(), std::memory_order_relaxed);
            Common::Nx2NceWrites::collected.fetch_add(1, std::memory_order_relaxed);
        }
        std::scoped_lock protect_lock{nx2_protect_mutex};
        host_buffer->Protect(page << LICHII_PAGEBITS, LICHII_PAGESIZE,
                             Common::MemoryPermission::Read | Common::MemoryPermission::Write);
        return true;
    }

    /// NX2 (NCE), on the GPU thread: closes the open pages and reports the bytes written to them
    /// since they were opened. The returned lock keeps them from being opened again until the
    /// caller has applied those writes.
    std::unique_lock<std::recursive_mutex> Nx2GatherOpenPages(
        std::function<void(PAddr, size_t)>& callback) {
        if (nx2_open_count.load(std::memory_order_relaxed) == 0) {
            return {};
        }
        std::unique_lock lock{nx2_open_mutex};
        if (nx2_open_pages.empty() || current_page_table == nullptr || !gpu_device_memory) {
            return {};
        }
        constexpr size_t none = ~size_t{0};
        for (const auto& [page, entry] : nx2_open_pages) {
            {
                std::scoped_lock protect_lock{nx2_protect_mutex};
                if (std::as_const(current_page_table->entries)[page].Type() ==
                    Common::PageType::RasterizerCachedMemory) {
                    host_buffer->Protect(page << LICHII_PAGEBITS, LICHII_PAGESIZE,
                                         Common::MemoryPermission::Read);
                }
            }
            const u8* const now = entry.ptr;
            const u8* const before = entry.before.get();
            size_t run_begin = none;
            for (size_t offset = 0; offset <= LICHII_PAGESIZE; offset += 4) {
                if (offset < LICHII_PAGESIZE && std::memcmp(now + offset, before + offset, 4) != 0) {
                    if (run_begin == none) {
                        run_begin = offset;
                    }
                    continue;
                }
                if (run_begin != none) {
                    const size_t run_size = offset - run_begin;
                    gpu_device_memory->ApplyOpOnPointer(
                        now + run_begin, nx2_open_scratch,
                        [&](DAddr address) { callback(address, run_size); });
                    run_begin = none;
                }
            }
        }
        nx2_open_pages.clear();
        nx2_open_count.store(0, std::memory_order_relaxed);
        return lock;
    }

    struct Nx2OpenPage {
        u8* ptr{};                    ///< The page's backing memory
        std::unique_ptr<u8[]> before; ///< Its bytes when it was opened
    };
    static constexpr size_t Nx2MaxOpenPages = 4096;
    std::recursive_mutex nx2_open_mutex;
    std::unordered_map<u64, Nx2OpenPage> nx2_open_pages; ///< By guest page number
    std::atomic<size_t> nx2_open_count{};
    Common::ScratchBuffer<u32> nx2_open_scratch;
    /// Host protection changes of cached pages together with their page type.
    std::mutex nx2_protect_mutex;

    Core::System& system;
    Tegra::MaxwellDeviceMemoryManager* gpu_device_memory{};
""",
      "bool Nx2OpenPageForWrite(u64 vaddr, u8* ptr)")

patch(mem,
      """    if (rasterizer) {
        impl->InvalidateGPUMemory(ptr, size);
    }
""",
      """    if (rasterizer) {
        // NX2: only the application's memory is gathered (System::Nx2GatherNceOpenPages).
        const Kernel::KProcess* const nx2_application = system.ApplicationProcess();
        if (size == LICHII_PAGESIZE && (GetInteger(vaddr) & LICHII_PAGEMASK) == 0 &&
            nx2_application != nullptr &&
            &const_cast<Kernel::KProcess*>(nx2_application)->GetMemory() == this &&
            impl->Nx2OpenPageForWrite(GetInteger(vaddr), ptr)) {
            return true;
        }
        impl->InvalidateGPUMemory(ptr, size);
    }
""",
      "impl->Nx2OpenPageForWrite(GetInteger(vaddr), ptr)")

patch(mem,
      """bool Memory::InvalidateSeparateHeap(void* fault_address) {""",
      """std::unique_lock<std::recursive_mutex> Memory::Nx2GatherOpenPages(
    std::function<void(PAddr, size_t)>& callback) {
    return impl->Nx2GatherOpenPages(callback);
}

bool Memory::InvalidateSeparateHeap(void* fault_address) {""",
      "Memory::Nx2GatherOpenPages(")
include(mem, ["<functional>", "<memory>", "<unordered_map>", '"common/scratch_buffer.h"'])

patch("core/memory.h",
      """    bool InvalidateNCE(Common::ProcessAddress vaddr, size_t size);
""",
      """    bool InvalidateNCE(Common::ProcessAddress vaddr, size_t size);

    /// NX2 (NCE), on the GPU thread: closes the pages InvalidateNCE opened for writing and
    /// reports the bytes written to them (device address, size). Hold the returned lock until
    /// those writes are applied.
    std::unique_lock<std::recursive_mutex> Nx2GatherOpenPages(
        std::function<void(PAddr, size_t)>& callback);
""",
      "Nx2GatherOpenPages(")
include("core/memory.h", ["<functional>", "<mutex>"])

patch("core/core.h",
      """    void GatherGPUDirtyMemory(std::function<void(PAddr, size_t)>& callback);
""",
      """    void GatherGPUDirtyMemory(std::function<void(PAddr, size_t)>& callback);

    /// NX2 (NCE): Memory::Nx2GatherOpenPages of the application process.
    std::unique_lock<std::recursive_mutex> Nx2GatherNceOpenPages(
        std::function<void(PAddr, size_t)>& callback);
""",
      "Nx2GatherNceOpenPages(")

patch("core/core.cpp",
      """void System::GatherGPUDirtyMemory(std::function<void(PAddr, size_t)>& callback) {""",
      """std::unique_lock<std::recursive_mutex> System::Nx2GatherNceOpenPages(
    std::function<void(PAddr, size_t)>& callback) {
    Kernel::KProcess* const process = ApplicationProcess();
    if (process == nullptr) {
        return {};
    }
    return process->GetMemory().Nx2GatherOpenPages(callback);
}

void System::GatherGPUDirtyMemory(std::function<void(PAddr, size_t)>& callback) {""",
      "System::Nx2GatherNceOpenPages(")

gpu = "video_core/gpu.cpp"
patch(gpu,
      """        std::vector<std::pair<DAddr, u64>> writes;
        {
            // Gathered writes stay pending (ForEachPendingCpuWrite) until they are applied: a
            // download written to guest memory meanwhile on another thread (FinishRead) would
            // otherwise write the GPU's older bytes over them.
            std::scoped_lock lock{applying_writes_mutex};
            std::function<void(PAddr, size_t)> callback_writes(
                [&writes](PAddr address, size_t size) { writes.emplace_back(address, size); });
            system.GatherGPUDirtyMemory(callback_writes);
""",
      """        std::vector<std::pair<DAddr, u64>> writes;
        std::function<void(PAddr, size_t)> callback_writes(
            [&writes](PAddr address, size_t size) { writes.emplace_back(address, size); });
        // NX2 (NCE): the bytes written to the pages opened at write faults. The lock (taken
        // before applying_writes_mutex, as while applying) keeps those pages closed until the
        // writes are applied below.
        auto nx2_open_lock = system.Nx2GatherNceOpenPages(callback_writes);
        {
            // Gathered writes stay pending (ForEachPendingCpuWrite) until they are applied: a
            // download written to guest memory meanwhile on another thread (FinishRead) would
            // otherwise write the GPU's older bytes over them.
            std::scoped_lock lock{applying_writes_mutex};
            system.GatherGPUDirtyMemory(callback_writes);
""",
      "system.Nx2GatherNceOpenPages(callback_writes)")
patch(gpu,
      """        SCOPE_EXIT {
            std::scoped_lock lock{applying_writes_mutex};
            std::erase(applying_writes, &writes);
        };
""",
      """        SCOPE_EXIT {
            if (nx2_open_lock.owns_lock()) {
                nx2_open_lock.unlock();
            }
            std::scoped_lock lock{applying_writes_mutex};
            std::erase(applying_writes, &writes);
        };
""",
      "nx2_open_lock.owns_lock()")
include(gpu, ["<mutex>"])
print("NCE writes to pages with GPU-written bytes: page opened at once, writes found by comparison")
