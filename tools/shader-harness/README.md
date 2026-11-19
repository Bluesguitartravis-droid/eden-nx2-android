# Shader harness (local, not built in CI)

Builds Lichii's shader recompiler on a Linux machine and emits a vertex shader that reads an object
through one untracked pointer (the shape of Ocarina's scenery vertex shaders), so SPIR-V changes can
be checked with `spirv-val` before a 50-minute APK build.

1. Lichii at the pinned commit with `patches/arm64-macro-jit.patch` and every
   `scripts/apply-*.py <tree>` the workflow runs, in order.
2. sirit v1.0.7, fmt 12.1.0, SPIRV-Headers vulkan-sdk-1.4.341.0 next to it; `libboost-dev`, `spirv-tools`.
   Paths are at the top of `CMakeLists.txt`.
3. `cmake -S . -B b && make -C b harness` (the recompiler takes ~3 min on 2 cores).
4. `./b/harness out.spv [1 = two blocks]`, then
   `spirv-val --target-env vulkan1.1 --scalar-block-layout out.spv`.

Switches such as `EDEN_NX2_GMEM_ROUTE_CACHE=0` and `EDEN_NX2_GMEM_HOIST=0` work as on the device.
