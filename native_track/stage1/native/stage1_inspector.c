// SPDX-License-Identifier: GPL-3.0-or-later
// Stage 1A: read-only Switch 2 ExeFS metadata inspector.
// NPDM Switch 2 ACID detection follows Eglantine/Eden's ProgramMetadata layout.
// No decryption, keys, firmware, or game data leave the device.

#include <jni.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef struct {
    char* p;
    size_t left;
} Out;

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

static void hex(Out* o, const uint8_t* p, size_t n) {
    for (size_t i = 0; i < n; ++i) add(o, "%02x", p[i]);
}

static void inspect_nso(Out* o, int fd, int64_t file_size) {
    uint8_t h[0x100];
    memset(h, 0, sizeof(h));
    add(o, "== main / NSO ==\n");
    add(o, "file_size=%" PRId64 " bytes\n", file_size);

    if (!read_exact(fd, h, sizeof(h), 0)) {
        add(o, "FAIL main_header: could not read 0x100 bytes (%s)\n", strerror(errno));
        return;
    }

    if (memcmp(h, "NSO0", 4) != 0) {
        add(o, "FAIL main_magic: expected NSO0, got %02x%02x%02x%02x\n",
            h[0], h[1], h[2], h[3]);
        return;
    }
    add(o, "PASS main_magic=NSO0\n");

    uint32_t flags = u32le(h + 0x0c);
    uint32_t bss = u32le(h + 0x3c);
    add(o, "flags=0x%08x compressed{text=%s,ro=%s,data=%s}\n",
        flags,
        (flags & 1) ? "yes" : "no",
        (flags & 2) ? "yes" : "no",
        (flags & 4) ? "yes" : "no");
    add(o, "bss_size=0x%x (%u bytes)\n", bss, bss);

    add(o, "build_id_16=");
    hex(o, h + 0x40, 0x10);
    add(o, "\n");
    add(o, "build_id_32=");
    hex(o, h + 0x40, 0x20);
    add(o, "\n");

    const char* names[3] = {"text", "ro", "data"};
    uint64_t image_end = 0;
    for (int i = 0; i < 3; ++i) {
        const size_t b = 0x10 + (size_t)i * 0x10;
        uint32_t file_off = u32le(h + b + 0);
        uint32_t mem_off = u32le(h + b + 4);
        uint32_t mem_size = u32le(h + b + 8);
        uint32_t comp_size = u32le(h + 0x60 + (size_t)i * 4);
        uint64_t end = (uint64_t)mem_off + mem_size;
        if (end > image_end) image_end = end;
        add(o,
            "%s: file_off=0x%x mem_off=0x%x mem_size=0x%x compressed_size=0x%x\n",
            names[i], file_off, mem_off, mem_size, comp_size);
    }

    add(o, "image_end_without_bss=0x%" PRIx64 "\n", image_end);
    add(o, "image_end_with_bss=0x%" PRIx64 "\n", image_end + bss);

    if ((int64_t)u32le(h + 0x10) >= file_size) {
        add(o, "WARN text_file_offset_outside_file\n");
    }
}

static void inspect_npdm(Out* o, int fd, int64_t file_size) {
    uint8_t h[0x80];
    memset(h, 0, sizeof(h));
    add(o, "\n== main.npdm ==\n");
    add(o, "file_size=%" PRId64 " bytes\n", file_size);

    if (!read_exact(fd, h, sizeof(h), 0)) {
        add(o, "FAIL npdm_header: could not read 0x80 bytes (%s)\n", strerror(errno));
        return;
    }

    if (memcmp(h, "META", 4) != 0) {
        add(o, "FAIL npdm_magic: expected META, got %02x%02x%02x%02x\n",
            h[0], h[1], h[2], h[3]);
        return;
    }
    add(o, "PASS npdm_magic=META\n");

    uint8_t flags = h[0x0c];
    uint8_t priority = h[0x0e];
    uint8_t cpu = h[0x0f];
    uint32_t sys_resource = u32le(h + 0x14);
    uint32_t category = u32le(h + 0x18);
    uint32_t stack = u32le(h + 0x1c);
    uint32_t aci_off = u32le(h + 0x70);
    uint32_t aci_size = u32le(h + 0x74);
    uint32_t acid_off = u32le(h + 0x78);
    uint32_t acid_size = u32le(h + 0x7c);

    char app_name[17];
    memcpy(app_name, h + 0x20, 16);
    app_name[16] = 0;
    for (int i = 15; i >= 0 && (app_name[i] == 0 || app_name[i] == ' '); --i) {
        app_name[i] = 0;
    }

    add(o, "flags=0x%02x is_64_bit=%s address_space_type=%u\n",
        flags, (flags & 1) ? "yes" : "no", (unsigned)((flags >> 1) & 7));
    add(o, "main_thread_priority=%u main_thread_cpu=%u\n",
        (unsigned)priority, (unsigned)cpu);
    add(o, "system_resource_size=0x%x\n", sys_resource);
    add(o, "process_category=0x%x\n", category);
    add(o, "main_stack_size=0x%x\n", stack);
    add(o, "application_name=%s\n", app_name[0] ? app_name : "(empty)");
    add(o, "aci_offset=0x%x aci_size=0x%x\n", aci_off, aci_size);
    add(o, "acid_offset=0x%x acid_size=0x%x\n", acid_off, acid_size);

    uint8_t magic[4];
    int switch2 = 0;
    int acid_valid = 0;
    if ((uint64_t)acid_off + 0x204 <= (uint64_t)file_size
            && read_exact(fd, magic, sizeof(magic), (off_t)acid_off + 0x200)
            && memcmp(magic, "ACID", 4) == 0) {
        acid_valid = 1;
        switch2 = 0;
        add(o, "acid_layout=Switch1 (ACID at +0x200)\n");
    } else if ((uint64_t)acid_off + 0x304 <= (uint64_t)file_size
            && read_exact(fd, magic, sizeof(magic), (off_t)acid_off + 0x300)
            && memcmp(magic, "ACID", 4) == 0) {
        acid_valid = 1;
        switch2 = 1;
        add(o, "acid_layout=Switch2 (ACID at +0x300)\n");
    }

    if (!acid_valid) {
        add(o, "FAIL acid_layout: ACID magic not found at Switch1 or Switch2 header position\n");
    } else {
        add(o, "PASS switch2_npdm=%s\n", switch2 ? "yes" : "no");
    }

    if ((uint64_t)aci_off + 0x18 <= (uint64_t)file_size) {
        uint8_t aci[0x18];
        if (read_exact(fd, aci, sizeof(aci), (off_t)aci_off)) {
            add(o, "aci_magic=%.4s\n", aci);
            if (memcmp(aci, "ACI0", 4) == 0) {
                add(o, "title_id=0x%016" PRIx64 "\n", u64le(aci + 0x10));
            }
        }
    }

    if (switch2) {
        add(o, "STAGE1A_SWITCH2_DETECTED=yes\n");
    } else if (acid_valid) {
        add(o, "STAGE1A_SWITCH2_DETECTED=no\n");
    }
}

JNIEXPORT jstring JNICALL
Java_org_nx2a_stage1_MainActivity_inspectExeFs(
        JNIEnv* env, jobject thiz,
        jint main_fd, jlong main_size,
        jint npdm_fd, jlong npdm_size) {
    (void)thiz;

    char report[16384];
    report[0] = 0;
    Out o = {report, sizeof(report)};

    add(&o, "NX2 Switch 2 Native Stage 1A ExeFS Report\n");
    add(&o, "mode=read-only header inspection\n");
    add(&o, "goal=validate the exact user-owned ExeFS before ARM64 rewriting/execution\n\n");

    inspect_nso(&o, main_fd, (int64_t)main_size);
    inspect_npdm(&o, npdm_fd, (int64_t)npdm_size);

    add(&o, "\nNEXT=If Switch2 detection and NSO layout pass, Stage 1B will add decompression, instruction census, Android rewrite, and headless entry execution.\n");

    return (*env)->NewStringUTF(env, report);
}
