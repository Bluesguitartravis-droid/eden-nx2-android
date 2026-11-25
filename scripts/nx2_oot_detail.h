// SPDX-License-Identifier: GPL-3.0-or-later
// EDEN_NX2_OOT_DETAIL (read at each game boot): Ocarina of Time draws less ground detail by turning off
// the game's own foliage layers in its scene files. The SARC/BYML/zstd-frame handling is ported from
// Anka Native's dm/host/detail.h (MIT, Copyright (c) 2026 seedds, EddizEge, Swap2-Native contributors;
// https://github.com/EddizEge/AnkaNative), where it was measured on an Adreno 830: all four layers off took
// a Kokiri balcony from 78.8 to 55 ms of GPU a frame. The game's Pack/Foliage.pack.zs (one zstd frame with
// dictionary 4 of Pack/ZsDic.pack.zs) is decoded, each Foliage/ResFoliageScene member's top-level
// IsEnable<layer> booleans are set false in place (no size changes), and the SARC is written back as one
// zstd frame of raw blocks with the source frame's header (same content size and dictionary ID), handed to
// the game through a RomFS layer of its own. Values:
//   reduced  IsEnableMoss, IsEnableStone
//   lowest   reduced + IsEnableUndergrowth4, IsEnableUndergrowth8
//   ug       IsEnableUndergrowth4, IsEnableUndergrowth8
//   IsEnable<Name>[,IsEnable<Name>...]  those keys (measurement)
// empty / full / 0: the game's own files. The grass (IsEnableTurf) is never touched.
#pragma once

#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include <zstd.h>

#include "common/common_types.h"
#include "common/logging.h"
#include "core/file_sys/vfs/vfs.h"
#include "core/file_sys/vfs/vfs_vector.h"
#include "video_core/nx2_live_switch.h"

namespace FileSys::Nx2OotDetail {

constexpr u64 kOcarina = 0x0400486024DEC000ULL;

inline std::vector<std::string> WantedKeys(u64 title_id) {
    if (title_id != kOcarina) {
        return {};
    }
    const std::string v = VideoCommon::Nx2LiveSwitch("EDEN_NX2_OOT_DETAIL");
    if (v.empty() || v == "full" || v == "0") {
        return {};
    }
    if (v == "reduced") {
        return {"IsEnableMoss", "IsEnableStone"};
    }
    if (v == "lowest") {
        return {"IsEnableMoss", "IsEnableStone", "IsEnableUndergrowth4", "IsEnableUndergrowth8"};
    }
    if (v == "ug") {
        return {"IsEnableUndergrowth4", "IsEnableUndergrowth8"};
    }
    std::vector<std::string> keys;
    size_t at = 0;
    while (at <= v.size()) {
        const size_t comma = v.find(',', at);
        std::string k = v.substr(at, comma == std::string::npos ? std::string::npos : comma - at);
        if (k.rfind("IsEnable", 0) == 0 && k != "IsEnableTurf") {
            keys.push_back(std::move(k));
        } else if (!k.empty()) {
            LOG_WARNING(Loader, "NX2_DETAIL ignores {} (only IsEnable<layer> keys, never IsEnableTurf)", k);
        }
        if (comma == std::string::npos) {
            break;
        }
        at = comma + 1;
    }
    return keys;
}

inline u32 Rd(const u8* b, size_t o, int w, bool be) {
    u32 v = 0;
    for (int i = 0; i < w; i++) {
        v |= static_cast<u32>(b[o + i]) << (8 * (be ? w - 1 - i : i));
    }
    return v;
}

struct Member {
    std::string name;
    u32 start;
    u32 end;
};

// The members of a SARC, false with why
inline bool ParseSarc(const std::vector<u8>& d, std::vector<Member>& out, std::string& why) {
    const u8* b = d.data();
    const size_t n = d.size();
    if (n < 0x20 || std::memcmp(b, "SARC", 4) != 0) {
        why = "not a SARC";
        return false;
    }
    const bool be = b[6] == 0xFE && b[7] == 0xFF;
    if (!be && !(b[6] == 0xFF && b[7] == 0xFE)) {
        why = "SARC byte order mark";
        return false;
    }
    const u32 hl = Rd(b, 4, 2, be), size = Rd(b, 8, 4, be), data = Rd(b, 12, 4, be);
    if (hl != 0x14 || size != n || data > n || std::memcmp(b + 0x14, "SFAT", 4) != 0) {
        why = "SARC header";
        return false;
    }
    const u32 cnt = Rd(b, 0x1A, 2, be);
    const u64 nodes = 0x20, names = nodes + 16ULL * cnt;
    if (names + 8 > n || std::memcmp(b + names, "SFNT", 4) != 0) {
        why = "SARC's SFNT is missing";
        return false;
    }
    const u64 pool = names + Rd(b, static_cast<size_t>(names) + 4, 2, be);
    out.clear();
    for (u32 i = 0; i < cnt; i++) {
        const size_t e = static_cast<size_t>(nodes + 16ULL * i);
        const u32 attr = Rd(b, e + 4, 4, be), s = Rd(b, e + 8, 4, be), en = Rd(b, e + 12, 4, be);
        if (s > en || static_cast<u64>(data) + en > n) {
            why = "SARC member runs past the file";
            return false;
        }
        Member m{{}, data + s, data + en};
        if (attr >> 24) {
            const u64 no = pool + 4ULL * (attr & 0xFFFFFF);
            if (no >= n || std::memchr(b + no, 0, n - static_cast<size_t>(no)) == nullptr) {
                why = "SARC member name runs past the file";
                return false;
            }
            m.name = reinterpret_cast<const char*>(b + no);
        }
        out.push_back(std::move(m));
    }
    return true;
}

// Sets the top-level booleans named in keys to false, in place. Returns the values changed (a key the
// file does not have is left to the game's default), -1 with why on a malformed file or a non-boolean.
inline int ClearBools(u8* b, size_t n, const std::vector<std::string>& keys, std::string& why) {
    if (n < 16 || !((b[0] == 'Y' && b[1] == 'B') || (b[0] == 'B' && b[1] == 'Y'))) {
        why = "not a BYML file";
        return -1;
    }
    const bool be = b[0] == 'B';
    const u32 ver = Rd(b, 2, 2, be);
    if (ver < 2 || ver > 10) {
        why = "BYML version " + std::to_string(ver);
        return -1;
    }
    const u32 kt = Rd(b, 4, 4, be), root = Rd(b, 12, 4, be);
    if (root == 0) {
        return 0;
    }
    if (kt == 0 || kt > n - 8 || b[kt] != 0xC2) {
        why = "BYML key table";
        return -1;
    }
    const u32 nkeys = Rd(b, kt + 1, 3, be);
    if (static_cast<u64>(kt) + 4 + 4ULL * (nkeys + 1) > n || root > n - 4 || b[root] != 0xC1) {
        why = "BYML key table or root dictionary";
        return -1;
    }
    const u32 cnt = Rd(b, root + 1, 3, be);
    if (static_cast<u64>(root) + 4 + 8ULL * cnt > n) {
        why = "BYML root dictionary runs past the file";
        return -1;
    }
    int changed = 0;
    for (u32 i = 0; i < cnt; i++) {
        const size_t e = root + 4 + 8 * static_cast<size_t>(i);
        const u32 ki = Rd(b, e, 3, be);
        if (ki >= nkeys) {
            why = "BYML key index outside the key table";
            return -1;
        }
        const u64 ko = static_cast<u64>(kt) + Rd(b, kt + 4 + 4 * ki, 4, be);
        if (ko >= n || std::memchr(b + ko, 0, n - static_cast<size_t>(ko)) == nullptr) {
            why = "BYML key runs past the file";
            return -1;
        }
        const char* key = reinterpret_cast<const char*>(b + ko);
        bool wanted = false;
        for (const auto& k : keys) {
            wanted = wanted || k == key;
        }
        if (!wanted) {
            continue;
        }
        if (b[e + 3] != 0xD0) {
            why = std::string{key} + " is not a boolean";
            return -1;
        }
        const u32 old = Rd(b, e + 4, 4, be);
        if (old > 1) {
            why = std::string{key} + ": boolean word " + std::to_string(old);
            return -1;
        }
        if (old != 0) {
            std::memset(b + e + 4, 0, 4);
            changed++;
        }
    }
    return changed;
}

struct ZHead {
    size_t hsize = 0;
    u64 fcs = 0;
    bool has_fcs = false;
    u32 dictid = 0;
    int did_size = 0;
    bool single = false;
    u64 window = 0;
    u8 fhd = 0;
    u8 wd = 0;
};

inline bool ZstdHead(const u8* b, size_t n, ZHead& h, std::string& why) {
    h = {};
    if (n < 6 || b[0] != 0x28 || b[1] != 0xB5 || b[2] != 0x2F || b[3] != 0xFD) {
        why = "not a zstd frame";
        return false;
    }
    const u8 fhd = b[4];
    h.fhd = fhd;
    const int fcsf = fhd >> 6, dic = fhd & 3;
    h.single = (fhd >> 5) & 1;
    if (fhd & 8) {
        why = "zstd frame header with its reserved bit set";
        return false;
    }
    static constexpr int ds[4] = {0, 1, 2, 4};
    const int fs = fcsf == 0 ? (h.single ? 1 : 0) : fcsf == 1 ? 2 : fcsf == 2 ? 4 : 8;
    size_t o = 5;
    if (!h.single) {
        if (o >= n) {
            why = "zstd frame header cut short";
            return false;
        }
        h.wd = b[o];
        const int ex = b[o] >> 3, m = b[o] & 7;
        const u64 base = 1ULL << (10 + ex);
        h.window = base + base / 8 * m;
        o++;
    }
    if (o + ds[dic] + fs > n) {
        why = "zstd frame header cut short";
        return false;
    }
    h.did_size = ds[dic];
    for (int i = 0; i < ds[dic]; i++) {
        h.dictid |= static_cast<u32>(b[o + i]) << (8 * i);
    }
    o += ds[dic];
    if (fs) {
        h.has_fcs = true;
        for (int i = 0; i < fs; i++) {
            h.fcs |= static_cast<u64>(b[o + i]) << (8 * i);
        }
        if (fs == 2) {
            h.fcs += 256;
        }
        o += fs;
    }
    if (h.single) {
        h.window = h.fcs;
    }
    h.hsize = o;
    return true;
}

// data as one zstd frame of raw blocks with the source frame's header layout (content size, window,
// dictionary ID); the checksum flag is cleared. Empty when the content does not fit the source header.
inline std::vector<u8> ZstdRaw(const ZHead& src, const std::vector<u8>& data) {
    const size_t n = data.size();
    if (!src.has_fcs || src.fcs != n) {
        return {};
    }
    size_t block = src.window < (128U << 10) ? static_cast<size_t>(src.window) : (128U << 10);
    if (block == 0) {
        block = 1;
    }
    std::vector<u8> o;
    o.reserve(18 + n + 3 * (n / block + 1));
    o.insert(o.end(), {0x28, 0xB5, 0x2F, 0xFD});
    u8 fhd = static_cast<u8>(src.fhd & ~0x07);
    if (src.did_size) {
        fhd |= static_cast<u8>(src.did_size == 1 ? 1 : src.did_size == 2 ? 2 : 3);
    }
    o.push_back(fhd);
    if (!src.single) {
        o.push_back(src.wd);
    }
    for (int i = 0; i < src.did_size; i++) {
        o.push_back(static_cast<u8>(src.dictid >> (8 * i)));
    }
    const int fcsf = fhd >> 6, fs = fcsf == 0 ? (src.single ? 1 : 0) : fcsf == 1 ? 2 : fcsf == 2 ? 4 : 8;
    const u64 fv = fs == 2 ? n - 256 : n;
    for (int i = 0; i < fs; i++) {
        o.push_back(static_cast<u8>(fv >> (8 * i)));
    }
    size_t done = 0;
    do {
        const size_t len = n - done < block ? n - done : block;
        const bool last = done + len == n;
        const u32 bh = static_cast<u32>(last) | static_cast<u32>(len) << 3;  // block type 0: raw
        o.push_back(static_cast<u8>(bh));
        o.push_back(static_cast<u8>(bh >> 8));
        o.push_back(static_cast<u8>(bh >> 16));
        o.insert(o.end(), data.begin() + done, data.begin() + done + len);
        done += len;
    } while (done < n);
    return o;
}

inline std::vector<u8> ReadAll(const VirtualFile& f) {
    return f == nullptr ? std::vector<u8>{} : f->ReadAllBytes();
}

// One zstd frame decoded, with the dictionary of the frame's ID when it has one; empty with why
inline std::vector<u8> Decode(const std::vector<u8>& src,
                              const std::vector<std::pair<u32, std::vector<u8>>>& dicts,
                              std::string& why) {
    const unsigned long long size = ZSTD_getFrameContentSize(src.data(), src.size());
    if (size == ZSTD_CONTENTSIZE_UNKNOWN || size == ZSTD_CONTENTSIZE_ERROR || size > (512ULL << 20)) {
        why = "zstd frame without a content size";
        return {};
    }
    const u32 id = ZSTD_getDictID_fromFrame(src.data(), src.size());
    const std::vector<u8>* dict = nullptr;
    for (const auto& [did, bytes] : dicts) {
        if (did == id) {
            dict = &bytes;
        }
    }
    if (id != 0 && dict == nullptr) {
        why = "no zstd dictionary " + std::to_string(id);
        return {};
    }
    std::vector<u8> out(static_cast<size_t>(size));
    ZSTD_DCtx* const dctx = ZSTD_createDCtx();
    const size_t r = dict != nullptr ? ZSTD_decompress_usingDict(dctx, out.data(), out.size(), src.data(),
                                                                  src.size(), dict->data(), dict->size())
                                     : ZSTD_decompressDCtx(dctx, out.data(), out.size(), src.data(), src.size());
    ZSTD_freeDCtx(dctx);
    if (ZSTD_isError(r) || r != out.size()) {
        why = std::string{"zstd: "} + (ZSTD_isError(r) ? ZSTD_getErrorName(r) : "short frame");
        return {};
    }
    return out;
}

// The RomFS layer with the edited Pack/Foliage.pack.zs, or nullptr (the game's own files)
inline VirtualDir BuildOverlay(const VirtualDir& romfs, const std::vector<std::string>& keys) {
    if (romfs == nullptr || keys.empty()) {
        return nullptr;
    }
    std::string why;
    const auto fail = [&](const std::string& what) -> VirtualDir {
        LOG_ERROR(Loader, "NX2_DETAIL {}: {}; Ocarina keeps its own foliage files", what, why);
        return nullptr;
    };
    const VirtualDir pack_dir = romfs->GetSubdirectory("Pack");
    if (pack_dir == nullptr) {
        why = "no Pack folder";
        return fail("RomFS");
    }
    // The dictionaries
    std::vector<std::pair<u32, std::vector<u8>>> dicts;
    {
        const auto zs = ReadAll(pack_dir->GetFile("ZsDic.pack.zs"));
        if (zs.empty()) {
            why = "missing";
            return fail("Pack/ZsDic.pack.zs");
        }
        const auto sarc = Decode(zs, {}, why);
        if (sarc.empty()) {
            return fail("Pack/ZsDic.pack.zs");
        }
        std::vector<Member> ms;
        if (!ParseSarc(sarc, ms, why)) {
            return fail("Pack/ZsDic.pack.zs");
        }
        for (const auto& m : ms) {
            if (m.name.size() < 6 || m.name.compare(m.name.size() - 6, 6, ".zsdic") != 0) {
                continue;
            }
            std::vector<u8> d(sarc.begin() + m.start, sarc.begin() + m.end);
            const u32 id = static_cast<u32>(ZSTD_getDictID_fromDict(d.data(), d.size()));
            dicts.emplace_back(id, std::move(d));
        }
    }
    const auto src = ReadAll(pack_dir->GetFile("Foliage.pack.zs"));
    if (src.empty()) {
        why = "missing";
        return fail("Pack/Foliage.pack.zs");
    }
    ZHead head;
    if (!ZstdHead(src.data(), src.size(), head, why)) {
        return fail("Pack/Foliage.pack.zs");
    }
    auto sarc = Decode(src, dicts, why);
    if (sarc.empty()) {
        return fail("Pack/Foliage.pack.zs");
    }
    std::vector<Member> ms;
    if (!ParseSarc(sarc, ms, why)) {
        return fail("Pack/Foliage.pack.zs");
    }
    static constexpr std::string_view prefix = "Foliage/ResFoliageScene/";
    static constexpr std::string_view suffix = ".game__foliage__ResFoliageScene.bgyml";
    int scenes = 0, edited = 0, values = 0, bad = 0;
    for (const auto& m : ms) {
        if (m.name.size() < prefix.size() + suffix.size() || m.name.rfind(prefix, 0) != 0 ||
            m.name.compare(m.name.size() - suffix.size(), suffix.size(), suffix) != 0) {
            continue;
        }
        scenes++;
        std::vector<u8> copy(sarc.begin() + m.start, sarc.begin() + m.end);
        std::string bwhy;
        const int c = ClearBools(copy.data(), copy.size(), keys, bwhy);
        if (c < 0) {
            bad++;
            LOG_WARNING(Loader, "NX2_DETAIL {}: {}; the scene keeps its own values", m.name, bwhy);
            continue;
        }
        if (c > 0) {
            std::memcpy(sarc.data() + m.start, copy.data(), copy.size());
            edited++;
            values += c;
        }
    }
    if (values == 0) {
        why = std::to_string(scenes) + " scene files, none has the layers asked for";
        return fail("Pack/Foliage.pack.zs");
    }
    auto out = ZstdRaw(head, sarc);
    if (out.empty()) {
        why = "the edited pack does not fit the source frame's header";
        return fail("Pack/Foliage.pack.zs");
    }
    {
        std::string vwhy;
        const auto back = Decode(out, dicts, vwhy);
        if (back != sarc) {
            why = "the written frame does not decode back to the edited pack (" + vwhy + ")";
            return fail("Pack/Foliage.pack.zs");
        }
    }
    std::string list;
    for (const auto& k : keys) {
        list += (list.empty() ? "" : ",") + k;
    }
    LOG_INFO(Loader,
             "NX2_DETAIL Ocarina foliage layers off ({}): {} of {} scene files edited, {} values, {} skipped; "
             "Pack/Foliage.pack.zs {} -> {} bytes (raw zstd blocks, dictionary {})",
             list, edited, scenes, values, bad, src.size(), out.size(), head.dictid);
    auto file = std::make_shared<VectorVfsFile>(std::move(out), "Foliage.pack.zs");
    auto pack = std::make_shared<VectorVfsDirectory>(std::vector<VirtualFile>{file}, std::vector<VirtualDir>{}, "Pack");
    return std::make_shared<VectorVfsDirectory>(std::vector<VirtualFile>{}, std::vector<VirtualDir>{pack}, "");
}

} // namespace FileSys::Nx2OotDetail
