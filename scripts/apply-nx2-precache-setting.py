#!/usr/bin/env python3
"""Shader precache and "build all cached shaders" as settings (global and per game).

Build 171 made the precache a switch in nx2_switches.txt. It is now a setting in Graphics
(below Disk shader cache), and like the other renderer settings it can be set per game:
  Precache shaders: Off / On   (Lichii's precache_shaders: Off / Always)
  Build all cached shaders     (build_all_cached_shaders)
Precache reads the Switch 2 (NVN2) shader archives of the game files; Switch 1 games have none
it can convert, so it only helps Switch 2 games. EDEN_NX2_PRECACHE=1 / =0 and
EDEN_NX2_BUILD_ALL_CACHED=1 still force them for a test.
Usage: python3 scripts/apply-nx2-precache-setting.py <upstream-root>
"""
from pathlib import Path
import sys

app = Path(sys.argv[1]) / "src/android/app/src/main"
java = app / "java/io/github/zortos293/lichii"
model = java / "features/settings/model"
MARK = "NX2 precache setting"


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


patch(model / "IntSetting.kt", [(
    "    RENDERER_ANTI_ALIASING(\"anti_aliasing\"),\n",
    "    RENDERER_ANTI_ALIASING(\"anti_aliasing\"),\n"
    "    RENDERER_PRECACHE_SHADERS(\"precache_shaders\"), // " + MARK + "\n",
)])
patch(model / "BooleanSetting.kt", [(
    "    RENDERER_USE_DISK_SHADER_CACHE(\"use_disk_shader_cache\"),\n",
    "    RENDERER_USE_DISK_SHADER_CACHE(\"use_disk_shader_cache\"),\n"
    "    RENDERER_BUILD_ALL_CACHED_SHADERS(\"build_all_cached_shaders\"), // " + MARK + "\n",
)])
patch(model / "view/SettingsItem.kt", [(
    "            put(\n"
    "                SwitchSetting(\n"
    "                    BooleanSetting.RENDERER_USE_DISK_SHADER_CACHE,\n"
    "                    titleId = R.string.use_disk_shader_cache,\n"
    "                    descriptionId = R.string.use_disk_shader_cache_description\n"
    "                )\n"
    "            )\n",
    "            put(\n"
    "                SwitchSetting(\n"
    "                    BooleanSetting.RENDERER_USE_DISK_SHADER_CACHE,\n"
    "                    titleId = R.string.use_disk_shader_cache,\n"
    "                    descriptionId = R.string.use_disk_shader_cache_description\n"
    "                )\n"
    "            )\n"
    "            // " + MARK + "\n"
    "            put(\n"
    "                SingleChoiceSetting(\n"
    "                    IntSetting.RENDERER_PRECACHE_SHADERS,\n"
    "                    titleId = R.string.nx2_precache_shaders,\n"
    "                    descriptionId = R.string.nx2_precache_shaders_description,\n"
    "                    choicesId = R.array.nx2PrecacheShadersNames,\n"
    "                    valuesId = R.array.nx2PrecacheShadersValues\n"
    "                )\n"
    "            )\n"
    "            put(\n"
    "                SwitchSetting(\n"
    "                    BooleanSetting.RENDERER_BUILD_ALL_CACHED_SHADERS,\n"
    "                    titleId = R.string.nx2_build_all_cached_shaders,\n"
    "                    descriptionId = R.string.nx2_build_all_cached_shaders_description\n"
    "                )\n"
    "            )\n",
)])
patch(java / "features/settings/ui/SettingsFragmentPresenter.kt", [(
    "            add(BooleanSetting.RENDERER_USE_DISK_SHADER_CACHE.key)\n",
    "            add(BooleanSetting.RENDERER_USE_DISK_SHADER_CACHE.key)\n"
    "            add(IntSetting.RENDERER_PRECACHE_SHADERS.key) // " + MARK + "\n"
    "            add(BooleanSetting.RENDERER_BUILD_ALL_CACHED_SHADERS.key)\n",
)])
patch(app / "res/values/strings.xml", [(
    "</resources>",
    "    <!-- " + MARK + " -->\n"
    "    <string name=\"nx2_precache_shaders\">Precache shaders (Switch 2 games)</string>\n"
    "    <string name=\"nx2_precache_shaders_description\">Builds every compute shader in the "
    "game files before the game starts. The first start (and the first after a driver or app "
    "update) takes a few minutes; play then stutters less. Switch 1 games have no shaders it "
    "can read.</string>\n"
    "    <string name=\"nx2_precache_off\">Off</string>\n"
    "    <string name=\"nx2_precache_on\">On</string>\n"
    "    <string name=\"nx2_build_all_cached_shaders\">Build all cached shaders</string>\n"
    "    <string name=\"nx2_build_all_cached_shaders_description\">Builds every shader earlier "
    "sessions recorded while the game loads, not only those the last sessions used. Longer "
    "loading and more memory, fewer stutters in places seen before. Works for any game.</string>\n"
    "</resources>",
)])
patch(app / "res/values/arrays.xml", [(
    "</resources>",
    "    <!-- " + MARK + ": Lichii's PrecacheShaders Off (0) and Always (2) -->\n"
    "    <string-array name=\"nx2PrecacheShadersNames\">\n"
    "        <item>@string/nx2_precache_off</item>\n"
    "        <item>@string/nx2_precache_on</item>\n"
    "    </string-array>\n"
    "\n"
    "    <integer-array name=\"nx2PrecacheShadersValues\">\n"
    "        <item>0</item>\n"
    "        <item>2</item>\n"
    "    </integer-array>\n"
    "</resources>",
)])

# Native: the precache file is built for the setting as for the switch; =0 turns both off.
patch(app / "jni/native.cpp", [
    (
        "    if (Nx2SwitchOn(\"EDEN_NX2_BUILD_ALL_CACHED\")) {\n",
        "    // " + MARK + ": the settings decide; the switches still force them on (=1) or off (=0).\n"
        "    const auto nx2_switch_off = [](const char* name) {\n"
        "        const char* const value{std::getenv(name)};\n"
        "        return value != nullptr && value[0] == '0';\n"
        "    };\n"
        "    if (nx2_switch_off(\"EDEN_NX2_BUILD_ALL_CACHED\")) {\n"
        "        Settings::values.build_all_cached_shaders.SetValue(false);\n"
        "    }\n"
        "    if (nx2_switch_off(\"EDEN_NX2_PRECACHE\")) {\n"
        "        Settings::values.precache_shaders.SetValue(Settings::PrecacheShaders::Off);\n"
        "    }\n"
        "    if (Nx2SwitchOn(\"EDEN_NX2_BUILD_ALL_CACHED\")) {\n",
    ),
    (
        "    if (!Nx2SwitchOn(\"EDEN_NX2_PRECACHE\") || title_id == 0) {\n"
        "        return;\n"
        "    }\n",
        "    const bool nx2_precache_setting{Settings::values.precache_shaders.GetValue() !=\n"
        "                                    Settings::PrecacheShaders::Off};\n"
        "    if ((!Nx2SwitchOn(\"EDEN_NX2_PRECACHE\") && !nx2_precache_setting) || title_id == 0) {\n"
        "        return;\n"
        "    }\n",
    ),
])
print("precache settings installed")
