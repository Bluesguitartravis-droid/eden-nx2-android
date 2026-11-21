#!/usr/bin/env python3
"""Equal/mask semaphore acquires on the syncpoint shim or a mirror-shadow word: counted, and passed
without draining the split when the word already meets them (EDEN_NX2_SHIM_ACQUIRE_PASS=1).

Build 217 (Lichii's #2143 series ported), Ocarina at Lon Lon Ranch: ~8,400 acquires a second still
drain the split, 82,938 of 85,831 in 10 s because their word is "elsewhere" (GpuSplit::
SettledWordAt): the read-only syncpoint shim (MemoryManager::OverlapsSyncpointShim) or a word
Nx2MirrorShadow diverts. The front then waits ~200 ms a second for the back and the GPU is only
~70 % busy.

Syncpoints and NVN2's queue progress values only advance, so an acquire the word meets now waits
for work that has already run. With the switch, CheckWithoutDrain reads such a word as the back
would (the shim serves syncpoints; a diverted word reads its shadow value) and passes the acquire
when it already matches; otherwise it drains as before. Off by default. Either way the log line
"NX2_SHIM_ACQUIRES" (every 10 s) counts shim and shadow acquires and how many already matched.
EDEN_NX2_ACQUIRE_VERIFY=1 checks the passes (drains anyway and counts mismatches).

Usage: apply-nx2-shim-acquires.py <upstream source root>
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "src/video_core/engines/puller.cpp"
text = path.read_text()
if "NX2 shim acquires" in text:
    print("puller.cpp: already patched")
    sys.exit(0)

helper = r'''// NX2 shim acquires: equal/mask acquires on the syncpoint shim or a diverted mirror word.
struct Nx2ShimAcquireStats {
    std::atomic<u64> shim{};
    std::atomic<u64> shim_met{};
    std::atomic<u64> shadow{};
    std::atomic<u64> shadow_met{};
    std::atomic<s64> last_report_ns{};
};
Nx2ShimAcquireStats g_nx2_shim_acquires;

bool Nx2ShimAcquirePass() {
    static const bool enabled{[] {
        const char* const value{std::getenv("EDEN_NX2_SHIM_ACQUIRE_PASS")};
        return value != nullptr && value[0] == '1';
    }()};
    return enabled;
}

void Nx2ReportShimAcquires() {
    const s64 now = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch())
                        .count();
    s64 last = g_nx2_shim_acquires.last_report_ns.load(std::memory_order_relaxed);
    if (last == 0) {
        g_nx2_shim_acquires.last_report_ns.compare_exchange_strong(last, now);
        return;
    }
    if (now - last < 10'000'000'000LL ||
        !g_nx2_shim_acquires.last_report_ns.compare_exchange_strong(last, now)) {
        return;
    }
    const u64 shim = g_nx2_shim_acquires.shim.exchange(0);
    const u64 shim_met = g_nx2_shim_acquires.shim_met.exchange(0);
    const u64 shadow = g_nx2_shim_acquires.shadow.exchange(0);
    const u64 shadow_met = g_nx2_shim_acquires.shadow_met.exchange(0);
    LOG_INFO(HW_GPU,
             "NX2_SHIM_ACQUIRES 10s: syncpoint shim {} ({} already met), mirror shadow {} ({} "
             "already met), passed without a drain: {}",
             shim, shim_met, shadow, shadow_met, Nx2ShimAcquirePass() ? "yes" : "no");
}

// The value the back would read for an acquire on a shim or diverted word, read now.
std::optional<bool> Nx2ShimAcquireMetNow(DmaPusher& dma_pusher,
                                         const Puller::PendingAcquire& acquire, u64 device) {
    auto& memory = dma_pusher.memory_manager;
    const bool shim = memory.OverlapsSyncpointShim(acquire.address, sizeof(u32));
    auto& shadow = Nx2MirrorShadow::Get();
    if (!shim && !shadow.Diverts(device)) {
        return std::nullopt;
    }
    u32 word = memory.Read<u32>(acquire.address);
    if (!shim) {
        word = shadow.Read(device, word).value_or(word);
    }
    const bool met = acquire.Matches(word);
    (shim ? g_nx2_shim_acquires.shim : g_nx2_shim_acquires.shadow)
        .fetch_add(1, std::memory_order_relaxed);
    if (met) {
        (shim ? g_nx2_shim_acquires.shim_met : g_nx2_shim_acquires.shadow_met)
            .fetch_add(1, std::memory_order_relaxed);
    }
    Nx2ReportShimAcquires();
    return met;
}

'''
anchor = "AcquireCheck CheckWithoutDrain(DmaPusher& dma_pusher, const Puller::PendingAcquire& acquire,"
if text.count(anchor) != 1:
    sys.exit("puller.cpp: CheckWithoutDrain anchor not found")
text = text.replace(anchor, helper + anchor, 1)

old = """        VideoCommon::Count(stats.drain_operation);
        if (!settled) {
            VideoCommon::Count(stats.unsettled[static_cast<size_t>(why)]);
        }
        return AcquireCheck::Drain;"""
new = """        if (!settled && why == VideoCommon::AcquireUnsettled::Elsewhere) {
            // NX2 shim acquires: the word only advances; met now, it stays met.
            const auto met_now = Nx2ShimAcquireMetNow(dma_pusher, acquire, *device);
            if (met_now && *met_now && Nx2ShimAcquirePass()) {
                return AcquireCheck::Met;
            }
        }
        VideoCommon::Count(stats.drain_operation);
        if (!settled) {
            VideoCommon::Count(stats.unsettled[static_cast<size_t>(why)]);
        }
        return AcquireCheck::Drain;"""
if text.count(old) != 1:
    sys.exit(f"puller.cpp: drain anchor found {text.count(old)} times")
text = text.replace(old, new, 1)
for inc in ("#include <chrono>\n", "#include <optional>\n"):
    if inc not in text:
        text = text.replace("#include <atomic>\n", "#include <atomic>\n" + inc, 1)
path.write_text(text)
print("puller.cpp: shim/shadow acquires counted; EDEN_NX2_SHIM_ACQUIRE_PASS passes met ones")
