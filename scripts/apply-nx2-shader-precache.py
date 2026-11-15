#!/usr/bin/env python3
"""Shader precache on Android (EDEN_NX2_PRECACHE=1) and building every cached pipeline at boot
(EDEN_NX2_BUILD_ALL_CACHED=1).

Lichii's desktop app can precache a game's shaders before it starts: it reads the game files
(Switch 2 .nup packages too), takes every compute program out of the game's shader archives,
converts them into a precache file (warm-compute.bin, next to the pipeline cache), and the
renderer builds them all on the loading screen, again only after a driver or SPIR-V change.
The renderer part is in the Android build too (it reports as "Building shaders"), but the app
never built the precache file and the setting is not in its menus.

With EDEN_NX2_PRECACHE=1, a game's first start (and a start after the precache builder changed)
builds the precache file before loading, with the loading screen counting the programs, then
the renderer precaches as on desktop. The first build reads the whole game and takes minutes.

EDEN_NX2_BUILD_ALL_CACHED=1 turns on build_all_cached_shaders: every graphics pipeline earlier
sessions recorded is built while the game loads (not only those the last sessions used), so
places seen before do not compile again during play. Longer loading, more memory.
Usage: python3 scripts/apply-nx2-shader-precache.py <upstream-root>
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) / "src/android/app/src/main/jni/native.cpp"
text = path.read_text()
MARK = "NX2 shader precache"
if MARK in text:
    print("already patched")
    raise SystemExit(0)


def replace(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit(f"native.cpp: anchor found {text.count(old)} times:\n{old}")
    text = text.replace(old, new, 1)


replace("static EmulationSession s_instance;\n", r'''static EmulationSession s_instance;

// NX2 shader precache: the game file being started, for the precache builder.
static std::string& Nx2PrecacheGamePath() {
    static std::string path;
    return path;
}

static bool Nx2SwitchOn(const char* name) {
    const char* const value{std::getenv(name)};
    return value != nullptr && value[0] == '1';
}

// NX2 shader precache: settings and the precache file before the disk cache loads.
static void Nx2PrepareShaderPrecache(
    Core::System& system, u64 title_id,
    const std::function<void(VideoCore::LoadCallbackStage, int, int)>& progress) {
    if (Nx2SwitchOn("EDEN_NX2_BUILD_ALL_CACHED")) {
        Settings::values.build_all_cached_shaders.SetValue(true);
        LOG_INFO(Frontend, "NX2 shader precache: building every cached pipeline at boot");
    }
    if (!Nx2SwitchOn("EDEN_NX2_PRECACHE") || title_id == 0) {
        return;
    }
    namespace ShaderWarm = VideoCommon::ShaderWarm;
    Settings::values.precache_shaders.SetValue(Settings::PrecacheShaders::Always);
    const auto warm_file = ShaderWarm::ComputeWarmPath(title_id);
    const auto stale = ShaderWarm::WarmFilesStale(warm_file);
    const std::string& game_path = Nx2PrecacheGamePath();
    if (!stale) {
        LOG_INFO(Frontend, "NX2 shader precache: precache file of {:016x} is current", title_id);
        return;
    }
    if (game_path.empty()) {
        LOG_WARNING(Frontend, "NX2 shader precache: no game path for {:016x}", title_id);
        return;
    }
    LOG_INFO(Frontend, "NX2 shader precache: {} for {:016x}, building it from the game files",
             *stale, title_id);
    ShaderWarm::BuildOptions options;
    // Conversion threads: all but two cores (the builder's default is a quarter, 2 to 6).
    const unsigned cores{std::max(4u, std::thread::hardware_concurrency())};
    options.threads = cores - 2;
    const auto begin{std::chrono::steady_clock::now()};
    auto last_report{begin - std::chrono::seconds{1}};
    std::string last_stage;
    const auto result = ShaderWarm::BuildWarmFilesWith(
        system, Common::FS::ToU8String(game_path), title_id, options,
        [&](const ShaderWarm::Progress& step) {
            const auto now{std::chrono::steady_clock::now()};
            if (step.stage != last_stage) {
                LOG_INFO(Frontend, "NX2 shader precache: {} ({} s)", step.stage,
                         std::chrono::duration_cast<std::chrono::seconds>(now - begin).count());
                last_stage = step.stage;
            }
            if (now - last_report >= std::chrono::milliseconds{250}) {
                last_report = now;
                progress(VideoCore::LoadCallbackStage::Build, static_cast<int>(step.done),
                         static_cast<int>(step.total));
            }
        },
        {});
    LOG_INFO(Frontend,
             "NX2 shader precache: build {} in {} s: {} archives, {} compute programs, {} "
             "skipped{}{}",
             result.ok ? "done" : "failed",
             std::chrono::duration_cast<std::chrono::seconds>(std::chrono::steady_clock::now() -
                                                              begin)
                 .count(),
             result.archives, result.compute_programs, result.skipped,
             result.error.empty() ? "" : ": ", result.error);
}
''')

replace("    m_load_result = m_system.Load(EmulationSession::GetInstance().Window(), filepath, params);\n",
        "    Nx2PrecacheGamePath() = filepath; // " + MARK + "\n"
        "    m_load_result = m_system.Load(EmulationSession::GetInstance().Window(), filepath, params);\n")

replace("    // Load the disk shader cache.\n"
        "    if (Settings::values.use_disk_shader_cache.GetValue()) {\n"
        "        LoadDiskCacheProgress(VideoCore::LoadCallbackStage::Prepare, 0, 0);\n",
        "    // Load the disk shader cache.\n"
        "    if (Settings::values.use_disk_shader_cache.GetValue()) {\n"
        "        LoadDiskCacheProgress(VideoCore::LoadCallbackStage::Prepare, 0, 0);\n"
        "        // " + MARK + "\n"
        "        Nx2PrepareShaderPrecache(m_system, m_system.GetApplicationProcessProgramID(),\n"
        "                                 LoadDiskCacheProgress);\n")

for inc in ("#include <cstdlib>\n", "#include <functional>\n", "#include <thread>\n",
            "#include <algorithm>\n", "#include <chrono>\n"):
    if inc not in text:
        text = text.replace("#include <jni.h>\n", inc + "#include <jni.h>\n", 1)
text = text.replace('#include "video_core/renderer_base.h"\n',
                    '#include "video_core/renderer_base.h"\n'
                    '#include "video_core/shader_warm/shader_warm_builder.h"\n', 1)
path.write_text(text)
print("shader precache switches installed (EDEN_NX2_PRECACHE, EDEN_NX2_BUILD_ALL_CACHED)")
