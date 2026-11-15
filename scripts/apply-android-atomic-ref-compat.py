#!/usr/bin/env python3
"""Android NDK's libc++ has no std::atomic_ref. Newer dynarmic uses it in the arm64
exclusive monitor for plain loads, stores and compare-exchanges; replace those with a
tiny wrapper over the GCC/Clang __atomic builtins, which compile to the same code."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
header = root / "src/dynarmic/src/dynarmic/interface/exclusive_monitor.h"
source = root / "src/dynarmic/src/dynarmic/backend/arm64/exclusive_monitor.cpp"

shim = '''
// Android NDK libc++ lacks atomic_ref: the same operations on the __atomic builtins.
template<typename T>
struct AtomicRefCompat {
    T& ref;
    static constexpr int Order(std::memory_order order) {
        return static_cast<int>(order);
    }
    static constexpr int FailureOrder(std::memory_order order) {
        return order == std::memory_order_acq_rel ? __ATOMIC_ACQUIRE
             : order == std::memory_order_release ? __ATOMIC_RELAXED
                                                  : static_cast<int>(order);
    }
    T load(std::memory_order order = std::memory_order_seq_cst) const {
        return __atomic_load_n(&ref, Order(order));
    }
    void store(T value, std::memory_order order = std::memory_order_seq_cst) const {
        __atomic_store_n(&ref, value, Order(order));
    }
    bool compare_exchange_strong(T& expected, T desired,
                                 std::memory_order order = std::memory_order_seq_cst) const {
        return __atomic_compare_exchange_n(&ref, &expected, desired, false, Order(order),
                                           FailureOrder(order));
    }
};
'''

text = header.read_text()
if "AtomicRefCompat" not in text:
    anchor = "using Vector = std::array<std::uint64_t, 2>;\n"
    if anchor not in text:
        raise SystemExit(f"anchor not found in {header}")
    text = text.replace(anchor, anchor + shim, 1)
text = text.replace("std::atomic_ref<VAddr>", "AtomicRefCompat<VAddr>")
header.write_text(text)

src = source.read_text().replace("std::atomic_ref<VAddr>", "AtomicRefCompat<VAddr>")
source.write_text(src)

for f in (header, source):
    if "std::atomic_ref" in f.read_text():
        raise SystemExit(f"std::atomic_ref still used in {f}")
print("atomic_ref compat applied")

# Android NDK libc++ also lacks std::atomic<std::shared_ptr<T>> (C++20). WindowTrace's
# Sessions only loads and stores it, so a mutex around a plain shared_ptr is equivalent.
trace = root / "src/core/arm/dynarmic/window_trace.h"
if trace.exists() and "std::atomic<std::shared_ptr" not in trace.read_text():
    print("window_trace.h already avoids atomic shared_ptr; skipping")
elif trace.exists():
    t = trace.read_text()
    replacements = [
        ("""        current.store(base != 0 ? std::make_shared<Session>(owner, base, next_id.fetch_add(1))
                                : nullptr);""",
         """        auto session = base != 0 ? std::make_shared<Session>(owner, base, next_id.fetch_add(1))
                                 : nullptr;
        std::scoped_lock lk{current_mutex};
        current = std::move(session);"""),
        ("""        auto session = current.load();""",
         """        std::shared_ptr<Session> session;
        {
            std::scoped_lock lk{current_mutex};
            session = current;
        }"""),
        ("""    std::atomic<std::shared_ptr<Session>> current;""",
         """    mutable std::mutex current_mutex;
    std::shared_ptr<Session> current;"""),
    ]
    for old, new in replacements:
        if old in t:
            t = t.replace(old, new, 1)
        elif new not in t:
            raise SystemExit(f"window_trace.h shape changed: {old.strip()[:60]}")
    if "#include <mutex>" not in t:
        t = t.replace("#pragma once\n", "#pragma once\n\n#include <mutex>\n", 1)
    trace.write_text(t)
    if "std::atomic<std::shared_ptr" in t:
        raise SystemExit("atomic shared_ptr still used in window_trace.h")
    print("window_trace atomic shared_ptr compat applied")

# Lichii faa4bd13a (video_core/nx2.h semaphore waiters) reads guest words with
# std::atomic_ref<u32>{word}.load(order): the same load on the __atomic builtin.
import re
pattern = re.compile(
    r"std::atomic_ref<(u32|u64|std::uint32_t|std::uint64_t)>\{([^{}]+)\}\.load\(std::memory_order_(relaxed|acquire|seq_cst)\)")
for path in sorted((root / "src").rglob("*")):
    if path.suffix not in (".h", ".cpp") or "tests" in path.parts or "dynarmic" in path.parts:
        continue
    text = path.read_text(errors="ignore")
    if "std::atomic_ref" not in text:
        continue
    text = pattern.sub(lambda m: f"__atomic_load_n(&({m.group(2)}), __ATOMIC_{m.group(3).upper()})", text)
    if "std::atomic_ref" in text:
        raise SystemExit(f"std::atomic_ref still used in {path}")
    path.write_text(text)
    print(f"atomic_ref loads rewritten in {path.relative_to(root)}")
