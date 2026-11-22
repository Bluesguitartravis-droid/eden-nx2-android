#!/usr/bin/env python3
"""Ocarina FSR: say why a frame has no scene camera, and let FSR take a padded scene.

On the Odin (build 207, EDEN_NX2_DLSS_REPLACEMENT=fsr, 1x) FSR replaced the upscale indoors, but
in Hyrule Field at night every frame fell back to the game's 12 ms upscale with "no scene camera
this frame": TrackOcarinaCamera rejected every draw, silently. It now remembers the last reason it
turned a draw down this frame (and the sizes involved), and the fallback line names it.

FSR also accepts a scene drawn up to 15 pixels smaller than its target, as DLSS already does: at
night the game's dynamic resolution drops to 854x480 drawn into an 864x480 target, and FSR
turned every such frame down (build 208, Lon Lon Ranch: with EDEN_NX2_OOT_CAMERA_PADDING=1 FSR
ran 854x480 -> 1920x1080). EDEN_NX2_OOT_CAMERA_PADDING=0 turns that off.

Usage: apply-nx2-oot-camera-why.py <upstream source root>
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
text = path.read_text()
if "NX2 camera why" in text:
    print("vk_rasterizer.cpp: already patched")
    sys.exit(0)

if "oot_camera_refusal" in text:
    # Lichii after 2026-11-20 names the refusal itself ("no scene camera: its size does not match
    # the scene target"); only FSR's padded night scene is left to allow.
    old = """    const auto camera = VideoCommon::DecodeOcarinaCamera(
        values, color.size.width, color.size.height,
        oot_backend == VideoCommon::OcarinaUpscaleBackend::Dlss);"""
    if text.count(old) != 1:
        sys.exit("vk_rasterizer.cpp: DecodeOcarinaCamera call not found")
    text = text.replace(old, """    // NX2 camera why: FSR takes the night's 854x480 scene in its 864x480 target too.
    static const bool nx2_camera_padding = [] {
        const char* value = std::getenv("EDEN_NX2_OOT_CAMERA_PADDING");
        return value == nullptr || value[0] != '0';
    }();
    const auto camera = VideoCommon::DecodeOcarinaCamera(
        values, color.size.width, color.size.height,
        oot_backend == VideoCommon::OcarinaUpscaleBackend::Dlss || nx2_camera_padding);""")
    path.write_text(text)
    print("vk_rasterizer.cpp: FSR accepts a padded scene (upstream names the refusals itself)")
    sys.exit(0)


def replace(old, new):
    global text
    if text.count(old) != 1:
        sys.exit(f"vk_rasterizer.cpp: expected one match for {old[:70]!r}, found {text.count(old)}")
    text = text.replace(old, new)


replace("""void RasterizerVulkan::TrackOcarinaCamera() {
    if (!OcarinaTemporalEnabled()) {
        return;
    }
""", """// NX2 camera why: the last reason TrackOcarinaCamera turned a draw down, for the fallback line.
namespace {
struct Nx2CameraWhy {
    u64 frame = ~u64{0};
    std::string reason;
};
Nx2CameraWhy nx2_camera_why;
void Nx2CameraReject(u64 frame, std::string reason) {
    nx2_camera_why.frame = frame;
    nx2_camera_why.reason = std::move(reason);
}
bool Nx2CameraPadding() {
    static const bool enabled = [] {
        const char* value = std::getenv("EDEN_NX2_OOT_CAMERA_PADDING");
        return value == nullptr || value[0] != '0';
    }();
    return enabled;
}
} // namespace

void RasterizerVulkan::TrackOcarinaCamera() {
    if (!OcarinaTemporalEnabled()) {
        return;
    }
    const u64 why_frame = render_trace_frame;
""")

replace("""    if (!targets.color_buffer_ids[0] || !targets.depth_buffer_id) {
        return;
    }
    const auto& color = texture_cache.GetImageView(targets.color_buffer_ids[0]);
    if (color.size.width < 640 || color.size.height < 360 ||
        color.format != VideoCore::Surface::PixelFormat::B10G11R11_FLOAT) {
        return;
    }""", """    if (!targets.color_buffer_ids[0] || !targets.depth_buffer_id) {
        return;
    }
    const auto& color = texture_cache.GetImageView(targets.color_buffer_ids[0]);
    if (color.size.width < 640 || color.size.height < 360 ||
        color.format != VideoCore::Surface::PixelFormat::B10G11R11_FLOAT) {
        if (color.size.width >= 640 && color.size.height >= 360) {
            Nx2CameraReject(why_frame, fmt::format("{}x{} target of format {}", color.size.width,
                                                   color.size.height,
                                                   static_cast<int>(color.format)));
        }
        return;
    }""")

replace("""        !gpu_memory->IsContinuousRange(binding.address, VideoCommon::OcarinaCameraBytes)) {
        return;
    }
    const auto device_address = gpu_memory->GpuToCpuAddress(binding.address);
    if (!device_address ||
        buffer_cache.IsRegionGpuModified(*device_address, VideoCommon::OcarinaCameraBytes)) {
        return;
    }""", """        !gpu_memory->IsContinuousRange(binding.address, VideoCommon::OcarinaCameraBytes)) {
        Nx2CameraReject(why_frame, fmt::format("{}x{} target: camera buffer 3 {} bytes, enabled {}",
                                               color.size.width, color.size.height, binding.size,
                                               binding.enabled));
        return;
    }
    const auto device_address = gpu_memory->GpuToCpuAddress(binding.address);
    if (!device_address ||
        buffer_cache.IsRegionGpuModified(*device_address, VideoCommon::OcarinaCameraBytes)) {
        Nx2CameraReject(why_frame, fmt::format("{}x{} target: camera buffer {}",
                                               color.size.width, color.size.height,
                                               device_address ? "written by the GPU" : "unmapped"));
        return;
    }""")

replace("""    const auto camera = VideoCommon::DecodeOcarinaCamera(
        values, color.size.width, color.size.height,
        oot_backend == VideoCommon::OcarinaUpscaleBackend::Dlss);
    if (camera.issue != VideoCommon::OcarinaCameraIssue::None) {
        return;
    }""", """    const auto camera = VideoCommon::DecodeOcarinaCamera(
        values, color.size.width, color.size.height,
        oot_backend == VideoCommon::OcarinaUpscaleBackend::Dlss || Nx2CameraPadding());
    if (camera.issue != VideoCommon::OcarinaCameraIssue::None) {
        Nx2CameraReject(why_frame,
                        fmt::format("{}x{} target: camera {} ({}x{}, projection {} {})",
                                    color.size.width, color.size.height,
                                    camera.issue == VideoCommon::OcarinaCameraIssue::Extent
                                        ? "size"
                                        : "projection",
                                    values[0x120 / sizeof(f32)], values[0x124 / sizeof(f32)],
                                    values[0xa8 / sizeof(f32)], values[0xac / sizeof(f32)]));
        return;
    }""")

replace("""    if (oot_camera_sample_frame != render_trace_frame) {
        return fail("no scene camera this frame");
    }""", """    if (oot_camera_sample_frame != render_trace_frame) {
        static std::string why;
        why = nx2_camera_why.frame == render_trace_frame
                  ? "no scene camera this frame: " + nx2_camera_why.reason
                  : std::string{"no scene camera this frame: no scene-sized draw"};
        // fail() logs when the reason pointer changes: keep it the same while the text is.
        static std::string last_why;
        static const char* reason = "no scene camera this frame";
        static u64 last_change = 0;
        if (why != last_why && render_trace_frame >= last_change + 60) {
            last_change = render_trace_frame;
            last_why = why;
            static std::array<std::string, 2> texts;
            static size_t slot = 0;
            slot ^= 1;
            texts[slot] = why;
            reason = texts[slot].c_str();
        }
        return fail(reason);
    }""")

path.write_text(text)
print("vk_rasterizer.cpp: no-camera fallback names its reason; EDEN_NX2_OOT_CAMERA_PADDING")
