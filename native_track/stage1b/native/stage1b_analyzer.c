// SPDX-License-Identifier: GPL-3.0-or-later
// Stage 1B: read-only Switch 2 NSO decompression and ARM64 rewrite census.
// No decryption, keys, firmware, game modification, or guest execution.

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
#include <sys/syscall.h>
#include <unistd.h>

typedef struct {
    char* p;
    size_t left;
} Out;

typedef struct {
    uint64_t count;
    uint64_t first;
    uint64_t last;
} Stat;

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

static void stat_hit(Stat* s, uint64_t off) {
    if (s->count == 0) s->first = off;
    s->last = off;
    s->count++;
}

static void stat_print(Out* o, const char* name, const Stat* s) {
    add(o, "%-22s %" PRIu64, name, s->count);
    if (s->count) {
        add(o, "  first=main+0x%" PRIx64 " last=main+0x%" PRIx64, s->first, s->last);
    }
    add(o, "\n");
}

static int load_segment(Out* o, int fd, int64_t file_size,
                        uint8_t* image, const char* name,
                        uint32_t file_off, uint32_t mem_off, uint32_t mem_size,
                        uint32_t stored_size, int compressed) {
    size_t input_size = compressed ? stored_size : mem_size;
    uint64_t stored_end = (uint64_t)file_off + input_size;

    add(o,
        "segment_%s_layout: file_off=0x%x mem_off=0x%x mem_size=0x%x "
        "stored_size=0x%zx stored_end=0x%" PRIx64 " file_size=0x%" PRIx64 "\n",
        name, file_off, mem_off, mem_size, input_size, stored_end, (uint64_t)file_size);

    if (stored_end > (uint64_t)file_size) {
        add(o,
            "FAIL decompress_%s: stored_end=0x%" PRIx64
            " exceeds file_size=0x%" PRIx64 " by %" PRIu64 " bytes\n",
            name, stored_end, (uint64_t)file_size,
            stored_end - (uint64_t)file_size);
        return 0;
    }

    if (!compressed) {
        if (!read_exact(fd, image + mem_off, mem_size, file_off)) {
            add(o, "FAIL decompress_%s: read failed (%s)\n", name, strerror(errno));
            return 0;
        }
        add(o, "PASS segment_%s: uncompressed %u bytes\n", name, mem_size);
        return 1;
    }

    if (input_size > INT_MAX || mem_size > INT_MAX) {
        add(o, "FAIL decompress_%s: segment too large for LZ4 API\n", name);
        return 0;
    }

    uint8_t* packed = (uint8_t*)malloc(input_size);
    if (!packed) {
        add(o, "FAIL decompress_%s: malloc %zu bytes\n", name, input_size);
        return 0;
    }
    if (!read_exact(fd, packed, input_size, file_off)) {
        add(o, "FAIL decompress_%s: read failed (%s)\n", name, strerror(errno));
        free(packed);
        return 0;
    }

    add(o, "segment_%s_first16=", name);
    size_t preview = input_size < 16 ? input_size : 16;
    for (size_t i = 0; i < preview; ++i) add(o, "%02x", packed[i]);
    add(o, "\n");

    int decoded = LZ4_decompress_safe(
        (const char*)packed,
        (char*)image + mem_off,
        (int)input_size,
        (int)mem_size);
    free(packed);

    if (decoded < 0) {
        add(o, "FAIL decompress_%s: LZ4_decompress_safe returned %d\n", name, decoded);
        return 0;
    }
    if ((uint32_t)decoded != mem_size) {
        add(o, "FAIL decompress_%s: LZ4 output=%d expected=%u\n",
            name, decoded, mem_size);
        return 0;
    }

    add(o, "PASS segment_%s: LZ4 %zu -> %u bytes\n",
        name, input_size, mem_size);
    return 1;
}

static int64_t fd_size(int fd) {
    struct stat st;
    if (fstat(fd, &st) == 0 && st.st_size > 0) return (int64_t)st.st_size;
    off_t cur = lseek(fd, 0, SEEK_CUR);
    off_t end = lseek(fd, 0, SEEK_END);
    if (cur >= 0) lseek(fd, cur, SEEK_SET);
    return end >= 0 ? (int64_t)end : -1;
}

static uint64_t host_cntfrq(void) {
#if defined(__aarch64__)
    uint64_t f = 0;
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(f));
    return f;
#else
    return 0;
#endif
}

static uint64_t read_tpidrro(void) {
#if defined(__aarch64__)
    uint64_t v = 0;
    __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(v));
    return v;
#else
    return 0;
#endif
}

static uint64_t read_tpidr(void) {
#if defined(__aarch64__)
    uint64_t v = 0;
    __asm__ volatile("mrs %0, tpidr_el0" : "=r"(v));
    return v;
#else
    return 0;
#endif
}

static int x18_preserved_across_yields(void) {
#if defined(__aarch64__)
    uint64_t bad = 0;
    uint64_t n = 20000;
    __asm__ volatile(
        "mov x9, x18\n"
        "movz x18, #0x00ef\n"
        "movk x18, #0x00ad, lsl #16\n"
        "movk x18, #0x1234, lsl #32\n"
        "movk x18, #0x5678, lsl #48\n"
        "mov x11, x18\n"
        "mov x10, %[n]\n"
        "mov %[bad], #0\n"
        "1:\n"
        "mov x8, #124\n"
        "svc #0\n"
        "cmp x18, x11\n"
        "cinc %[bad], %[bad], ne\n"
        "mov x18, x11\n"
        "subs x10, x10, #1\n"
        "b.ne 1b\n"
        "mov x18, x9\n"
        : [bad] "=&r"(bad)
        : [n] "r"(n)
        : "x0", "x8", "x9", "x10", "x11", "memory", "cc"
    );
    return bad == 0;
#else
    return 0;
#endif
}

static int switch2_npdm(int fd, int64_t file_size, uint64_t* title_id) {
    uint8_t h[0x80];
    if (!read_exact(fd, h, sizeof(h), 0)) return 0;
    if (memcmp(h, "META", 4) != 0) return 0;

    uint32_t aci_off = u32le(h + 0x70);
    uint32_t acid_off = u32le(h + 0x78);
    uint8_t magic[4];

    int sw2 = 0;
    if ((uint64_t)acid_off + 0x304 <= (uint64_t)file_size
            && read_exact(fd, magic, 4, (off_t)acid_off + 0x300)
            && memcmp(magic, "ACID", 4) == 0) {
        sw2 = 1;
    }

    if ((uint64_t)aci_off + 0x18 <= (uint64_t)file_size) {
        uint8_t aci[0x18];
        if (read_exact(fd, aci, sizeof(aci), (off_t)aci_off)
                && memcmp(aci, "ACI0", 4) == 0) {
            *title_id = u64le(aci + 0x10);
        }
    }
    return sw2;
}

static void scan_text(Out* o, const uint8_t* text, uint32_t text_mem_off,
                      uint32_t text_size) {
    Stat svc = {0}, tpidr_r = {0}, tpidr_w = {0}, tpidrro_r = {0};
    Stat cntfrq = {0}, cntpct = {0}, cntvct = {0}, ctr = {0}, sp_align = {0};
    uint32_t svc_counts[256] = {0};
    uint64_t svc_other = 0;
    uint64_t words = text_size / 4;

    for (uint64_t i = 0; i < words; ++i) {
        uint32_t w = u32le(text + i * 4);
        uint64_t off = (uint64_t)text_mem_off + i * 4;

        if ((w & 0xFFE0001F) == 0xD4000001) {
            unsigned imm = (w >> 5) & 0xffff;
            stat_hit(&svc, off);
            if (imm < 256) svc_counts[imm]++;
            else svc_other++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BD040) {
            stat_hit(&tpidr_r, off);
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD51BD040) {
            stat_hit(&tpidr_w, off);
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BD060) {
            stat_hit(&tpidrro_r, off);
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE000) {
            stat_hit(&cntfrq, off);
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE020) {
            stat_hit(&cntpct, off);
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE040) {
            stat_hit(&cntvct, off);
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53B0020) {
            stat_hit(&ctr, off);
            continue;
        }
        if ((w & 0xFFE0FFFF) == 0xCB2063FF && ((w >> 16) & 31) != 18) {
            stat_hit(&sp_align, off);
            continue;
        }
    }

    add(o, "\n== ARM64 rewrite census ==\n");
    add(o, "scan=text segment linear sweep, exact opcode masks\n");
    add(o, "text_words=%" PRIu64 " text_bytes=%u\n", words, text_size);
    stat_print(o, "svc", &svc);
    stat_print(o, "mrs tpidr_el0", &tpidr_r);
    stat_print(o, "msr tpidr_el0", &tpidr_w);
    stat_print(o, "mrs tpidrro_el0", &tpidrro_r);
    stat_print(o, "mrs cntfrq_el0", &cntfrq);
    stat_print(o, "mrs cntpct_el0", &cntpct);
    stat_print(o, "mrs cntvct_el0", &cntvct);
    stat_print(o, "mrs ctr_el0", &ctr);
    stat_print(o, "sub sp,sp,xN", &sp_align);

    add(o, "\nsvc_numbers:");
    int any = 0;
    for (unsigned i = 0; i < 256; ++i) {
        if (svc_counts[i]) {
            add(o, " 0x%02x=%u", i, svc_counts[i]);
            any = 1;
        }
    }
    if (svc_other) add(o, " >=0x100=%" PRIu64, svc_other);
    if (!any && !svc_other) add(o, " none");
    add(o, "\n");

    uint64_t counter_sites = cntfrq.count + cntpct.count + cntvct.count;
    add(o, "\nSwitch2 timing policy:\n");
    add(o, "  host_cntfrq=%" PRIu64 " Hz\n", host_cntfrq());
    add(o, "  guest_cntfrq=31250000 Hz\n");
    add(o, "  host_to_guest_tick_ratio=625/384\n");
    add(o, "  counter_sites_requiring_virtualization=%" PRIu64 "\n", counter_sites);
    add(o, "  note=Unlike Switch1-on-Android, direct 19.2 MHz counter reads are NOT guest-correct for Switch2.\n");

    add(o, "\nRewrite planning:\n");
    add(o, "  host_gate_sites_minimum=%" PRIu64 "\n",
        svc.count + tpidr_r.count + tpidr_w.count + tpidrro_r.count
        + counter_sites + ctr.count + sp_align.count);
    add(o, "  x18_instruction_census=deferred_to_disassembler-backed rewrite pass\n");
    add(o, "  reason=raw register-field matching would create false positives in normal ARM64 encodings\n");
}

JNIEXPORT jstring JNICALL
Java_org_nx2a_stage1b_MainActivity_analyzeExeFs(
        JNIEnv* env, jobject thiz,
        jint main_fd, jlong main_size,
        jint npdm_fd, jlong npdm_size) {
    (void)thiz;

    char* report = (char*)calloc(1, 32768);
    if (!report) {
        return (*env)->NewStringUTF(env, "Stage 1B: report allocation failed");
    }
    Out o = {report, 32768};

    add(&o, "NX2 Switch 2 Native Stage 1B Rewrite Census\n");
    add(&o, "mode=read-only NSO decompression + ARM64 scan\n");
    add(&o, "guest_execution=no\n");
    add(&o, "game_modification=no\n\n");

    uint64_t title_id = 0;
    int sw2 = switch2_npdm(npdm_fd, (int64_t)npdm_size, &title_id);
    add(&o, "switch2_npdm=%s\n", sw2 ? "PASS" : "FAIL");
    add(&o, "title_id=0x%016" PRIx64 "\n", title_id);
    if (!sw2) {
        add(&o, "BLOCKED=Selected NPDM is not recognized as Switch 2\n");
        jstring out = (*env)->NewStringUTF(env, report);
        free(report);
        return out;
    }

    int64_t os_main_size = fd_size(main_fd);
    int64_t effective_main_size = os_main_size > 0 ? os_main_size : (int64_t)main_size;
    add(&o, "main_size_java=%" PRId64 "\n", (int64_t)main_size);
    add(&o, "main_size_os=%" PRId64 "\n", os_main_size);
    if (os_main_size > 0 && (int64_t)main_size > 0 && os_main_size != (int64_t)main_size) {
        add(&o, "WARN main_size_disagreement=%" PRId64 " bytes\n",
            os_main_size - (int64_t)main_size);
    }

    uint8_t h[0x100];
    if (!read_exact(main_fd, h, sizeof(h), 0) || memcmp(h, "NSO0", 4) != 0) {
        add(&o, "BLOCKED=main is not a readable NSO0\n");
        jstring out = (*env)->NewStringUTF(env, report);
        free(report);
        return out;
    }

    uint32_t flags = u32le(h + 0x0c);
    uint32_t bss = u32le(h + 0x3c);
    uint32_t csz[3] = {
        u32le(h + 0x60), u32le(h + 0x64), u32le(h + 0x68)
    };
    uint32_t fo[3], mo[3], sz[3];
    uint64_t image_end = 0;
    for (int i = 0; i < 3; ++i) {
        size_t b = 0x10 + (size_t)i * 0x10;
        fo[i] = u32le(h + b);
        mo[i] = u32le(h + b + 4);
        sz[i] = u32le(h + b + 8);
        uint64_t end = (uint64_t)mo[i] + sz[i];
        if (end > image_end) image_end = end;
    }

    add(&o, "nso_file_size_effective=%" PRId64 "\n", effective_main_size);
    add(&o, "nso_flags=0x%08x\n", flags);
    add(&o, "nso_header_segments:\n");
    const char* header_names[3] = {"text", "ro", "data"};
    for (int i = 0; i < 3; ++i) {
        size_t stored = ((flags >> i) & 1) ? csz[i] : sz[i];
        add(&o,
            "  %s file_off=0x%x mem_off=0x%x mem_size=0x%x csz=0x%x "
            "stored_end=0x%" PRIx64 "%s\n",
            header_names[i], fo[i], mo[i], sz[i], csz[i],
            (uint64_t)fo[i] + stored,
            ((uint64_t)fo[i] + stored <= (uint64_t)effective_main_size) ? "" : " OUT_OF_FILE");
    }
    add(&o, "image_without_bss=0x%" PRIx64 " (%" PRIu64 " bytes)\n",
        image_end, image_end);
    add(&o, "image_with_bss=0x%" PRIx64 "\n", image_end + bss);
    add(&o, "host_x18_preserved_20000_yields=%s\n",
        x18_preserved_across_yields() ? "PASS" : "FAIL");
    add(&o, "host_tpidr_el0=0x%016" PRIx64 "\n", read_tpidr());
    add(&o, "host_tpidrro_el0=0x%016" PRIx64 "\n", read_tpidrro());

    if (image_end == 0 || image_end > (512ULL << 20)) {
        add(&o, "BLOCKED=Unreasonable NSO image size\n");
        jstring out = (*env)->NewStringUTF(env, report);
        free(report);
        return out;
    }

    uint8_t* image = (uint8_t*)calloc(1, (size_t)image_end);
    if (!image) {
        add(&o, "BLOCKED=Could not allocate %" PRIu64 " bytes for decompressed NSO\n",
            image_end);
        jstring out = (*env)->NewStringUTF(env, report);
        free(report);
        return out;
    }

    const char* names[3] = {"text", "ro", "data"};
    int ok = 1;
    for (int i = 0; i < 3; ++i) {
        ok &= load_segment(&o, main_fd, effective_main_size, image, names[i],
                           fo[i], mo[i], sz[i], csz[i], (flags >> i) & 1);
    }
    if (!ok) {
        add(&o, "BLOCKED=One or more NSO segments did not decompress exactly\n");
        free(image);
        jstring out = (*env)->NewStringUTF(env, report);
        free(report);
        return out;
    }

    add(&o, "PASS nso_decompression=all 3 segments exact\n");

    if (image_end >= 8) {
        uint32_t mod0_off = u32le(image + 4);
        add(&o, "mod0_offset=0x%x\n", mod0_off);
        if ((uint64_t)mod0_off + 4 <= image_end
                && memcmp(image + mod0_off, "MOD0", 4) == 0) {
            add(&o, "PASS mod0_magic=MOD0\n");
        } else {
            add(&o, "WARN mod0_magic=not found at header-declared offset\n");
        }
    }

    scan_text(&o, image + mo[0], mo[0], sz[0]);

    add(&o, "\nSTAGE1B_RESULT=PASS\n");
    add(&o, "NEXT=Use this census to build the first Switch2-aware ARM64 rewrite + headless guest-entry harness.\n");
    add(&o, "IMPORTANT=Stage 1C will still start with graphics disabled and stop at/around the first unsupported host service.\n");

    free(image);
    jstring out = (*env)->NewStringUTF(env, report);
    free(report);
    return out;
}
