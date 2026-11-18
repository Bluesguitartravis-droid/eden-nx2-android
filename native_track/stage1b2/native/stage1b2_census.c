// SPDX-License-Identifier: GPL-3.0-or-later
// Stage 1B.2: read-only census of every executable module in a Switch 2 ExeFS.
// No decryption, keys, firmware, modification, or guest execution.

#include <jni.h>
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <lz4.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct {
    char* p;
    size_t left;
} Out;

typedef struct {
    uint64_t svc;
    uint64_t tpidr_r;
    uint64_t tpidr_w;
    uint64_t tpidrro_r;
    uint64_t cntfrq;
    uint64_t cntpct;
    uint64_t cntvct;
    uint64_t ctr;
    uint64_t sp_align;
    uint32_t svc_counts[256];
    uint64_t svc_other;
} Census;

static void add(Out* o, const char* fmt, ...) {
    if (o->left == 0) return;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(o->p, o->left, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    size_t used = (size_t)n;
    if (used >= o->left) {
        o->p += o->left - 1;
        o->left = 1;
    } else {
        o->p += used;
        o->left -= used;
    }
}

static uint32_t u32le(const uint8_t* p) {
    return (uint32_t)p[0]
        | ((uint32_t)p[1] << 8)
        | ((uint32_t)p[2] << 16)
        | ((uint32_t)p[3] << 24);
}

static uint64_t u64le(const uint8_t* p) {
    return (uint64_t)u32le(p) | ((uint64_t)u32le(p + 4) << 32);
}

static int read_exact(int fd, void* dst, size_t n, off_t off) {
    uint8_t* p = (uint8_t*)dst;
    size_t got = 0;
    while (got < n) {
        ssize_t r = pread(fd, p + got, n - got, off + (off_t)got);
        if (r <= 0) return 0;
        got += (size_t)r;
    }
    return 1;
}

static int64_t actual_size(int fd, int64_t hinted) {
    struct stat st;
    if (fstat(fd, &st) == 0 && st.st_size > 0) return (int64_t)st.st_size;
    return hinted;
}

static void hex16(Out* o, const uint8_t* p) {
    for (int i = 0; i < 16; ++i) add(o, "%02x", p[i]);
}

static int is_switch2_npdm(int fd, int64_t size, uint64_t* title_id) {
    uint8_t h[0x80];
    if (!read_exact(fd, h, sizeof(h), 0) || memcmp(h, "META", 4) != 0) return 0;

    uint32_t aci_off = u32le(h + 0x70);
    uint32_t acid_off = u32le(h + 0x78);
    uint8_t magic[4];

    int sw2 = 0;
    if ((uint64_t)acid_off + 0x304 <= (uint64_t)size
            && read_exact(fd, magic, sizeof(magic), (off_t)acid_off + 0x300)
            && memcmp(magic, "ACID", 4) == 0) {
        sw2 = 1;
    }

    if ((uint64_t)aci_off + 0x18 <= (uint64_t)size) {
        uint8_t aci[0x18];
        if (read_exact(fd, aci, sizeof(aci), (off_t)aci_off)
                && memcmp(aci, "ACI0", 4) == 0) {
            *title_id = u64le(aci + 0x10);
        }
    }
    return sw2;
}

static int decode_segment(Out* o, int fd, int64_t file_size,
                          uint8_t* image, const char* module, const char* seg,
                          uint32_t file_off, uint32_t mem_off, uint32_t mem_size,
                          uint32_t stored_size, int compressed) {
    size_t in_size = compressed ? stored_size : mem_size;
    if ((uint64_t)file_off + in_size > (uint64_t)file_size) {
        add(o, "  FAIL %s.%s: stored bytes exceed file\n", module, seg);
        return 0;
    }

    if (!compressed) {
        if (!read_exact(fd, image + mem_off, mem_size, file_off)) {
            add(o, "  FAIL %s.%s: read failed\n", module, seg);
            return 0;
        }
        return 1;
    }

    if (in_size > INT_MAX || mem_size > INT_MAX) {
        add(o, "  FAIL %s.%s: segment too large for LZ4 API\n", module, seg);
        return 0;
    }

    uint8_t* packed = (uint8_t*)malloc(in_size);
    if (!packed) {
        add(o, "  FAIL %s.%s: input allocation failed\n", module, seg);
        return 0;
    }
    if (!read_exact(fd, packed, in_size, file_off)) {
        free(packed);
        add(o, "  FAIL %s.%s: read failed\n", module, seg);
        return 0;
    }

    int decoded = LZ4_decompress_safe(
        (const char*)packed, (char*)image + mem_off,
        (int)in_size, (int)mem_size);
    free(packed);

    if (decoded != (int)mem_size) {
        add(o, "  FAIL %s.%s: LZ4=%d expected=%u\n",
            module, seg, decoded, mem_size);
        return 0;
    }
    return 1;
}

static void census_text(const uint8_t* text, uint32_t text_size, Census* c) {
    uint64_t words = text_size / 4;
    for (uint64_t i = 0; i < words; ++i) {
        uint32_t w = u32le(text + i * 4);

        if ((w & 0xFFE0001F) == 0xD4000001) {
            unsigned imm = (w >> 5) & 0xffff;
            c->svc++;
            if (imm < 256) c->svc_counts[imm]++;
            else c->svc_other++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BD040) {
            c->tpidr_r++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD51BD040) {
            c->tpidr_w++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BD060) {
            c->tpidrro_r++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE000) {
            c->cntfrq++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE020) {
            c->cntpct++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE040) {
            c->cntvct++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53B0020) {
            c->ctr++;
            continue;
        }
        if ((w & 0xFFE0FFFF) == 0xCB2063FF && ((w >> 16) & 31) != 18) {
            c->sp_align++;
        }
    }
}

static void add_census(Census* dst, const Census* src) {
    dst->svc += src->svc;
    dst->tpidr_r += src->tpidr_r;
    dst->tpidr_w += src->tpidr_w;
    dst->tpidrro_r += src->tpidrro_r;
    dst->cntfrq += src->cntfrq;
    dst->cntpct += src->cntpct;
    dst->cntvct += src->cntvct;
    dst->ctr += src->ctr;
    dst->sp_align += src->sp_align;
    dst->svc_other += src->svc_other;
    for (int i = 0; i < 256; ++i) dst->svc_counts[i] += src->svc_counts[i];
}

static void print_census(Out* o, const Census* c, const char* prefix) {
    add(o,
        "%s svc=%" PRIu64
        " tpidr_r=%" PRIu64
        " tpidr_w=%" PRIu64
        " tpidrro_r=%" PRIu64
        " cntfrq=%" PRIu64
        " cntpct=%" PRIu64
        " cntvct=%" PRIu64
        " ctr=%" PRIu64
        " sp_align=%" PRIu64 "\n",
        prefix,
        c->svc, c->tpidr_r, c->tpidr_w, c->tpidrro_r,
        c->cntfrq, c->cntpct, c->cntvct, c->ctr, c->sp_align);
}

static int analyze_module(Out* o, int fd, int64_t hinted_size,
                          const char* name, Census* out_census) {
    int64_t file_size = actual_size(fd, hinted_size);
    uint8_t h[0x100];
    if (file_size < (int64_t)sizeof(h)
            || !read_exact(fd, h, sizeof(h), 0)
            || memcmp(h, "NSO0", 4) != 0) {
        add(o, "\n[%s]\n  SKIP not a readable NSO0\n", name);
        return 0;
    }

    uint32_t flags = u32le(h + 0x0c);
    uint32_t bss = u32le(h + 0x3c);
    uint32_t csz[3] = {u32le(h + 0x60), u32le(h + 0x64), u32le(h + 0x68)};
    uint32_t fo[3], mo[3], sz[3];
    uint64_t image_end = 0;

    for (int i = 0; i < 3; ++i) {
        size_t b = 0x10 + (size_t)i * 0x10;
        fo[i] = u32le(h + b);
        mo[i] = u32le(h + b + 4);
        sz[i] = u32le(h + b + 8);
        uint64_t e = (uint64_t)mo[i] + sz[i];
        if (e > image_end) image_end = e;
    }

    add(o, "\n[%s]\n", name);
    add(o, "  file_size=%" PRId64 " flags=0x%08x build_id=", file_size, flags);
    hex16(o, h + 0x40);
    add(o, "\n");
    add(o, "  text=0x%x ro=0x%x data=0x%x bss=0x%x image=0x%" PRIx64 "\n",
        sz[0], sz[1], sz[2], bss, image_end + bss);

    if (flags & 0x80) {
        add(o, "  BLOCKED compression=ZBIC-or-newer-layout (bit7 set)\n");
        return 0;
    }

    if (image_end == 0 || image_end > (768ULL << 20)) {
        add(o, "  BLOCKED unreasonable image size\n");
        return 0;
    }

    uint8_t* image = (uint8_t*)calloc(1, (size_t)image_end);
    if (!image) {
        add(o, "  BLOCKED image allocation failed\n");
        return 0;
    }

    const char* sn[3] = {"text", "ro", "data"};
    int ok = 1;
    for (int i = 0; i < 3; ++i) {
        ok &= decode_segment(
            o, fd, file_size, image, name, sn[i],
            fo[i], mo[i], sz[i], csz[i], (flags >> i) & 1);
    }

    if (!ok) {
        free(image);
        add(o, "  BLOCKED decompression\n");
        return 0;
    }

    if (image_end >= 8) {
        uint32_t mod0 = u32le(image + 4);
        add(o, "  mod0=0x%x %s\n",
            mod0,
            ((uint64_t)mod0 + 4 <= image_end
             && memcmp(image + mod0, "MOD0", 4) == 0)
                ? "PASS" : "WARN");
    }

    Census c = {0};
    census_text(image + mo[0], sz[0], &c);
    print_census(o, &c, "  census");
    *out_census = c;
    free(image);
    return 1;
}

JNIEXPORT jstring JNICALL
Java_org_nx2a_stage1b2_MainActivity_analyzeModules(
        JNIEnv* env, jobject thiz,
        jintArray fds_arr, jlongArray sizes_arr, jobjectArray names_arr,
        jint npdm_fd, jlong npdm_size) {
    (void)thiz;

    char* report = (char*)calloc(1, 65536);
    if (!report) return (*env)->NewStringUTF(env, "Stage 1B.2 report allocation failed");
    Out o = {report, 65536};

    uint64_t title_id = 0;
    int64_t npdm_real_size = actual_size(npdm_fd, (int64_t)npdm_size);
    int sw2 = is_switch2_npdm(npdm_fd, npdm_real_size, &title_id);

    add(&o, "NX2 Switch 2 Native Stage 1B.2 Full ExeFS Census\n");
    add(&o, "guest_execution=no\n");
    add(&o, "game_modification=no\n");
    add(&o, "switch2_npdm=%s\n", sw2 ? "PASS" : "FAIL");
    add(&o, "title_id=0x%016" PRIx64 "\n", title_id);

    if (!sw2) {
        add(&o, "BLOCKED=NPDM is not recognized as Switch 2\n");
        jstring s = (*env)->NewStringUTF(env, report);
        free(report);
        return s;
    }

    jsize n = (*env)->GetArrayLength(env, fds_arr);
    if ((*env)->GetArrayLength(env, sizes_arr) != n
            || (*env)->GetArrayLength(env, names_arr) != n) {
        add(&o, "BLOCKED=JNI module array length mismatch\n");
        jstring s = (*env)->NewStringUTF(env, report);
        free(report);
        return s;
    }

    jint* fds = (*env)->GetIntArrayElements(env, fds_arr, NULL);
    jlong* sizes = (*env)->GetLongArrayElements(env, sizes_arr, NULL);
    Census total = {0};
    int good = 0;

    add(&o, "module_count=%d\n", (int)n);

    for (jsize i = 0; i < n; ++i) {
        jstring jname = (jstring)(*env)->GetObjectArrayElement(env, names_arr, i);
        const char* name = (*env)->GetStringUTFChars(env, jname, NULL);

        Census c = {0};
        if (analyze_module(&o, fds[i], (int64_t)sizes[i], name, &c)) {
            add_census(&total, &c);
            good++;
        }

        (*env)->ReleaseStringUTFChars(env, jname, name);
        (*env)->DeleteLocalRef(env, jname);
    }

    (*env)->ReleaseIntArrayElements(env, fds_arr, fds, JNI_ABORT);
    (*env)->ReleaseLongArrayElements(env, sizes_arr, sizes, JNI_ABORT);

    add(&o, "\n== totals ==\n");
    add(&o, "modules_scanned=%d/%d\n", good, (int)n);
    print_census(&o, &total, "total");
    add(&o, "total_counter_sites=%" PRIu64 "\n",
        total.cntfrq + total.cntpct + total.cntvct);
    add(&o, "switch2_guest_counter_hz=31250000\n");
    add(&o, "android_host_counter_hz=19200000\n");

    add(&o, "svc_numbers:");
    int any = 0;
    for (int i = 0; i < 256; ++i) {
        if (total.svc_counts[i]) {
            add(&o, " 0x%02x=%u", i, total.svc_counts[i]);
            any = 1;
        }
    }
    if (total.svc_other) add(&o, " >=0x100=%" PRIu64, total.svc_other);
    if (!any && !total.svc_other) add(&o, " none");
    add(&o, "\n");

    if (good == n) {
        add(&o, "\nSTAGE1B2_RESULT=PASS\n");
        add(&o, "NEXT=Build the complete-module Android rewrite and Stage 1C headless guest-entry harness.\n");
    } else {
        add(&o, "\nSTAGE1B2_RESULT=PARTIAL\n");
        add(&o, "NEXT=Resolve blocked module format before guest execution.\n");
    }

    jstring s = (*env)->NewStringUTF(env, report);
    free(report);
    return s;
}
