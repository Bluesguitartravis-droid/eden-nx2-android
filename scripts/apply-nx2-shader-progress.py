#!/usr/bin/env python3
"""Shader loading screen: what is being done, how far it is and about how long is left.

The loading screen said "Building shaders" with a bare bar, nothing while the precache builder
(EDEN_NX2_PRECACHE=1, build 171) read the game files (no total yet: the screen went back to
"Loading"), and showed the precache's own builds as plain shader building.
Now the text names the step and counts it, with a time estimate once a step has run 3 s:

  Precache: reading game files
  Precache: extracting shaders  812 / 3104 (26%) · ~3 min left
  Precompiling shaders  1460 / 3104 (47%) · ~2 min left
  Building shaders  233 / 1124 (20%) · ~40 s left

Native side: the precache builder's steps report as stages 10-13 and the renderer's precache
(Warm) as stage 3, where it was folded into Build. The bar is indeterminate while a step has no
total.
First built in build 173; the R8 keep rule for LoadCallbackStage from build 177.
Usage: python3 scripts/apply-nx2-shader-progress.py <upstream-root>
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
app = root / "src/android/app/src/main"
java = app / "java/io/github/zortos293/lichii"
MARK = "NX2 shader progress"


def patch(path, edits):
    text = path.read_text()
    if MARK in text:
        print(f"{path.name}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{path.name}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    path.write_text(text)
    print(f"{path.name}: patched")


# Native: keep the renderer's precache stage; the precache builder's steps as stages 10-13.
patch(app / "jni/native.cpp", [
    (
        "    // The app has no precache stage: it shows as shader building.\n"
        "    if (stage == VideoCore::LoadCallbackStage::Warm) {\n"
        "        stage = VideoCore::LoadCallbackStage::Build;\n"
        "    }\n",
        "    // " + MARK + ": the app shows the precache stage (3) as its own step.\n",
    ),
    (
        "            if (now - last_report >= std::chrono::milliseconds{250}) {\n"
        "                last_report = now;\n"
        "                progress(VideoCore::LoadCallbackStage::Build, static_cast<int>(step.done),\n"
        "                         static_cast<int>(step.total));\n"
        "            }\n",
        "            const bool nx2_new_stage{step.stage != last_stage};\n"
        "            if (nx2_new_stage || now - last_report >= std::chrono::milliseconds{250}) {\n"
        "                last_report = now;\n"
        "                // " + MARK + ": the builder's steps are stages 10-13 in the app.\n"
        "                int nx2_stage{10};\n"
        "                if (step.stage == \"Extracting shaders\") {\n"
        "                    nx2_stage = 11;\n"
        "                } else if (step.stage == \"Converting\") {\n"
        "                    nx2_stage = 12;\n"
        "                } else if (step.stage == \"Writing\") {\n"
        "                    nx2_stage = 13;\n"
        "                }\n"
        "                progress(static_cast<VideoCore::LoadCallbackStage>(nx2_stage),\n"
        "                         static_cast<int>(step.done), static_cast<int>(step.total));\n"
        "            }\n",
    ),
])

# The stage line is logged before the report: move the logging after it so last_stage is still
# the previous one when the report checks it.
native = app / "jni/native.cpp"
text = native.read_text()
old_log = ("            const auto now{std::chrono::steady_clock::now()};\n"
           "            if (step.stage != last_stage) {\n"
           "                LOG_INFO(Frontend, \"NX2 shader precache: {} ({} s)\", step.stage,\n"
           "                         std::chrono::duration_cast<std::chrono::seconds>(now - begin).count());\n"
           "                last_stage = step.stage;\n"
           "            }\n")
if old_log in text:
    text = text.replace(old_log, "            const auto now{std::chrono::steady_clock::now()};\n", 1)
    anchor = ("                progress(static_cast<VideoCore::LoadCallbackStage>(nx2_stage),\n"
              "                         static_cast<int>(step.done), static_cast<int>(step.total));\n"
              "            }\n")
    if text.count(anchor) != 1:
        raise SystemExit("native.cpp: precache progress call not found")
    text = text.replace(anchor, anchor +
                        "            if (nx2_new_stage) {\n"
                        "                LOG_INFO(Frontend, \"NX2 shader precache: {} ({} s)\", step.stage,\n"
                        "                         std::chrono::duration_cast<std::chrono::seconds>(now - begin).count());\n"
                        "                last_stage = step.stage;\n"
                        "            }\n", 1)
    native.write_text(text)
    print("native.cpp: precache stage logging after the report")
elif "nx2_new_stage" not in text:
    raise SystemExit("native.cpp: precache stage logging not found (apply-nx2-shader-precache.py first)")

patch(java / "disk_shader_cache/DiskShaderCacheProgress.kt", [
    (
        "            when (LoadCallbackStage.values()[stage]) {\n"
        "                LoadCallbackStage.Prepare -> prepareViewModel()\n"
        "                LoadCallbackStage.Build -> emulationViewModel.updateProgress(\n"
        "                    emulationActivity.getString(R.string.building_shaders),\n"
        "                    progress,\n"
        "                    max\n"
        "                )\n"
        "\n"
        "                LoadCallbackStage.Complete -> {}\n"
        "            }\n",
        "            // " + MARK + ": the step, its count and an estimate of the time left.\n"
        "            when (stage) {\n"
        "                0 -> {\n"
        "                    prepareViewModel()\n"
        "                    nx2StageKey = -1\n"
        "                }\n"
        "                1 -> nx2Update(stage, emulationActivity.getString(R.string.building_shaders), progress, max)\n"
        "                2 -> {}\n"
        "                3 -> nx2Update(stage, \"Precompiling shaders\", progress, max)\n"
        "                10 -> nx2Update(stage, \"Precache: reading game files\", progress, max)\n"
        "                11 -> nx2Update(stage, \"Precache: extracting shaders\", progress, max)\n"
        "                12 -> nx2Update(stage, \"Precache: converting shaders\", progress, max)\n"
        "                13 -> nx2Update(stage, \"Precache: writing\", progress, max)\n"
        "                else -> {}\n"
        "            }\n",
    ),
    (
        "    // Equivalent to VideoCore::LoadCallbackStage\n",
        "    private var nx2StageKey = -1\n"
        "    private var nx2StageStart = 0L\n"
        "\n"
        "    private fun nx2Update(stage: Int, base: String, progress: Int, max: Int) {\n"
        "        if (!this::emulationViewModel.isInitialized) {\n"
        "            prepareViewModel()\n"
        "        }\n"
        "        val now = android.os.SystemClock.elapsedRealtime()\n"
        "        if (stage != nx2StageKey) {\n"
        "            nx2StageKey = stage\n"
        "            nx2StageStart = now\n"
        "        }\n"
        "        var text = base\n"
        "        if (max > 0) {\n"
        "            val shown = progress.coerceIn(0, max)\n"
        "            text += \"  $shown / $max (${shown * 100L / max}%)\"\n"
        "            val elapsed = now - nx2StageStart\n"
        "            if (shown in 1 until max && elapsed >= 3000L) {\n"
        "                val left = elapsed * (max - shown) / shown / 1000L\n"
        "                text += if (left >= 90L) \" \\u00b7 ~${(left + 30L) / 60L} min left\"\n"
        "                else \" \\u00b7 ~${left.coerceAtLeast(1L)} s left\"\n"
        "            }\n"
        "        }\n"
        "        emulationViewModel.updateProgress(text, progress, max)\n"
        "    }\n"
        "\n"
        "    // Equivalent to VideoCore::LoadCallbackStage\n",
    ),
])

# The total first, then the count, then the text: the screen compares the count with the total.
patch(java / "model/EmulationViewModel.kt", [(
    "    fun updateProgress(msg: String, progress: Int, max: Int) {\n"
    "        setShaderMessage(msg)\n"
    "        setShaderProgress(progress)\n"
    "        setTotalShaders(max)\n"
    "    }\n",
    "    fun updateProgress(msg: String, progress: Int, max: Int) {\n"
    "        // " + MARK + ": total, count, then text (the text names the step and its count).\n"
    "        setTotalShaders(max)\n"
    "        setShaderProgress(progress)\n"
    "        setShaderMessage(msg)\n"
    "    }\n",
)])

patch(java / "fragments/EmulationFragment.kt", [(
    "        emulationViewModel.shaderProgress.collect(viewLifecycleOwner) {\n"
    "            if (it > 0 && it != emulationViewModel.totalShaders.value) {\n"
    "                binding.loadingProgressIndicator.isIndeterminate = false\n"
    "\n"
    "                if (it < binding.loadingProgressIndicator.max) {\n"
    "                    binding.loadingProgressIndicator.progress = it\n"
    "                }\n"
    "            }\n"
    "\n"
    "            if (it == emulationViewModel.totalShaders.value) {\n"
    "                binding.loadingText.setText(R.string.loading)\n"
    "                binding.loadingProgressIndicator.isIndeterminate = true\n"
    "            }\n"
    "        }\n"
    "        emulationViewModel.totalShaders.collect(viewLifecycleOwner) {\n"
    "            binding.loadingProgressIndicator.max = it\n"
    "        }\n",
    "        // " + MARK + ": a step without a total keeps a moving bar; the text says what runs.\n"
    "        emulationViewModel.shaderProgress.collect(viewLifecycleOwner) {\n"
    "            val total = emulationViewModel.totalShaders.value\n"
    "            if (total > 0 && it < total) {\n"
    "                binding.loadingProgressIndicator.isIndeterminate = false\n"
    "                binding.loadingProgressIndicator.max = total\n"
    "                binding.loadingProgressIndicator.progress = it.coerceAtLeast(0)\n"
    "            } else {\n"
    "                binding.loadingProgressIndicator.isIndeterminate = true\n"
    "            }\n"
    "        }\n"
    "        emulationViewModel.totalShaders.collect(viewLifecycleOwner) {\n"
    "            if (it > 0) {\n"
    "                binding.loadingProgressIndicator.max = it\n"
    "            } else {\n"
    "                binding.loadingProgressIndicator.isIndeterminate = true\n"
    "            }\n"
    "        }\n",
)])
# The Kotlin code above no longer uses the LoadCallbackStage enum, so R8 removed it, but the
# native library looks the class up when it loads (id_cache.cpp: FindClass of
# DiskShaderCacheProgress$LoadCallbackStage): builds 173-176 aborted at start with
# ClassNotFoundException. Keep it.
rules = root / "src/android/app/proguard-rules.pro"
keep = ("\n# " + MARK + ": looked up by the native library (id_cache.cpp)\n"
        "-keep class io.github.zortos293.lichii.disk_shader_cache.DiskShaderCacheProgress$LoadCallbackStage { *; }\n")
text = rules.read_text()
if "DiskShaderCacheProgress$LoadCallbackStage" not in text:
    rules.write_text(text.rstrip("\n") + "\n" + keep)
    print("proguard-rules.pro: LoadCallbackStage kept")
print("shader loading progress installed")
