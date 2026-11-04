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
