#!/usr/bin/env bash
# Builds dav1d and FFmpeg for Android arm64 into $1 (default: $GITHUB_WORKSPACE/ffmpeg-android).
# Lichii's bundled Android FFmpeg (crueter-ci) has no AV1 decoder, so Switch 2 movies (AV1, e.g.
# Ocarina of Time's intro) never decode. Same configure line as that package plus libdav1d.
set -euo pipefail
OUT="${1:-$GITHUB_WORKSPACE/ffmpeg-android}"
NDK="${ANDROID_NDK_DIR:?set ANDROID_NDK_DIR}"
TC="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
API=24
W="$(mktemp -d)"
CC="$TC/bin/aarch64-linux-android${API}-clang"
CXX="$TC/bin/aarch64-linux-android${API}-clang++"
mkdir -p "$OUT"

sudo apt-get install -y -qq meson ninja-build pkg-config >/dev/null

git clone -q --depth 1 --branch 1.5.1 https://code.videolan.org/videolan/dav1d.git "$W/dav1d" ||
    git clone -q --depth 1 --branch 1.5.1 https://github.com/videolan/dav1d.git "$W/dav1d"
cat > "$W/cross.txt" <<CROSS
[binaries]
c = '$CC'
cpp = '$CXX'
ar = '$TC/bin/llvm-ar'
strip = '$TC/bin/llvm-strip'
[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8-a'
endian = 'little'
CROSS
meson setup "$W/dav1d-build" "$W/dav1d" --cross-file "$W/cross.txt" --default-library=static \
    --buildtype=release -Denable_tools=false -Denable_tests=false -Denable_examples=false \
    --prefix="$OUT" -Dlibdir=lib
ninja -C "$W/dav1d-build" install

git clone -q --depth 1 --branch n9.0.2 https://github.com/FFmpeg/FFmpeg.git "$W/ffmpeg" ||
    git clone -q --depth 1 --branch release/9.0 https://github.com/FFmpeg/FFmpeg.git "$W/ffmpeg"
cd "$W/ffmpeg"
PKG_CONFIG_PATH="$OUT/lib/pkgconfig" PKG_CONFIG_LIBDIR="$OUT/lib/pkgconfig" ./configure \
    --disable-avdevice --disable-avformat --disable-doc --disable-everything --disable-ffmpeg \
    --disable-ffprobe --disable-network --enable-static --disable-shared --enable-filter='yadif,scale' \
    --enable-small --enable-pic --enable-swresample --enable-decoder=h264 --enable-decoder=vp8 \
    --enable-decoder=vp9 --enable-decoder=opus --enable-mediacodec --enable-jni \
    --enable-decoder=h264_mediacodec --enable-decoder=vp8_mediacodec --enable-decoder=vp9_mediacodec \
    --enable-libdav1d --enable-decoder=libdav1d \
    --pkg-config=pkg-config --pkg-config-flags=--static \
    --prefix="$OUT" --enable-cross-compile --target-os=android --arch=aarch64 --toolchain=llvm \
    --cross-prefix="$TC/bin/" --cc="$CC" --cxx="$CXX" \
    --extra-cflags="-I$OUT/include" \
    --extra-ldflags="-L$OUT/lib -Wl,-z,max-page-size=16384,--hash-style=both" \
    || { tail -n 60 ffbuild/config.log; exit 1; }
make -j"$(nproc)" >/dev/null
make install >/dev/null
grep -q "CONFIG_LIBDAV1D_DECODER 1" config_components.h
echo "libdav1d decoder enabled"

cat > "$OUT/CMakeLists.txt" <<'CM'
cmake_minimum_required(VERSION 3.10)
# Eden NX2 Android: FFmpeg with libdav1d (AV1), same targets as crueter-ci's package.
set(FFmpeg_PATH "${CMAKE_CURRENT_LIST_DIR}" PARENT_SCOPE)
set(FFMPEG_INCLUDE_DIR ${CMAKE_CURRENT_LIST_DIR}/include)
set(FFMPEG_LIB_DIR ${CMAKE_CURRENT_LIST_DIR}/lib)
add_library(ffmpeg INTERFACE)
set_target_properties(ffmpeg PROPERTIES INTERFACE_INCLUDE_DIRECTORIES ${FFMPEG_INCLUDE_DIR})
foreach (LIB swscale avcodec swresample avfilter avutil)
    add_library(${LIB} STATIC IMPORTED GLOBAL)
    add_library(FFmpeg::${LIB} ALIAS ${LIB})
    target_link_libraries(ffmpeg INTERFACE FFmpeg::${LIB})
    set_target_properties(${LIB} PROPERTIES
        INTERFACE_INCLUDE_DIRECTORIES ${FFMPEG_INCLUDE_DIR}
        IMPORTED_LOCATION ${FFMPEG_LIB_DIR}/lib${LIB}.a)
endforeach()
add_library(nx2_dav1d STATIC IMPORTED GLOBAL)
set_target_properties(nx2_dav1d PROPERTIES IMPORTED_LOCATION ${FFMPEG_LIB_DIR}/libdav1d.a)
target_link_libraries(ffmpeg INTERFACE nx2_dav1d)
find_library(MEDIANDK_LIBRARY mediandk REQUIRED)
target_link_libraries(ffmpeg INTERFACE ${MEDIANDK_LIBRARY})
add_library(FFmpeg::FFmpeg ALIAS ffmpeg)
set(FFmpeg_LIBRARY_DIR "${FFMPEG_LIB_DIR}" PARENT_SCOPE)
set(FFmpeg_INCLUDE_DIR "${FFMPEG_INCLUDE_DIR}" PARENT_SCOPE)
CM
ls -l "$OUT/lib"
