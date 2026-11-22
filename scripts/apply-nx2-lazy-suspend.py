#!/usr/bin/env python3
"""EDEN_NX2_LAZY_SUSPEND=1: an acquire that CheckWithoutDrain finds NotMet (no release to its word
is queued on any back, and memory does not meet it) suspends its channel without first draining
the split's back and releasing the fences synchronously.

At Hyrule Field about 8,000 acquires a second take this path and nearly all are still unmet after
the drain: the release belongs to another channel that has not sent it yet. Each drain stops the
front until the back has caught up, which serializes the two halves of the split.

With the switch the fences are released on the back instead (asynchronously, after the work
queued before the acquire), the waiter is registered first as before, and the channel suspends.
CanResumeAcquire still drains at most once per 1 ms for a waiting acquire (the existing valve),
so work only a drain lands is at most that late, and with this switch that drain also releases
the fences. Acquires found Drain (a write the front cannot
see) keep the drain."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/video_core/engines/puller.cpp"
t = f.read_text()
if "Nx2LazySuspend" in t:
    print("puller.cpp: already patched")
    sys.exit(0)

anchor = "void DrainBack() {"
helper = """// EDEN_NX2_LAZY_SUSPEND=1 (scripts/apply-nx2-lazy-suspend.py): an acquire found NotMet without
// a drain suspends its channel without draining the back first.
bool Nx2LazySuspend() {
    static const bool enabled{[] {
        const char* const value{std::getenv("EDEN_NX2_LAZY_SUSPEND")};
        return value != nullptr && value[0] == '1';
    }()};
    return enabled;
}

"""
if t.count(anchor) != 1:
    sys.exit("puller.cpp: DrainBack not found")
t = t.replace(anchor, helper + anchor, 1)

old_drain = """        VideoCommon::Count(stats.drained);
        DrainBack();
        if (acquire.Matches(ReadSemaphoreWord(dma_pusher, acquire.address))) {
            VideoCommon::Nx2FastDraw::NoteSync();
            return;
        }
        if (not_met) {
            VideoCommon::Count(stats.trigger_not_met_twice);
        }"""
new_drain = """        const bool lazy = not_met && Nx2LazySuspend();
        if (!lazy) {
            VideoCommon::Count(stats.drained);
            DrainBack();
            if (acquire.Matches(ReadSemaphoreWord(dma_pusher, acquire.address))) {
                VideoCommon::Nx2FastDraw::NoteSync();
                return;
            }
            if (not_met) {
                VideoCommon::Count(stats.trigger_not_met_twice);
            }
        }"""
old_fences = """        ReleaseFences(dma_pusher);
        if (!waiters.Latched(this) && !acquire.Matches(ReadSemaphoreWord(dma_pusher, acquire.address))) {"""
new_fences = """        if (lazy) {
            // After the work queued before the acquire, without waiting for it here.
            OnBack([rasterizer = dma_pusher.rasterizer] { rasterizer->ReleaseFences(); },
                   FenceWrites);
            // Sent now, not when the batch fills: the guest CPU may wait on what this releases
            // before it sends the release the acquire waits for (222 hung at boot without it).
            VideoCommon::GpuSplit::Get().Publish();
            // A release that landed before the waiter was added latches nothing.
            if (waiters.Latched(this) ||
                acquire.Matches(ReadSemaphoreWord(dma_pusher, acquire.address))) {
                waiters.Remove(this);
                VideoCommon::Nx2FastDraw::NoteSync();
                return;
            }
            pending_acquire = acquire;
            dma_pusher.suspensions.fetch_add(1, std::memory_order_release);
            VideoCommon::Nx2Profile::Add(VideoCommon::Nx2Profile::Counter::AcquireWaits, 1);
            return;
        }
        ReleaseFences(dma_pusher);
        if (!waiters.Latched(this) && !acquire.Matches(ReadSemaphoreWord(dma_pusher, acquire.address))) {"""
# The fenced releases (query reports with a fence) run only when the fences are released. The
# immediate path releases them after a drain; a lazily suspended acquire releases them with the
# 1 ms valve's drain instead (223 hung at boot without this: the guest CPU spun on a fenced write).
old_valve = """    if (const auto now = std::chrono::steady_clock::now();
        delayed && now - pending_acquire->last_fences >= std::chrono::milliseconds{1}) {"""
new_valve = """    if (const auto now = std::chrono::steady_clock::now();
        (delayed || Nx2LazySuspend()) &&
        now - pending_acquire->last_fences >= std::chrono::milliseconds{1}) {"""
for old, new in ((old_drain, new_drain), (old_fences, new_fences), (old_valve, new_valve)):
    if t.count(old) != 1:
        sys.exit(f"puller.cpp: expected one match for {old[:70]!r}, found {t.count(old)}")
    t = t.replace(old, new, 1)
f.write_text(t)
print("puller.cpp: EDEN_NX2_LAZY_SUSPEND=1 suspends unmet acquires without draining the back")
