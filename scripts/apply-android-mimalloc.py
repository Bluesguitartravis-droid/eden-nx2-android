#!/usr/bin/env python3
"""Android: serve the emulator's C++ allocations (operator new/delete) from mimalloc.

DKB crashes with "Scudo ERROR: internal map failure": tombstones show ~18 of
scudo's 256 MiB per-size-class primary regions full (~4.8 GiB of small
objects). Scudo then falls back to its secondary allocator, which costs 2-3
memory mappings per allocation, and the process hits vm.max_map_count
(65,744 mappings at the crash, ~30k of them scudo:secondary). mimalloc has no
per-size-class cap and reserves memory in large arenas, so it needs only a
handful of mappings. malloc/free themselves are left to bionic.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
jni = root / "src/android/app/src/main/jni"
cm = jni / "CMakeLists.txt"
t = cm.read_text()
if "nx2_mimalloc" in t:
    print("mimalloc already applied"); sys.exit(0)

(jni / "nx2_mimalloc_new_delete.cpp").write_text("""// Eden NX2 Android: route operator new/delete in liblichii-android.so to mimalloc.
// libc++ is linked statically and the library uses -Bsymbolic, so these
// definitions replace libc++'s for every allocation made by the emulator.
#include <mimalloc-new-delete.h>
""")

src_anchor = "    native_post_processing.cpp\n)"
if src_anchor not in t:
    raise SystemExit("jni/CMakeLists.txt: source list shape changed")
t = t.replace(src_anchor, "    native_post_processing.cpp\n    nx2_mimalloc_new_delete.cpp\n)", 1)

t += """
# Eden NX2 Android: mimalloc for operator new/delete (nx2_mimalloc).
include(FetchContent)
set(MI_OVERRIDE OFF CACHE BOOL "" FORCE)
set(MI_BUILD_SHARED OFF CACHE BOOL "" FORCE)
set(MI_BUILD_STATIC ON CACHE BOOL "" FORCE)
set(MI_BUILD_OBJECT OFF CACHE BOOL "" FORCE)
set(MI_BUILD_TESTS OFF CACHE BOOL "" FORCE)
# liblichii-android.so is dlopened: initial-exec TLS fails to load on bionic.
set(MI_LOCAL_DYNAMIC_TLS ON CACHE BOOL "" FORCE)
set(MI_INSTALL_TOPLEVEL OFF CACHE BOOL "" FORCE)
FetchContent_Declare(nx2_mimalloc
    URL https://github.com/microsoft/mimalloc/archive/refs/tags/v2.1.7.tar.gz
)
FetchContent_MakeAvailable(nx2_mimalloc)
target_link_libraries(lichii-android PRIVATE mimalloc-static)
"""
cm.write_text(t)
print("mimalloc operator new/delete installed for liblichii-android")
