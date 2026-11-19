#!/usr/bin/env python3
"""Android: label mimalloc's memory so memory_breakdown can see it.

Build 189 on the Odin 3: Ocarina is closed by Android for low memory at ~17 GB. The
biggest group in memory_breakdown is "[anonymous, unnamed]", which grows from 3.6 GB at
boot to 8.0 GB after loading Hyrule Field, while the "native heap" figure stays at
~0.6 GB. apply-android-mimalloc.py routes every C++ new/delete in liblichii-android
to mimalloc 2.1.7, whose mmap()s carry no name, and mallinfo() only counts bionic's
malloc. So the unnamed group is very likely the emulator's own C++ objects (caches,
staging copies), but nothing showed it.

This names every anonymous mapping mimalloc makes "mimalloc" (prctl PR_SET_VMA_ANON_NAME),
so memory_breakdown shows "[anon:mimalloc]" separately from the JIT, Vulkan and other
unnamed memory. It changes no allocation behaviour.

Run after apply-android-mimalloc.py. mimalloc is fetched at CMake configure time, so the
edit is a FetchContent PATCH_COMMAND (a small CMake script) applied to its prim.c.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
jni = root / "src/android/app/src/main/jni"
cm = jni / "CMakeLists.txt"
t = cm.read_text()
if "nx2_mimalloc_names" in t:
    print("mimalloc names already applied"); sys.exit(0)
if "nx2_mimalloc" not in t:
    raise SystemExit("run apply-android-mimalloc.py first")

(jni / "nx2_mimalloc_names.cmake").write_text(r'''# Eden NX2 Android (nx2_mimalloc_names): name mimalloc's anonymous mappings "mimalloc".
# Run as a FetchContent PATCH_COMMAND inside the mimalloc source tree.
set(prim "src/prim/unix/prim.c")
file(READ "${prim}" text)
string(FIND "${text}" "unix_mmap_prim_raw" already)
if(NOT already EQUAL -1)
  return()
endif()
set(head "static void* unix_mmap_prim(void* addr, size_t size, size_t try_alignment, int protect_flags, int flags, int fd) {")
string(FIND "${text}" "${head}" at)
if(at EQUAL -1)
  message(FATAL_ERROR "nx2_mimalloc_names: unix_mmap_prim shape changed")
endif()
set(wrapper [=[
#include <sys/prctl.h>
#ifndef PR_SET_VMA
#define PR_SET_VMA 0x53564d41
#endif
#ifndef PR_SET_VMA_ANON_NAME
#define PR_SET_VMA_ANON_NAME 0
#endif
static void* unix_mmap_prim_raw(void* addr, size_t size, size_t try_alignment, int protect_flags, int flags, int fd);
// Eden NX2 Android: label mimalloc's memory so /proc/self/smaps shows [anon:mimalloc].
static void* unix_mmap_prim(void* addr, size_t size, size_t try_alignment, int protect_flags, int flags, int fd) {
  void* p = unix_mmap_prim_raw(addr, size, try_alignment, protect_flags, flags, fd);
  if (p != NULL && fd == -1) {
    prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME, (unsigned long)p, (unsigned long)size, (unsigned long)"mimalloc");
  }
  return p;
}
static void* unix_mmap_prim_raw(void* addr, size_t size, size_t try_alignment, int protect_flags, int flags, int fd) {]=])
string(REPLACE "${head}" "${wrapper}" text "${text}")
file(WRITE "${prim}" "${text}")
message(STATUS "nx2_mimalloc_names: mimalloc mappings will be named")
''')

old = """FetchContent_Declare(nx2_mimalloc
    URL https://github.com/microsoft/mimalloc/archive/refs/tags/v2.1.7.tar.gz
)"""
new = """FetchContent_Declare(nx2_mimalloc
    URL https://github.com/microsoft/mimalloc/archive/refs/tags/v2.1.7.tar.gz
    # nx2_mimalloc_names: label mimalloc's mappings for memory_breakdown.
    PATCH_COMMAND ${CMAKE_COMMAND} -P ${CMAKE_CURRENT_SOURCE_DIR}/nx2_mimalloc_names.cmake
)"""
if old not in t:
    raise SystemExit("mimalloc FetchContent_Declare shape changed")
cm.write_text(t.replace(old, new, 1))
print("mimalloc mappings will be named [anon:mimalloc]")
