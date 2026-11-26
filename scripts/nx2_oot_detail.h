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
//
// Build 276 adds, read at each game boot too:
// EDEN_NX2_OOT_PARAM  edits of any of the game's BYML parameter files, rows separated by ';':
//   <RomFS path, its last component may be a glob>|<key path>|<edit>
//   pack:<pack path>:<member glob>|<key path>|<edit>
//   key path: components separated by '.', each a glob over a dictionary's keys or an array index;
//   edit: x<factor> (scale a number), =true, =false, =<number>. In place, sizes kept. A value the game's
//   writer stores once for several paths changes for all of them.
// EDEN_NX2_OOT_DUMP   (277) comma-separated file specs as in EDEN_NX2_OOT_PARAM: every value logged (NX2_DUMP).
// EDEN_NX2_OOT_LIST   comma-separated substrings (case-insensitive): NX2_LIST log lines for the RomFS files and
//   the members of the top-level Pack/*.pack.zs packs whose paths contain one; "packs" also lists every pack.
#pragma once

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <fnmatch.h>

#include <zstd.h>

#include "common/common_types.h"
#include "common/logging.h"
#include "core/file_sys/vfs/vfs.h"
#include "core/file_sys/vfs/vfs_vector.h"
#include "video_core/nx2_live_switch.h"
#include "video_core/nx2_foliage_addons.h"

namespace FileSys::Nx2OotDetail {

constexpr u64 kOcarina = 0x0400486024DEC000ULL;

inline std::vector<std::string> WantedKeys(u64 title_id) {
    if (title_id != kOcarina) {
        return {};
    }
    std::string v = VideoCommon::Nx2LiveSwitch("EDEN_NX2_OOT_DETAIL");
    if (v.empty() && Nx2FoliageAddons::Enabled(title_id, Nx2FoliageAddons::kLighter)) {
        LOG_INFO(Loader, "NX2_DETAIL: {} is checked in Add-Ons", Nx2FoliageAddons::kLighter);
        v = "lowest";
    }
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

// ---- EDEN_NX2_OOT_PARAM / EDEN_NX2_OOT_LIST ----

enum class EditKind { Scale, SetBool, SetNum };

struct ParamRow {
    bool pack = false;
    std::string path;    // RomFS path (loose) or the pack's path
    std::string member;  // member glob (pack rows)
    std::string key;     // key path
    std::string text;    // the row as given, for the log
    EditKind kind = EditKind::Scale;
    double num = 0;
    bool b = false;
    int files = 0;
    int values = 0;
};

inline std::vector<std::string> Split(const std::string& s, char c) {
    std::vector<std::string> out;
    size_t at = 0;
    while (true) {
        const size_t e = s.find(c, at);
        std::string part = s.substr(at, e == std::string::npos ? std::string::npos : e - at);
        while (!part.empty() && part.front() == ' ') {
            part.erase(part.begin());
        }
        while (!part.empty() && part.back() == ' ') {
            part.pop_back();
        }
        out.push_back(std::move(part));
        if (e == std::string::npos) {
            return out;
        }
        at = e + 1;
    }
}

inline bool ParseEdit(const std::string& s, ParamRow& q) {
    char* end = nullptr;
    if (s.size() > 1 && s[0] == 'x') {
        const double f = std::strtod(s.c_str() + 1, &end);
        if (*end != 0 || !std::isfinite(f) || f <= 0) {
            return false;
        }
        q.kind = EditKind::Scale;
        q.num = f;
        return true;
    }
    if (s == "=true" || s == "=false") {
        q.kind = EditKind::SetBool;
        q.b = s == "=true";
        return true;
    }
    if (s.size() > 1 && s[0] == '=') {
        const double v = std::strtod(s.c_str() + 1, &end);
        if (*end != 0 || !std::isfinite(v)) {
            return false;
        }
        q.kind = EditKind::SetNum;
        q.num = v;
        return true;
    }
    return false;
}

inline std::vector<ParamRow> WantedParams(u64 title_id) {
    if (title_id != kOcarina) {
        return {};
    }
    std::vector<ParamRow> rows;
    for (const auto& row : Split(VideoCommon::Nx2LiveSwitch("EDEN_NX2_OOT_PARAM"), ';')) {
        if (row.empty()) {
            continue;
        }
        const auto f = Split(row, '|');
        ParamRow q;
        q.text = row;
        bool ok = f.size() == 3 && !f[0].empty() && !f[1].empty() && ParseEdit(f[2], q);
        if (ok && f[0].rfind("pack:", 0) == 0) {
            const size_t c = f[0].find(':', 5);
            ok = c != std::string::npos && c > 5 && c + 1 < f[0].size();
            if (ok) {
                q.pack = true;
                q.path = f[0].substr(5, c - 5);
                q.member = f[0].substr(c + 1);
            }
        } else if (ok) {
            q.path = f[0];
        }
        if (!ok) {
            LOG_WARNING(Loader, "NX2_PARAM row \"{}\" left out: <file>|<key path>|<x<factor> | =true | =false | =<number>>", row);
            continue;
        }
        q.key = f[1];
        rows.push_back(std::move(q));
    }
    return rows;
}

inline std::vector<std::string> WantedList(u64 title_id) {
    if (title_id != kOcarina) {
        return {};
    }
    std::vector<std::string> out;
    for (auto s : Split(VideoCommon::Nx2LiveSwitch("EDEN_NX2_OOT_LIST"), ',')) {
        if (!s.empty()) {
            std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
            out.push_back(std::move(s));
        }
    }
    return out;
}

// EDEN_NX2_OOT_DUMP: file specs separated by ',' (a RomFS path, its last component may be a glob, or
// pack:<pack path>:<member glob>); every value of each matching BYML file is logged as NX2_DUMP lines.
inline std::vector<std::string> WantedDump(u64 title_id) {
    if (title_id != kOcarina) {
        return {};
    }
    std::vector<std::string> out;
    for (auto& s : Split(VideoCommon::Nx2LiveSwitch("EDEN_NX2_OOT_DUMP"), ',')) {
        if (!s.empty()) {
            out.push_back(std::move(s));
        }
    }
    return out;
}

struct Request {
    std::vector<std::string> keys;
    std::vector<ParamRow> params;
    std::vector<std::string> list;
    std::vector<std::string> dump;
    bool Any() const {
        return !keys.empty() || !params.empty() || !list.empty() || !dump.empty();
    }
};

inline Request Wanted(u64 title_id) {
    return Request{WantedKeys(title_id), WantedParams(title_id), WantedList(title_id), WantedDump(title_id)};
}

// ---- BYML, generic edit ----

struct Byml {
    u8* b = nullptr;
    size_t n = 0;
    bool be = false;
    u32 kt = 0;
    u32 nkeys = 0;
    u32 root = 0;
};

inline bool BymlOpen(Byml& y, u8* b, size_t n, std::string& why) {
    y = {};
    y.b = b;
    y.n = n;
    if (n < 16 || !((b[0] == 'Y' && b[1] == 'B') || (b[0] == 'B' && b[1] == 'Y'))) {
        why = "not a BYML file";
        return false;
    }
    y.be = b[0] == 'B';
    const u32 ver = Rd(b, 2, 2, y.be);
    if (ver < 2 || ver > 10) {
        why = "BYML version " + std::to_string(ver);
        return false;
    }
    y.kt = Rd(b, 4, 4, y.be);
    y.root = Rd(b, 12, 4, y.be);
    if (y.kt != 0) {
        if (y.kt > n - 8 || b[y.kt] != 0xC2) {
            why = "BYML key table";
            return false;
        }
        y.nkeys = Rd(b, y.kt + 1, 3, y.be);
        if (static_cast<u64>(y.kt) + 4 + 4ULL * (y.nkeys + 1) > n) {
            why = "BYML key table runs past the file";
            return false;
        }
    }
    if (y.root != 0 && y.root > n - 4) {
        why = "BYML root past the file";
        return false;
    }
    return true;
}

inline const char* BymlKey(const Byml& y, u32 i) {
    if (i >= y.nkeys) {
        return nullptr;
    }
    const u64 o = static_cast<u64>(y.kt) + Rd(y.b, y.kt + 4 + 4 * i, 4, y.be);
    if (o >= y.n || std::memchr(y.b + o, 0, y.n - static_cast<size_t>(o)) == nullptr) {
        return nullptr;
    }
    return reinterpret_cast<const char*>(y.b + o);
}

inline void Wr(Byml& y, size_t o, u64 v, int w) {
    for (int i = 0; i < w; i++) {
        y.b[o + i] = static_cast<u8>(v >> (8 * (y.be ? w - 1 - i : i)));
    }
}

// One value (type t, slot = its 4-byte slot) edited; false with why when the edit does not fit its type
inline bool BymlApply(Byml& y, int t, size_t slot, const ParamRow& q, const std::string& path, std::string& why) {
    size_t at = slot;
    int w = 4;
    if (t == 0xD4 || t == 0xD5 || t == 0xD6) {
        at = Rd(y.b, slot, 4, y.be);
        w = 8;
        if (y.n < 8 || at > y.n - 8) {
            why = path + ": its 64-bit value is past the file";
            return false;
        }
    }
    u64 raw = 0;
    for (int i = 0; i < w; i++) {
        raw |= static_cast<u64>(y.b[at + i]) << (8 * (y.be ? w - 1 - i : i));
    }
    if (t == 0xD0) {
        if (q.kind != EditKind::SetBool || raw > 1) {
            why = path + " is a boolean: =true or =false";
            return false;
        }
        Wr(y, at, q.b ? 1 : 0, 4);
        return true;
    }
    if (t != 0xD1 && t != 0xD2 && t != 0xD3 && t != 0xD4 && t != 0xD5 && t != 0xD6) {
        why = path + " is not a number or a boolean (type " + std::to_string(t) + ")";
        return false;
    }
    if (q.kind == EditKind::SetBool) {
        why = path + " is a number: x<factor> or =<number>";
        return false;
    }
    double old;
    if (t == 0xD2) {
        const u32 u = static_cast<u32>(raw);
        float f;
        std::memcpy(&f, &u, 4);
        old = f;
    } else if (t == 0xD6) {
        double d;
        std::memcpy(&d, &raw, 8);
        old = d;
    } else if (t == 0xD1) {
        old = static_cast<s32>(static_cast<u32>(raw));
    } else if (t == 0xD4) {
        old = static_cast<double>(static_cast<s64>(raw));
    } else {
        old = static_cast<double>(raw);
    }
    double v = q.kind == EditKind::Scale ? old * q.num : q.num;
    if (t == 0xD2) {
        const float f = static_cast<float>(v);
        u32 u;
        std::memcpy(&u, &f, 4);
        Wr(y, at, u, 4);
    } else if (t == 0xD6) {
        u64 u;
        std::memcpy(&u, &v, 8);
        Wr(y, at, u, 8);
    } else {
        v = std::round(v);
        if ((t == 0xD3 || t == 0xD5) && v < 0) {
            v = 0;
        }
        const u64 u = (t == 0xD1 || t == 0xD4) ? static_cast<u64>(static_cast<s64>(v)) : static_cast<u64>(v);
        Wr(y, at, u, w);
    }
    return true;
}

// The values under the node at o reached by components [ci, ...) edited once each (done holds the slots already
// edited by this row in this file). false with why on a malformed file or an edit that does not fit.
inline bool BymlWalk(Byml& y, u32 o, const std::vector<std::string>& comps, size_t ci, const ParamRow& q,
                     const std::string& path, int depth, std::set<size_t>& done, int& found, std::string& why) {
    if (depth > 32 || y.n < 4 || o > y.n - 4) {
        why = (path.empty() ? std::string{"the root"} : path) + ": too deep or past the file";
        return false;
    }
    const int t = y.b[o];
    if (t != 0xC0 && t != 0xC1) {
        return true;  // a value where the path goes on: not this path
    }
    const u32 cnt = Rd(y.b, o + 1, 3, y.be);
    const u64 vals = t == 0xC1 ? static_cast<u64>(o) + 4 : static_cast<u64>(o) + 4 + ((cnt + 3) & ~3U);
    const u64 step = t == 0xC1 ? 8 : 4;
    if (vals + step * cnt > y.n) {
        why = path + ": a container past the file";
        return false;
    }
    const bool last = ci + 1 == comps.size();
    for (u32 i = 0; i < cnt; i++) {
        std::string name;
        int et;
        size_t slot;
        if (t == 0xC1) {
            const size_t e = static_cast<size_t>(vals) + 8 * static_cast<size_t>(i);
            const char* k = BymlKey(y, Rd(y.b, e, 3, y.be));
            if (k == nullptr) {
                why = path + ": a key index outside the key table";
                return false;
            }
            name = k;
            et = y.b[e + 3];
            slot = e + 4;
        } else {
            name = std::to_string(i);
            et = y.b[o + 4 + i];
            slot = static_cast<size_t>(vals) + 4 * static_cast<size_t>(i);
        }
        if (fnmatch(comps[ci].c_str(), name.c_str(), 0) != 0) {
            continue;
        }
        const std::string p = path.empty() ? name : path + "." + name;
        const bool container = et == 0xC0 || et == 0xC1;
        if (!last) {
            if (container &&
                !BymlWalk(y, Rd(y.b, slot, 4, y.be), comps, ci + 1, q, p, depth + 1, done, found, why)) {
                return false;
            }
            continue;
        }
        if (container) {
            why = p + " is a container, not a value";
            return false;
        }
        const size_t id = (et == 0xD4 || et == 0xD5 || et == 0xD6) ? (size_t{1} << 40) | Rd(y.b, slot, 4, y.be) : slot;
        found++;
        if (!done.insert(id).second) {
            continue;
        }
        if (!BymlApply(y, et, slot, q, p, why)) {
            return false;
        }
    }
    return true;
}

// Returns the values the row's key path reaches in this file (edited in place), -1 with why
inline int BymlEdit(std::vector<u8>& file, const ParamRow& q, std::string& why) {
    Byml y;
    if (!BymlOpen(y, file.data(), file.size(), why)) {
        return -1;
    }
    if (y.root == 0) {
        return 0;
    }
    std::set<size_t> done;
    int found = 0;
    if (!BymlWalk(y, y.root, Split(q.key, '.'), 0, q, "", 0, done, found, why)) {
        return -1;
    }
    return found;
}


// Every value of a BYML file as "<key path> = <value>" lines (at most max_lines)
inline void BymlDumpNode(const Byml& y, u32 o, const std::string& path, int depth, std::vector<std::string>& out,
                         size_t max_lines, u32 st, u32 nstr) {
    if (out.size() >= max_lines || depth > 24 || y.n < 4 || o > y.n - 4) {
        return;
    }
    const int t = y.b[o];
    if (t != 0xC0 && t != 0xC1) {
        return;
    }
    const u32 cnt = Rd(y.b, o + 1, 3, y.be);
    const u64 vals = t == 0xC1 ? static_cast<u64>(o) + 4 : static_cast<u64>(o) + 4 + ((cnt + 3) & ~3U);
    if (vals + (t == 0xC1 ? 8ULL : 4ULL) * cnt > y.n) {
        out.push_back(path + " = <container past the file>");
        return;
    }
    for (u32 i = 0; i < cnt && out.size() < max_lines; i++) {
        std::string name;
        int et;
        size_t slot;
        if (t == 0xC1) {
            const size_t e = static_cast<size_t>(vals) + 8 * static_cast<size_t>(i);
            const char* k = BymlKey(y, Rd(y.b, e, 3, y.be));
            name = k != nullptr ? k : "?";
            et = y.b[e + 3];
            slot = e + 4;
        } else {
            name = std::to_string(i);
            et = y.b[o + 4 + i];
            slot = static_cast<size_t>(vals) + 4 * static_cast<size_t>(i);
        }
        const std::string p = path.empty() ? name : path + "." + name;
        const u32 raw = Rd(y.b, slot, 4, y.be);
        switch (et) {
        case 0xC0:
        case 0xC1:
            BymlDumpNode(y, raw, p, depth + 1, out, max_lines, st, nstr);
            break;
        case 0xA0: {
            std::string v = "?";
            if (st != 0 && raw < nstr) {
                const u64 so = static_cast<u64>(st) + Rd(y.b, st + 4 + 4 * raw, 4, y.be);
                if (so < y.n && std::memchr(y.b + so, 0, y.n - static_cast<size_t>(so)) != nullptr) {
                    v = reinterpret_cast<const char*>(y.b + so);
                }
            }
            out.push_back(p + " = \"" + v + "\"");
            break;
        }
        case 0xD0:
            out.push_back(p + " = " + (raw ? "true" : "false"));
            break;
        case 0xD1:
            out.push_back(p + " = " + std::to_string(static_cast<s32>(raw)) + " (int)");
            break;
        case 0xD3:
            out.push_back(p + " = " + std::to_string(raw) + " (uint)");
            break;
        case 0xD2: {
            float f;
            std::memcpy(&f, &raw, 4);
            out.push_back(p + " = " + std::to_string(f) + " (float)");
            break;
        }
        case 0xD4:
        case 0xD5:
        case 0xD6: {
            if (y.n < 8 || raw > y.n - 8) {
                out.push_back(p + " = <64-bit past the file>");
                break;
            }
            u64 v = 0;
            for (int b = 0; b < 8; b++) {
                v |= static_cast<u64>(y.b[raw + b]) << (8 * (y.be ? 7 - b : b));
            }
            if (et == 0xD6) {
                double d;
                std::memcpy(&d, &v, 8);
                out.push_back(p + " = " + std::to_string(d) + " (double)");
            } else {
                out.push_back(p + " = " + std::to_string(et == 0xD4 ? static_cast<s64>(v) : static_cast<s64>(v)) +
                              (et == 0xD4 ? " (int64)" : " (uint64)"));
            }
            break;
        }
        case 0xFF:
            out.push_back(p + " = null");
            break;
        default:
            out.push_back(p + " = <type " + std::to_string(et) + ">");
            break;
        }
    }
}

inline void BymlDump(const std::string& name, std::vector<u8> file) {
    Byml y;
    std::string why;
    if (!BymlOpen(y, file.data(), file.size(), why)) {
        LOG_INFO(Loader, "NX2_DUMP {}: {}", name, why);
        return;
    }
    u32 st = Rd(y.b, 8, 4, y.be), nstr = 0;
    if (st != 0 && (st > y.n - 8 || y.b[st] != 0xC2)) {
        st = 0;
    }
    if (st != 0) {
        nstr = Rd(y.b, st + 1, 3, y.be);
        if (static_cast<u64>(st) + 4 + 4ULL * (nstr + 1) > y.n) {
            st = 0;
            nstr = 0;
        }
    }
    std::vector<std::string> out;
    if (y.root != 0) {
        BymlDumpNode(y, y.root, "", 0, out, 400, st, nstr);
    }
    LOG_INFO(Loader, "NX2_DUMP {} ({} bytes, {} values)", name, file.size(), out.size());
    for (const auto& line : out) {
        LOG_INFO(Loader, "NX2_DUMP   {}", line);
    }
}

// ---- The overlay ----

inline bool LoadDicts(const VirtualDir& romfs, std::vector<std::pair<u32, std::vector<u8>>>& dicts, std::string& why) {
    const VirtualDir pack_dir = romfs->GetSubdirectory("Pack");
    const auto zs = pack_dir != nullptr ? ReadAll(pack_dir->GetFile("ZsDic.pack.zs")) : std::vector<u8>{};
    if (zs.empty()) {
        why = "Pack/ZsDic.pack.zs missing";
        return false;
    }
    const auto sarc = Decode(zs, {}, why);
    std::vector<Member> ms;
    if (sarc.empty() || !ParseSarc(sarc, ms, why)) {
        why = "Pack/ZsDic.pack.zs: " + why;
        return false;
    }
    for (const auto& m : ms) {
        if (m.name.size() < 6 || m.name.compare(m.name.size() - 6, 6, ".zsdic") != 0) {
            continue;
        }
        std::vector<u8> d(sarc.begin() + m.start, sarc.begin() + m.end);
        const u32 id = static_cast<u32>(ZSTD_getDictID_fromDict(d.data(), d.size()));
        dicts.emplace_back(id, std::move(d));
    }
    return true;
}

struct PackEdit {
    bool ok = false;
    ZHead head;
    size_t src_size = 0;
    std::vector<u8> sarc;
    std::vector<Member> ms;
    bool changed = false;
};

struct Node {
    std::map<std::string, Node> dirs;
    std::map<std::string, std::vector<u8>> files;
};

inline VirtualDir MakeDir(Node& node, const std::string& name) {
    std::vector<VirtualFile> files;
    std::vector<VirtualDir> dirs;
    for (auto& [n, bytes] : node.files) {
        files.push_back(std::make_shared<VectorVfsFile>(std::move(bytes), n));
    }
    for (auto& [n, child] : node.dirs) {
        dirs.push_back(MakeDir(child, n));
    }
    return std::make_shared<VectorVfsDirectory>(std::move(files), std::move(dirs), name);
}

inline void ListFiles(const VirtualDir& romfs, const std::vector<std::string>& subs) {
    const auto matches = [&](std::string s) {
        std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
        for (const auto& sub : subs) {
            if (s.find(sub) != std::string::npos) {
                return true;
            }
        }
        return false;
    };
    const bool all_packs = std::find(subs.begin(), subs.end(), "packs") != subs.end();
    int lines = 0, total = 0;
    std::vector<std::pair<std::string, VirtualDir>> stack{{"", romfs}};
    while (!stack.empty()) {
        auto [prefix, dir] = std::move(stack.back());
        stack.pop_back();
        for (const auto& f : dir->GetFiles()) {
            const std::string p = prefix + f->GetName();
            total++;
            if (matches(p) && lines < 600) {
                LOG_INFO(Loader, "NX2_LIST file {} ({} bytes)", p, f->GetSize());
                lines++;
            }
        }
        for (const auto& d : dir->GetSubdirectories()) {
            if (prefix.empty()) {
                LOG_INFO(Loader, "NX2_LIST top folder {}/ ({} files directly in it)", d->GetName(), d->GetFiles().size());
            }
            stack.emplace_back(prefix + d->GetName() + "/", d);
        }
    }
    LOG_INFO(Loader, "NX2_LIST {} RomFS files, {} listed", total, lines);
    std::vector<std::pair<u32, std::vector<u8>>> dicts;
    std::string why;
    if (!LoadDicts(romfs, dicts, why)) {
        LOG_WARNING(Loader, "NX2_LIST packs not read: {}", why);
        return;
    }
    const VirtualDir pack_dir = romfs->GetSubdirectory("Pack");
    int member_lines = 0;
    for (const auto& f : pack_dir->GetFiles()) {
        const std::string name = f->GetName();
        if (name.size() < 8 || name.compare(name.size() - 8, 8, ".pack.zs") != 0) {
            continue;
        }
        if (all_packs) {
            LOG_INFO(Loader, "NX2_LIST pack Pack/{} ({} bytes)", name, f->GetSize());
        }
        if (f->GetSize() > (64U << 20)) {
            continue;
        }
        std::string pwhy;
        const auto sarc = Decode(ReadAll(f), dicts, pwhy);
        std::vector<Member> ms;
        if (sarc.empty() || !ParseSarc(sarc, ms, pwhy)) {
            LOG_INFO(Loader, "NX2_LIST pack Pack/{} not read: {}", name, pwhy);
            continue;
        }
        for (const auto& m : ms) {
            if (matches(m.name) && member_lines < 1500) {
                LOG_INFO(Loader, "NX2_LIST member pack:Pack/{}:{} ({} bytes)", name, m.name, m.end - m.start);
                member_lines++;
            }
        }
    }
    LOG_INFO(Loader, "NX2_LIST {} pack members listed", member_lines);
}

// The RomFS layer with every requested edit, or nullptr (the game's own files)
inline VirtualDir BuildOverlay(const VirtualDir& romfs, Request req) {
    if (romfs == nullptr || !req.Any()) {
        return nullptr;
    }
    if (!req.list.empty()) {
        ListFiles(romfs, req.list);
    }
    if (req.keys.empty() && req.params.empty() && req.dump.empty()) {
        return nullptr;
    }
    std::string why;
    std::vector<std::pair<u32, std::vector<u8>>> dicts;
    bool dicts_loaded = false, dicts_ok = false;
    std::map<std::string, PackEdit> packs;
    const auto get_pack = [&](const std::string& path) -> PackEdit* {
        auto it = packs.find(path);
        if (it != packs.end()) {
            return it->second.ok ? &it->second : nullptr;
        }
        PackEdit& pe = packs[path];
        if (!dicts_loaded) {
            dicts_loaded = true;
            dicts_ok = LoadDicts(romfs, dicts, why);
            if (!dicts_ok) {
                LOG_ERROR(Loader, "NX2_DETAIL {}", why);
            }
        }
        if (!dicts_ok) {
            return nullptr;
        }
        const auto src = ReadAll(romfs->GetFileRelative(path));
        std::string pwhy;
        if (src.empty() || !ZstdHead(src.data(), src.size(), pe.head, pwhy)) {
            LOG_ERROR(Loader, "NX2_DETAIL {}: {}", path, src.empty() ? "missing" : pwhy);
            return nullptr;
        }
        pe.src_size = src.size();
        pe.sarc = Decode(src, dicts, pwhy);
        if (pe.sarc.empty() || !ParseSarc(pe.sarc, pe.ms, pwhy)) {
            LOG_ERROR(Loader, "NX2_DETAIL {}: {}", path, pwhy);
            return nullptr;
        }
        pe.ok = true;
        return &pe;
    };

    // The dumps (EDEN_NX2_OOT_DUMP), of the game's own files
    for (const auto& spec : req.dump) {
        if (spec.rfind("pack:", 0) == 0) {
            const size_t c = spec.find(':', 5);
            if (c == std::string::npos) {
                LOG_WARNING(Loader, "NX2_DUMP {}: pack:<pack path>:<member glob>", spec);
                continue;
            }
            const std::string pack = spec.substr(5, c - 5), glob = spec.substr(c + 1);
            if (PackEdit* pe = get_pack(pack)) {
                int n = 0;
                for (const auto& m : pe->ms) {
                    if (n < 20 && fnmatch(glob.c_str(), m.name.c_str(), 0) == 0) {
                        BymlDump("pack:" + pack + ":" + m.name,
                                 std::vector<u8>(pe->sarc.begin() + m.start, pe->sarc.begin() + m.end));
                        n++;
                    }
                }
                LOG_INFO(Loader, "NX2_DUMP {}: {} members dumped (at most 20)", spec, n);
            }
        } else {
            const size_t sl = spec.rfind('/');
            const std::string dir_path = sl == std::string::npos ? "" : spec.substr(0, sl);
            const std::string glob = sl == std::string::npos ? spec : spec.substr(sl + 1);
            const VirtualDir dir = dir_path.empty() ? romfs : romfs->GetDirectoryRelative(dir_path);
            int n = 0;
            if (dir != nullptr) {
                for (const auto& f : dir->GetFiles()) {
                    if (n < 20 && fnmatch(glob.c_str(), f->GetName().c_str(), 0) == 0) {
                        BymlDump(dir_path + "/" + f->GetName(), f->ReadAllBytes());
                        n++;
                    }
                }
            }
            LOG_INFO(Loader, "NX2_DUMP {}: {} files dumped (at most 20)", spec, n);
        }
    }
    if (req.keys.empty() && req.params.empty()) {
        return nullptr;
    }

    // The foliage layers (EDEN_NX2_OOT_DETAIL)
    if (!req.keys.empty()) {
        if (PackEdit* pe = get_pack("Pack/Foliage.pack.zs")) {
            static constexpr std::string_view prefix = "Foliage/ResFoliageScene/";
            static constexpr std::string_view suffix = ".game__foliage__ResFoliageScene.bgyml";
            int scenes = 0, edited = 0, values = 0, bad = 0;
            for (const auto& m : pe->ms) {
                if (m.name.size() < prefix.size() + suffix.size() || m.name.rfind(prefix, 0) != 0 ||
                    m.name.compare(m.name.size() - suffix.size(), suffix.size(), suffix) != 0) {
                    continue;
                }
                scenes++;
                std::vector<u8> copy(pe->sarc.begin() + m.start, pe->sarc.begin() + m.end);
                std::string bwhy;
                const int c = ClearBools(copy.data(), copy.size(), req.keys, bwhy);
                if (c < 0) {
                    bad++;
                    LOG_WARNING(Loader, "NX2_DETAIL {}: {}; the scene keeps its own values", m.name, bwhy);
                    continue;
                }
                if (c > 0) {
                    std::memcpy(pe->sarc.data() + m.start, copy.data(), copy.size());
                    edited++;
                    values += c;
                    pe->changed = true;
                }
            }
            std::string list;
            for (const auto& k : req.keys) {
                list += (list.empty() ? "" : ",") + k;
            }
            LOG_INFO(Loader, "NX2_DETAIL Ocarina foliage layers off ({}): {} of {} scene files edited, {} values, {} skipped",
                     list, edited, scenes, values, bad);
        }
    }

    // The parameter rows (EDEN_NX2_OOT_PARAM)
    Node tree;
    std::map<std::string, std::vector<u8>> loose;
    for (auto& q : req.params) {
        std::string ewhy;
        if (q.pack) {
            PackEdit* pe = get_pack(q.path);
            if (pe == nullptr) {
                LOG_WARNING(Loader, "NX2_PARAM \"{}\": pack {} not read", q.text, q.path);
                continue;
            }
            for (const auto& m : pe->ms) {
                if (fnmatch(q.member.c_str(), m.name.c_str(), 0) != 0) {
                    continue;
                }
                q.files++;
                std::vector<u8> copy(pe->sarc.begin() + m.start, pe->sarc.begin() + m.end);
                const int c = BymlEdit(copy, q, ewhy);
                if (c < 0) {
                    LOG_WARNING(Loader, "NX2_PARAM \"{}\" {}: {}; the member keeps its own values", q.text, m.name, ewhy);
                    continue;
                }
                if (c > 0) {
                    std::memcpy(pe->sarc.data() + m.start, copy.data(), copy.size());
                    q.values += c;
                    pe->changed = true;
                }
            }
        } else {
            const size_t sl = q.path.rfind('/');
            const std::string dir_path = sl == std::string::npos ? "" : q.path.substr(0, sl);
            const std::string glob = sl == std::string::npos ? q.path : q.path.substr(sl + 1);
            const VirtualDir dir = dir_path.empty() ? romfs : romfs->GetDirectoryRelative(dir_path);
            if (dir == nullptr) {
                LOG_WARNING(Loader, "NX2_PARAM \"{}\": no folder {}", q.text, dir_path);
                continue;
            }
            for (const auto& f : dir->GetFiles()) {
                if (fnmatch(glob.c_str(), f->GetName().c_str(), 0) != 0) {
                    continue;
                }
                const std::string p = dir_path.empty() ? f->GetName() : dir_path + "/" + f->GetName();
                q.files++;
                auto it = loose.find(p);
                std::vector<u8> copy = it != loose.end() ? it->second : f->ReadAllBytes();
                const int c = BymlEdit(copy, q, ewhy);
                if (c < 0) {
                    LOG_WARNING(Loader, "NX2_PARAM \"{}\" {}: {}; the file keeps its own values", q.text, p, ewhy);
                    continue;
                }
                if (c > 0) {
                    loose[p] = std::move(copy);
                    q.values += c;
                }
            }
        }
        LOG_INFO(Loader, "NX2_PARAM \"{}\": {} files matched, {} values edited", q.text, q.files, q.values);
    }

    // Packs written back as raw-block zstd frames with the source header, checked by decoding them again
    int written = 0;
    for (auto& [path, pe] : packs) {
        if (!pe.ok || !pe.changed) {
            continue;
        }
        auto out = ZstdRaw(pe.head, pe.sarc);
        std::string vwhy;
        if (out.empty() || Decode(out, dicts, vwhy) != pe.sarc) {
            LOG_ERROR(Loader, "NX2_DETAIL {}: the written frame does not decode back ({}); the game keeps its own", path, vwhy);
            continue;
        }
        LOG_INFO(Loader, "NX2_DETAIL {} {} -> {} bytes (raw zstd blocks, dictionary {})", path, pe.src_size, out.size(),
                 pe.head.dictid);
        loose[path] = std::move(out);
    }
    for (auto& [path, bytes] : loose) {
        Node* node = &tree;
        const auto parts = Split(path, '/');
        for (size_t i = 0; i + 1 < parts.size(); i++) {
            node = &node->dirs[parts[i]];
        }
        node->files[parts.back()] = std::move(bytes);
        written++;
    }
    if (written == 0) {
        LOG_WARNING(Loader, "NX2_DETAIL nothing edited; Ocarina keeps its own files");
        return nullptr;
    }
    return MakeDir(tree, "");
}

} // namespace FileSys::Nx2OotDetail
