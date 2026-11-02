// SPDX-License-Identifier: GPL-3.0-or-later
// Stage 1C: controlled first execution of a specifically validated Switch 2 ExeFS build.
//
// Architecture follows the nx2android/nxnative model: the host replaces Horizon rtld,
// maps modules, applies dynamic relocations and enters SDK startup itself.
//
// This bring-up harness deliberately does NOT implement Switch services yet.
// SVCs and unresolved imports are converted into controlled BRK stop points so the
// first missing dependency can be reported without allowing a Switch SVC to become
// an Android syscall.
//
// No decryption, keys, firmware, game modification or network upload is performed.

#include <jni.h>
#include <elf.h>
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <lz4.h>
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <ucontext.h>
#include <unistd.h>

#ifndef DT_RELR
#define DT_RELRSZ 35
#define DT_RELR 36
#define DT_RELRENT 37
#endif

#ifndef R_AARCH64_ABS64
#define R_AARCH64_ABS64 257
#define R_AARCH64_PREL64 260
#define R_AARCH64_GLOB_DAT 1025
#define R_AARCH64_JUMP_SLOT 1026
#define R_AARCH64_RELATIVE 1027
#endif

#define EXPECT_TITLE_ID 0x040074a01bf12000ULL
#define MAX_MODULES 2
#define MAX_IMPORTS 4096
#define REPORT_CAP 131072
#define STUB_BYTES (MAX_IMPORTS * 8)
#define GUEST_STACK_SIZE (2u << 20)
#define MAX_PHYS_RANGES 4096
#define MAX_IPC_SESSIONS 128
#define SM_SESSION_HANDLE 0x100u
#define HEAP_REGION_SIZE  (12ull << 30)
#define ALIAS_REGION_MAX   (64ull << 30)
#define ALIAS_REGION_MIN   (16ull << 30)
#define STACK_REGION_SIZE  (2ull << 30)

enum {
    TR_TPIDR_R = 0x1000,
    TR_TPIDR_W = 0x1100,
    TR_TPIDRRO_R = 0x1200,
    TR_CNTPCT = 0x1300,
    TR_CNTVCT = 0x1400,
    TR_CNTFRQ = 0x1500,
    TR_CTR = 0x1600,
    TR_SVC = 0x2000,
    TR_IMPORT = 0x8000,
};

typedef struct {
    char *p;
    size_t left;
} Out;

typedef struct {
    char name[8];
    int fd;
    int64_t file_size;
    uint8_t build_id[16];

    uint8_t *base;
    size_t map_size;

    uint32_t text_off, text_size;
    uint32_t ro_off, ro_size;
    uint32_t data_off, data_size;
    uint32_t bss_size;

    Elf64_Dyn *dyn;
    Elf64_Sym *symtab;
    const char *strtab;
    size_t nsym;

    uint64_t rela, relasz;
    uint64_t jmprel, pltrelsz;
    uint64_t relr, relrsz;

    uint64_t init;
    uint64_t init_array;
    uint64_t init_arraysz;

    uint64_t patch_svc;
    uint64_t patch_tpidr_r;
    uint64_t patch_tpidr_w;
    uint64_t patch_tpidrro_r;
    uint64_t patch_cntpct;
    uint64_t patch_cntvct;
    uint64_t patch_cntfrq;
    uint64_t patch_ctr;

    uint64_t reloc_relative;
    uint64_t reloc_relr;
    uint64_t reloc_symbol;
} Module;

typedef struct {
    int type;
    int sig;
    int arg;
    uint64_t pc;
    uint64_t fault;
    uint32_t insn;
    uint64_t regs[8];
} StopEvent;

static Module g_mod[MAX_MODULES];
static int g_nmod;

static char *g_import_name[MAX_IMPORTS];
static unsigned g_nimport;
static uint8_t *g_stub_page;
static size_t g_stub_map_size;

static uint8_t *g_guest_tls;
static uint8_t *g_guest_tlr;
static uint8_t *g_guest_stack;
static void *g_guest_stack_top;
static uint8_t *g_heap_region;
static size_t g_heap_region_size;
static size_t g_heap_current;

static uint8_t *g_alias_region;
static size_t g_alias_region_size;

static uint8_t *g_stack_region;
static size_t g_stack_region_size;

static uint8_t *g_nx2_region;
static size_t g_nx2_region_size;

static uint64_t g_aslr_region_addr;
static uint64_t g_aslr_region_size;

static _Alignas(0x1000) uint8_t g_user_exception_ctx[0x1000];
static volatile sig_atomic_t g_getinfo_hits[64];
static volatile sig_atomic_t g_svc_hits[256];

typedef struct {
    uint64_t base;
    uint64_t size;
} PhysRange;

static PhysRange g_phys[MAX_PHYS_RANGES];
static unsigned g_phys_count;

typedef struct {
    uint32_t handle;
    char name[12];
    int active;
} IpcSession;

static IpcSession g_ipc_sessions[MAX_IPC_SESSIONS];
static unsigned g_ipc_session_count;
static uint32_t g_next_ipc_handle = 0x200;
static int g_sm_initialized;
static uint32_t g_pointer_buffer_size = 0x10000;
static char g_ipc_stop_service[16];
static uint32_t g_ipc_stop_command;
static uint32_t g_ipc_stop_type;
static uint32_t g_ipc_stop_words[16];

static int32_t g_main_thread_priority = 44;
static int32_t g_main_thread_core = 0;
static uint64_t g_main_thread_mask = 0x3f;
static uint64_t g_main_thread_id = 1;

static sigjmp_buf g_jmp;
static volatile sig_atomic_t g_guest_active;
static StopEvent g_stop;
static int g_phase;

extern uint64_t nx2_guest_call(void *fn, uint64_t arg0, uint64_t arg1, void *stack_top);

static const uint8_t EXPECT_MAIN_BUILD[16] = {
    0xa1,0xdd,0x74,0x80,0xbc,0xa7,0xb6,0xe8,
    0xfe,0x4f,0x71,0xd2,0x5d,0x3b,0xab,0x09
};
static const uint8_t EXPECT_SDK_BUILD[16] = {
    0xb7,0xff,0xf6,0x14,0xf2,0xbc,0xdd,0xd3,
    0x8e,0x80,0xd5,0x71,0x61,0x9c,0x73,0xf3
};
static const uint8_t EXPECT_RTLD_BUILD[16] = {
    0x9b,0xd0,0xb8,0x87,0xb6,0x48,0x47,0xa6,
    0x10,0xf7,0x8a,0xfe,0x16,0x99,0xdf,0x3d
};

static const char *PHASES[] = {
    "none",
    "_init_libc0",
    "nnosInitialize",
    "_init_libc1",
    "nninitInitializeSdkModule",
    "nninitStartup",
    "_init_libc2",
    "module DT_INIT",
    "nnMain",
};

static void add(Out *o, const char *fmt, ...) {
    if (!o || o->left == 0) return;
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

static uint32_t u32le(const uint8_t *p) {
    return (uint32_t)p[0]
        | ((uint32_t)p[1] << 8)
        | ((uint32_t)p[2] << 16)
        | ((uint32_t)p[3] << 24);
}

static uint64_t u64le(const uint8_t *p) {
    return (uint64_t)u32le(p) | ((uint64_t)u32le(p + 4) << 32);
}

static size_t align_up(size_t n, size_t a) {
    return (n + a - 1) & ~(a - 1);
}

static uintptr_t align_down_ptr(uintptr_t n, size_t a) {
    return n & ~(uintptr_t)(a - 1);
}

static void *reserve_aligned_sparse(size_t size, size_t alignment) {
    if (!size || !alignment || (alignment & (alignment - 1))) return MAP_FAILED;

    size_t total;
    if (__builtin_add_overflow(size, alignment, &total)) return MAP_FAILED;

    uint8_t *raw = (uint8_t *)mmap(
        NULL, total,
        PROT_NONE,
        MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE,
        -1, 0);
    if (raw == MAP_FAILED) return MAP_FAILED;

    uintptr_t aligned =
        ((uintptr_t)raw + alignment - 1) & ~(uintptr_t)(alignment - 1);
    size_t prefix = (size_t)(aligned - (uintptr_t)raw);
    size_t suffix = total - prefix - size;

    if (prefix) munmap(raw, prefix);
    if (suffix) munmap((void *)(aligned + size), suffix);

    return (void *)aligned;
}

static int reserve_guest_geometry(Out *o) {
    const size_t G = 1ull << 30;

    g_heap_region_size = HEAP_REGION_SIZE;
    g_heap_region = (uint8_t *)reserve_aligned_sparse(g_heap_region_size, G);
    if (g_heap_region == MAP_FAILED) {
        g_heap_region = NULL;
        add(o, "FAIL guest_heap_reserve size=0x%zx errno=%d\n",
            g_heap_region_size, errno);
        return 0;
    }

    g_stack_region_size = STACK_REGION_SIZE;
    g_stack_region = (uint8_t *)reserve_aligned_sparse(g_stack_region_size, G);
    if (g_stack_region == MAP_FAILED) {
        g_stack_region = NULL;
        add(o, "FAIL guest_stack_region_reserve size=0x%zx errno=%d\n",
            g_stack_region_size, errno);
        return 0;
    }

    for (size_t want = ALIAS_REGION_MAX; want >= ALIAS_REGION_MIN; want >>= 1) {
        g_alias_region = (uint8_t *)reserve_aligned_sparse(want, G);
        if (g_alias_region != MAP_FAILED) {
            g_alias_region_size = want;
            break;
        }
        g_alias_region = NULL;
        if (want == ALIAS_REGION_MIN) break;
    }
    if (!g_alias_region) {
        add(o, "FAIL guest_alias_reserve min=0x%llx errno=%d\n",
            (unsigned long long)ALIAS_REGION_MIN, errno);
        return 0;
    }

    // Match the current Eglantine Switch 2 bring-up model: the NX2/VAMM region
    // occupies the upper half of the alias region and MapPhysicalMemory will
    // eventually commit pages inside it.
    g_nx2_region = g_alias_region + g_alias_region_size / 2;
    g_nx2_region_size = g_alias_region_size / 2;

    // The native loader maps NSOs at host addresses. Advertise a compact ASLR
    // window which actually contains those mappings instead of a fictitious
    // console address range.
    uint64_t lo = UINT64_MAX;
    uint64_t hi = 0;
    for (int i = 0; i < g_nmod; ++i) {
        uint64_t b = (uint64_t)(uintptr_t)g_mod[i].base;
        uint64_t e = b + g_mod[i].map_size;
        if (b < lo) lo = b;
        if (e > hi) hi = e;
    }
    if (lo != UINT64_MAX) {
        lo = (uint64_t)align_down_ptr((uintptr_t)lo, G);
        hi = (uint64_t)align_up((size_t)hi, G);
        g_aslr_region_addr = lo;
        g_aslr_region_size = hi - lo;
    }

    add(o,
        "guest_geometry heap=%p+0x%zx alias=%p+0x%zx "
        "stack=%p+0x%zx nx2=%p+0x%zx aslr=0x%" PRIx64 "+0x%" PRIx64 "\n",
        g_heap_region, g_heap_region_size,
        g_alias_region, g_alias_region_size,
        g_stack_region, g_stack_region_size,
        g_nx2_region, g_nx2_region_size,
        g_aslr_region_addr, g_aslr_region_size);

    return 1;
}

static int read_exact(int fd, void *dst, size_t n, off_t off) {
    uint8_t *p = (uint8_t *)dst;
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

static void print_build(Out *o, const uint8_t id[16]) {
    for (int i = 0; i < 16; ++i) add(o, "%02x", id[i]);
}

static int check_nso_identity(
        Out *o, int fd, int64_t hinted, const char *name,
        const uint8_t expected[16]) {
    uint8_t h[0x100];
    int64_t size = actual_size(fd, hinted);
    if (size < (int64_t)sizeof(h)
            || !read_exact(fd, h, sizeof(h), 0)
            || memcmp(h, "NSO0", 4) != 0) {
        add(o, "FAIL identity_%s: not a readable NSO0\n", name);
        return 0;
    }

    add(o, "%s_build=", name);
    print_build(o, h + 0x40);
    add(o, "\n");

    if (memcmp(h + 0x40, expected, 16) != 0) {
        add(o, "REFUSED wrong_%s_build=yes\n", name);
        return 0;
    }
    return 1;
}

static int check_npdm(Out *o, int fd, int64_t hinted) {
    uint8_t h[0x80];
    int64_t size = actual_size(fd, hinted);

    if (size < (int64_t)sizeof(h)
            || !read_exact(fd, h, sizeof(h), 0)
            || memcmp(h, "META", 4) != 0) {
        add(o, "FAIL npdm: not META\n");
        return 0;
    }

    uint32_t aci_off = u32le(h + 0x70);
    uint32_t acid_off = u32le(h + 0x78);
    uint8_t magic[4];
    int sw2 = 0;

    if ((uint64_t)acid_off + 0x304 <= (uint64_t)size
            && read_exact(fd, magic, sizeof(magic), (off_t)acid_off + 0x300)
            && memcmp(magic, "ACID", 4) == 0) {
        sw2 = 1;
    }

    uint64_t title = 0;
    if ((uint64_t)aci_off + 0x18 <= (uint64_t)size) {
        uint8_t aci[0x18];
        if (read_exact(fd, aci, sizeof(aci), (off_t)aci_off)
                && memcmp(aci, "ACI0", 4) == 0) {
            title = u64le(aci + 0x10);
        }
    }

    add(o, "npdm_switch2=%s\n", sw2 ? "PASS" : "FAIL");
    add(o, "title_id=0x%016" PRIx64 "\n", title);

    if (!sw2 || title != EXPECT_TITLE_ID) {
        add(o, "REFUSED title_or_npdm_mismatch=yes\n");
        return 0;
    }
    return 1;
}

static int decode_segment(
        Out *o, int fd, int64_t file_size, uint8_t *image,
        const char *module, const char *seg,
        uint32_t file_off, uint32_t mem_off, uint32_t mem_size,
        uint32_t stored_size, int compressed) {
    size_t input_size = compressed ? stored_size : mem_size;
    if ((uint64_t)file_off + input_size > (uint64_t)file_size) {
        add(o, "FAIL %s.%s stored bytes exceed NSO\n", module, seg);
        return 0;
    }

    if (!compressed) {
        if (!read_exact(fd, image + mem_off, mem_size, file_off)) {
            add(o, "FAIL %s.%s read\n", module, seg);
            return 0;
        }
        return 1;
    }

    if (input_size > INT_MAX || mem_size > INT_MAX) {
        add(o, "FAIL %s.%s too large for LZ4\n", module, seg);
        return 0;
    }

    uint8_t *packed = (uint8_t *)malloc(input_size);
    if (!packed) {
        add(o, "FAIL %s.%s packed allocation\n", module, seg);
        return 0;
    }

    if (!read_exact(fd, packed, input_size, file_off)) {
        free(packed);
        add(o, "FAIL %s.%s packed read\n", module, seg);
        return 0;
    }

    int decoded = LZ4_decompress_safe(
        (const char *)packed,
        (char *)image + mem_off,
        (int)input_size,
        (int)mem_size);
    free(packed);

    if (decoded != (int)mem_size) {
        add(o, "FAIL %s.%s LZ4=%d expected=%u\n",
            module, seg, decoded, mem_size);
        return 0;
    }
    return 1;
}

static uint32_t make_brk(unsigned imm16) {
    return 0xD4200000u | ((imm16 & 0xffffu) << 5);
}

static void patch_text(Module *m) {
    for (uint64_t off = 0; off + 4 <= m->text_size; off += 4) {
        uint32_t *wp = (uint32_t *)(m->base + m->text_off + off);
        uint32_t w = *wp;
        unsigned reg = w & 31u;

        if ((w & 0xFFE0001F) == 0xD4000001) {
            unsigned svc = (w >> 5) & 0xffffu;
            *wp = make_brk(TR_SVC | (svc & 0xfffu));
            m->patch_svc++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BD040) {
            *wp = make_brk(TR_TPIDR_R | reg);
            m->patch_tpidr_r++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD51BD040) {
            *wp = make_brk(TR_TPIDR_W | reg);
            m->patch_tpidr_w++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BD060) {
            *wp = make_brk(TR_TPIDRRO_R | reg);
            m->patch_tpidrro_r++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE020) {
            *wp = make_brk(TR_CNTPCT | reg);
            m->patch_cntpct++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE040) {
            *wp = make_brk(TR_CNTVCT | reg);
            m->patch_cntvct++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53BE000) {
            *wp = make_brk(TR_CNTFRQ | reg);
            m->patch_cntfrq++;
            continue;
        }
        if ((w & 0xFFFFFFE0) == 0xD53B0020) {
            *wp = make_brk(TR_CTR | reg);
            m->patch_ctr++;
            continue;
        }
    }
}

static int parse_dynamic(Out *o, Module *m) {
    if (m->map_size < 8) return 0;

    uint32_t mod0 = u32le(m->base + 4);
    if ((uint64_t)mod0 + 8 > m->map_size
            || memcmp(m->base + mod0, "MOD0", 4) != 0) {
        add(o, "FAIL %s MOD0\n", m->name);
        return 0;
    }

    int32_t dyn_rel;
    memcpy(&dyn_rel, m->base + mod0 + 4, sizeof(dyn_rel));
    int64_t dyn_off_signed = (int64_t)mod0 + dyn_rel;
    if (dyn_off_signed < 0
            || (uint64_t)dyn_off_signed + sizeof(Elf64_Dyn) > m->map_size) {
        add(o, "FAIL %s dynamic offset\n", m->name);
        return 0;
    }

    m->dyn = (Elf64_Dyn *)(m->base + dyn_off_signed);

    uint64_t hash_off = 0;
    for (size_t i = 0; i < 8192; ++i) {
        Elf64_Dyn *d = &m->dyn[i];
        if (d->d_tag == DT_NULL) break;

        uint64_t v = d->d_un.d_ptr;
        switch (d->d_tag) {
        case DT_SYMTAB: m->symtab = (Elf64_Sym *)(m->base + v); break;
        case DT_STRTAB: m->strtab = (const char *)(m->base + v); break;
        case DT_HASH: hash_off = v; break;
        case DT_RELA: m->rela = v; break;
        case DT_RELASZ: m->relasz = d->d_un.d_val; break;
        case DT_JMPREL: m->jmprel = v; break;
        case DT_PLTRELSZ: m->pltrelsz = d->d_un.d_val; break;
        case DT_RELR: m->relr = v; break;
        case DT_RELRSZ: m->relrsz = d->d_un.d_val; break;
        case DT_INIT: m->init = v; break;
        case DT_INIT_ARRAY: m->init_array = v; break;
        case DT_INIT_ARRAYSZ: m->init_arraysz = d->d_un.d_val; break;
        default: break;
        }
    }

    if (!m->symtab || !m->strtab) {
        add(o, "FAIL %s missing SYMTAB/STRTAB\n", m->name);
        return 0;
    }

    if (hash_off && hash_off + 8 <= m->map_size) {
        const uint32_t *h = (const uint32_t *)(m->base + hash_off);
        m->nsym = h[1];
    } else if ((const uint8_t *)m->strtab > (const uint8_t *)m->symtab) {
        m->nsym =
            (size_t)((const uint8_t *)m->strtab
                     - (const uint8_t *)m->symtab) / sizeof(Elf64_Sym);
    }

    if (!m->nsym || m->nsym > 1000000) {
        add(o, "FAIL %s bad symbol count=%zu\n", m->name, m->nsym);
        return 0;
    }
    return 1;
}

static int load_module(
        Out *o, Module *m, const char *name,
        int fd, int64_t hinted, const uint8_t expected_build[16]) {
    memset(m, 0, sizeof(*m));
    snprintf(m->name, sizeof(m->name), "%s", name);
    m->fd = fd;
    m->file_size = actual_size(fd, hinted);

    uint8_t h[0x100];
    if (m->file_size < (int64_t)sizeof(h)
            || !read_exact(fd, h, sizeof(h), 0)
            || memcmp(h, "NSO0", 4) != 0) {
        add(o, "FAIL load_%s header\n", name);
        return 0;
    }

    memcpy(m->build_id, h + 0x40, 16);
    if (memcmp(m->build_id, expected_build, 16) != 0) {
        add(o, "REFUSED %s changed since Stage1B2\n", name);
        return 0;
    }

    uint32_t flags = u32le(h + 0x0c);
    if (flags & 0x80) {
        add(o, "REFUSED %s uses unsupported newer compression flag 0x80\n", name);
        return 0;
    }

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

    m->text_off = mo[0]; m->text_size = sz[0];
    m->ro_off = mo[1]; m->ro_size = sz[1];
    m->data_off = mo[2]; m->data_size = sz[2];
    m->bss_size = u32le(h + 0x3c);

    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uint64_t wanted = image_end + m->bss_size;
    m->map_size = align_up((size_t)wanted, page);

    m->base = (uint8_t *)mmap(
        NULL, m->map_size,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS,
        -1, 0);
    if (m->base == MAP_FAILED) {
        m->base = NULL;
        add(o, "FAIL mmap_%s size=0x%zx errno=%d\n",
            name, m->map_size, errno);
        return 0;
    }

    const char *seg[3] = {"text", "ro", "data"};
    for (int i = 0; i < 3; ++i) {
        if (!decode_segment(
                o, fd, m->file_size, m->base,
                name, seg[i],
                fo[i], mo[i], sz[i], csz[i],
                (flags >> i) & 1)) {
            return 0;
        }
    }

    patch_text(m);
    if (!parse_dynamic(o, m)) return 0;

    add(o,
        "%s_loaded base=%p map=0x%zx symbols=%zu "
        "patches{svc=%" PRIu64 ",tpidr=%" PRIu64 ",tpidrw=%" PRIu64
        ",tpidrro=%" PRIu64 ",cntpct=%" PRIu64 ",ctr=%" PRIu64 "}\n",
        m->name, m->base, m->map_size, m->nsym,
        m->patch_svc, m->patch_tpidr_r, m->patch_tpidr_w,
        m->patch_tpidrro_r, m->patch_cntpct, m->patch_ctr);

    return 1;
}

static void *defined_symbol_in(Module *m, const char *name) {
    for (size_t i = 1; i < m->nsym; ++i) {
        Elf64_Sym *s = &m->symtab[i];
        if (s->st_shndx == SHN_UNDEF || !s->st_name) continue;
        const char *n = m->strtab + s->st_name;
        if (!strcmp(n, name)) {
            return m->base + s->st_value;
        }
    }
    return NULL;
}

static void *guest_symbol(const char *name) {
    for (int i = 0; i < g_nmod; ++i) {
        void *p = defined_symbol_in(&g_mod[i], name);
        if (p) return p;
    }
    return NULL;
}

static void *import_stub(const char *name) {
    for (unsigned i = 0; i < g_nimport; ++i) {
        if (!strcmp(g_import_name[i], name)) {
            return g_stub_page + (size_t)i * 8;
        }
    }

    if (g_nimport >= MAX_IMPORTS) return NULL;
    unsigned idx = g_nimport++;
    g_import_name[idx] = strdup(name && *name ? name : "<unnamed>");

    uint32_t *p = (uint32_t *)(g_stub_page + (size_t)idx * 8);
    p[0] = make_brk(TR_IMPORT | idx);
    p[1] = 0xD65F03C0u; // ret
    return p;
}

static uint64_t resolve_symbol(Module *request, unsigned sym_index, Out *o) {
    if (sym_index >= request->nsym) {
        add(o, "FAIL %s relocation symbol index %u >= %zu\n",
            request->name, sym_index, request->nsym);
        return UINT64_MAX;
    }

    Elf64_Sym *s = &request->symtab[sym_index];
    const char *name = s->st_name ? request->strtab + s->st_name : "";

    if (s->st_shndx != SHN_UNDEF
            && ELF64_ST_BIND(s->st_info) == STB_LOCAL) {
        return (uint64_t)(request->base + s->st_value);
    }

    for (int i = 0; i < g_nmod; ++i) {
        void *p = defined_symbol_in(&g_mod[i], name);
        if (p) return (uint64_t)p;
    }

    if (s->st_shndx != SHN_UNDEF) {
        return (uint64_t)(request->base + s->st_value);
    }

    if (ELF64_ST_BIND(s->st_info) == STB_WEAK) return 0;

    void *stub = import_stub(name);
    if (!stub) {
        add(o, "FAIL import stub overflow at %s\n", name);
        return UINT64_MAX;
    }
    return (uint64_t)stub;
}

static int apply_rela_table(
        Out *o, Module *m, uint64_t off, uint64_t bytes,
        unsigned *unsupported) {
    if (!off || !bytes) return 1;
    if (off + bytes > m->map_size
            || bytes % sizeof(Elf64_Rela)) {
        add(o, "FAIL %s RELA bounds off=0x%" PRIx64 " size=0x%" PRIx64 "\n",
            m->name, off, bytes);
        return 0;
    }

    Elf64_Rela *tab = (Elf64_Rela *)(m->base + off);
    size_t n = (size_t)(bytes / sizeof(Elf64_Rela));

    for (size_t i = 0; i < n; ++i) {
        Elf64_Rela *r = &tab[i];
        uint32_t type = (uint32_t)ELF64_R_TYPE(r->r_info);
        unsigned sym = (unsigned)ELF64_R_SYM(r->r_info);

        if (r->r_offset + 8 > m->map_size) {
            add(o, "FAIL %s relocation target outside image\n", m->name);
            return 0;
        }

        uint64_t *where = (uint64_t *)(m->base + r->r_offset);
        switch (type) {
        case R_AARCH64_NONE:
            break;
        case R_AARCH64_RELATIVE:
            *where = (uint64_t)m->base + (uint64_t)r->r_addend;
            m->reloc_relative++;
            break;
        case R_AARCH64_ABS64:
        case R_AARCH64_GLOB_DAT:
        case R_AARCH64_JUMP_SLOT: {
            uint64_t target = resolve_symbol(m, sym, o);
            if (target == UINT64_MAX) return 0;
            if (type == R_AARCH64_JUMP_SLOT) {
                *where = target;
            } else {
                *where = target + (uint64_t)r->r_addend;
            }
            m->reloc_symbol++;
            break;
        }
        case R_AARCH64_PREL64: {
            uint64_t target = resolve_symbol(m, sym, o);
            if (target == UINT64_MAX) return 0;
            *where = target + (uint64_t)r->r_addend - (uint64_t)where;
            m->reloc_symbol++;
            break;
        }
        default:
            if ((*unsupported)++ < 16) {
                add(o,
                    "UNSUPPORTED_RELOC %s type=%u offset=0x%" PRIx64
                    " sym=%u\n",
                    m->name, type, r->r_offset, sym);
            }
            break;
        }
    }
    return 1;
}

static int apply_relr(Out *o, Module *m) {
    if (!m->relr || !m->relrsz) return 1;
    if (m->relr + m->relrsz > m->map_size
            || m->relrsz % sizeof(uint64_t)) {
        add(o, "FAIL %s RELR bounds\n", m->name);
        return 0;
    }

    const uint64_t *tab = (const uint64_t *)(m->base + m->relr);
    size_t n = (size_t)(m->relrsz / sizeof(uint64_t));
    uint64_t *where = NULL;

    for (size_t i = 0; i < n; ++i) {
        uint64_t v = tab[i];
        if (!(v & 1)) {
            if (v + 8 > m->map_size) return 0;
            where = (uint64_t *)(m->base + v);
            *where++ += (uint64_t)m->base;
            m->reloc_relr++;
            continue;
        }

        if (!where) {
            add(o, "FAIL %s RELR bitmap without base\n", m->name);
            return 0;
        }

        for (unsigned b = 0; b < 63; ++b) {
            if (v & (1ULL << (b + 1))) {
                uint64_t *slot = where + b;
                if ((uint8_t *)(slot + 1) > m->base + m->map_size) return 0;
                *slot += (uint64_t)m->base;
                m->reloc_relr++;
            }
        }
        where += 63;
    }
    return 1;
}

static int relocate_all(Out *o) {
    unsigned unsupported = 0;

    for (int i = 0; i < g_nmod; ++i) {
        Module *m = &g_mod[i];
        if (!apply_relr(o, m)) return 0;
        if (!apply_rela_table(o, m, m->rela, m->relasz, &unsupported)) return 0;
        if (!apply_rela_table(o, m, m->jmprel, m->pltrelsz, &unsupported)) return 0;

        add(o,
            "%s_reloc relative=%" PRIu64 " relr=%" PRIu64
            " symbol=%" PRIu64 "\n",
            m->name, m->reloc_relative, m->reloc_relr, m->reloc_symbol);
    }

    if (unsupported) {
        add(o, "BLOCKED unsupported_dynamic_relocations=%u\n", unsupported);
        return 0;
    }

    add(o, "unresolved_import_traps=%u\n", g_nimport);
    for (unsigned i = 0; i < g_nimport && i < 24; ++i) {
        add(o, "  import[%u]=%s\n", i, g_import_name[i]);
    }
    if (g_nimport > 24) {
        add(o, "  ... %u more unresolved imports\n", g_nimport - 24);
    }
    return 1;
}

static int protect_range(uint8_t *base, size_t off, size_t len, int prot) {
    if (!len) return 1;
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uintptr_t start = align_down_ptr((uintptr_t)(base + off), page);
    uintptr_t end = align_up((size_t)((uintptr_t)(base + off + len)), page);
    return mprotect((void *)start, end - start, prot) == 0;
}

static int seal_modules(Out *o) {
    if (mprotect(g_stub_page, g_stub_map_size, PROT_READ | PROT_EXEC) != 0) {
        add(o, "FAIL stub mprotect errno=%d\n", errno);
        return 0;
    }
    __builtin___clear_cache((char *)g_stub_page, (char *)g_stub_page + g_stub_map_size);

    for (int i = 0; i < g_nmod; ++i) {
        Module *m = &g_mod[i];
        __builtin___clear_cache(
            (char *)m->base + m->text_off,
            (char *)m->base + m->text_off + m->text_size);

        if (!protect_range(m->base, m->text_off, m->text_size, PROT_READ | PROT_EXEC)
                || !protect_range(m->base, m->ro_off, m->ro_size, PROT_READ)
                || !protect_range(
                    m->base, m->data_off,
                    m->map_size - m->data_off,
                    PROT_READ | PROT_WRITE)) {
            add(o, "FAIL %s mprotect errno=%d\n", m->name, errno);
            return 0;
        }
    }
    return 1;
}

static uint64_t host_counter(void) {
#if defined(__aarch64__)
    uint64_t v = 0;
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(v));
    return v;
#else
    return 0;
#endif
}

static uint64_t switch2_counter(void) {
    __uint128_t v = host_counter();
    v = v * 625u;
    v /= 384u;
    return (uint64_t)v;
}


static int phys_find_containing(uint64_t addr);

static int guest_readable(uint64_t addr, size_t n) {
    if (!addr || !n || addr + n < addr) return 0;

    for (int i = 0; i < g_nmod; ++i) {
        uint64_t b = (uint64_t)(uintptr_t)g_mod[i].base;
        if (addr >= b && addr + n <= b + g_mod[i].map_size) return 1;
    }

    uint64_t sb = (uint64_t)(uintptr_t)g_guest_stack;
    if (g_guest_stack && addr >= sb && addr + n <= sb + GUEST_STACK_SIZE) return 1;

    uint64_t tb = (uint64_t)(uintptr_t)g_guest_tls;
    if (g_guest_tls && addr >= tb && addr + n <= tb + 0x4000) return 1;

    uint64_t rb = (uint64_t)(uintptr_t)g_guest_tlr;
    if (g_guest_tlr && addr >= rb && addr + n <= rb + 0x4000) return 1;

    uint64_t hb = (uint64_t)(uintptr_t)g_heap_region;
    if (g_heap_region && addr >= hb && addr + n <= hb + g_heap_current) return 1;

    int k = phys_find_containing(addr);
    if (k >= 0 && addr + n <= g_phys[k].base + g_phys[k].size) return 1;

    return 0;
}

static int guest_copy_cstring(char *out, size_t cap, uint64_t addr) {
    if (!out || cap < 2 || !addr) return 0;
    size_t i = 0;
    for (; i + 1 < cap; ++i) {
        if (!guest_readable(addr + i, 1)) return 0;
        char ch = *(const char *)(uintptr_t)(addr + i);
        out[i] = ch;
        if (!ch) return 1;
    }
    out[i] = 0;
    return 1;
}

static IpcSession *ipc_find(uint32_t handle) {
    if (handle == SM_SESSION_HANDLE) return NULL;
    for (unsigned i = 0; i < g_ipc_session_count; ++i) {
        if (g_ipc_sessions[i].active && g_ipc_sessions[i].handle == handle) {
            return &g_ipc_sessions[i];
        }
    }
    return NULL;
}

static uint32_t ipc_open_service(const char *name) {
    for (unsigned i = 0; i < g_ipc_session_count; ++i) {
        if (!g_ipc_sessions[i].active) {
            g_ipc_sessions[i].active = 1;
            g_ipc_sessions[i].handle = g_next_ipc_handle++;
            snprintf(g_ipc_sessions[i].name, sizeof(g_ipc_sessions[i].name), "%s", name);
            return g_ipc_sessions[i].handle;
        }
    }

    if (g_ipc_session_count >= MAX_IPC_SESSIONS) return 0;
    IpcSession *s = &g_ipc_sessions[g_ipc_session_count++];
    memset(s, 0, sizeof(*s));
    s->active = 1;
    s->handle = g_next_ipc_handle++;
    snprintf(s->name, sizeof(s->name), "%s", name);
    return s->handle;
}

static void ipc_close(uint32_t handle) {
    for (unsigned i = 0; i < g_ipc_session_count; ++i) {
        if (g_ipc_sessions[i].handle == handle) {
            g_ipc_sessions[i].active = 0;
            return;
        }
    }
}

static uint32_t *ipc_command_buffer(void) {
    // On Horizon, TPIDRRO_EL0 points at the thread-local region whose first
    // 0x100 bytes are the IPC command buffer. Eden's KThread::GetTlsAddress()
    // likewise points directly at this command area.
    return (uint32_t *)g_guest_tlr;
}

typedef struct {
    uint32_t type;
    uint32_t command;
    unsigned params_word;
    int tipc;
} IpcParsed;

static int ipc_parse_request(uint32_t *b, IpcParsed *p) {
    if (!b || !p) return 0;
    memset(p, 0, sizeof(*p));

    uint32_t low = b[0];
    uint32_t high = b[1];
    p->type = low & 0xffffu;

    if (p->type >= 16) {
        p->tipc = 1;
        p->command = p->type - 16;
        p->params_word = 2;
        return 1;
    }

    // HIPC / CMIF: skip handle descriptor and buffer descriptors, then align
    // to 16 bytes, skip SFCI header, then read the u64 command id.
    unsigned x = (low >> 16) & 0xf;
    unsigned a = (low >> 20) & 0xf;
    unsigned bb = (low >> 24) & 0xf;
    unsigned w = (low >> 28) & 0xf;
    unsigned idx = 2;

    if (high >> 31) {
        uint32_t hd = b[idx++];
        if (hd & 1) idx += 2;
        idx += (hd >> 1) & 0xf;
        idx += (hd >> 5) & 0xf;
    }

    idx += x * 2 + (a + bb + w) * 3;
    idx = (idx + 3) & ~3u;

    if (idx + 4 >= 64) return 0;
    if (b[idx] != 0x49434653u) return 0; // SFCI

    p->command = b[idx + 2];
    p->params_word = idx + 4;
    return 1;
}

static void ipc_reply_success(uint32_t *b, const IpcParsed *p) {
    memset(b, 0, 0x100);
    if (p->tipc) {
        b[0] = p->type;
        b[1] = 1; // one 32-bit result word in TIPC response payload
        b[2] = 0; // ResultSuccess
    } else {
        b[0] = 0; // HIPC response type
        b[1] = 10;
        b[4] = 0x4f434653u; // SFCO
        b[5] = 0;
        b[6] = 0;
        b[7] = 0;
    }
}


static void ipc_reply_success_u16(
        uint32_t *b, const IpcParsed *p, uint16_t value) {
    memset(b, 0, 0x100);
    if (p->tipc) {
        b[0] = p->type;
        b[1] = 2;
        b[2] = 0;
        b[3] = value;
    } else {
        b[0] = 0;
        b[1] = 12; // ResponseBuilder normal_params_size=3
        b[4] = 0x4f434653u; // SFCO
        b[5] = 0;
        b[6] = 0;
        b[7] = 0;
        b[8] = value;
    }
}

static uint32_t ipc_clone_session(uint32_t handle) {
    if (handle == SM_SESSION_HANDLE) return SM_SESSION_HANDLE;
    IpcSession *s = ipc_find(handle);
    if (!s) return 0;
    return ipc_open_service(s->name);
}

static void ipc_reply_move_handle(
        uint32_t *b, const IpcParsed *p, uint32_t handle) {
    memset(b, 0, 0x100);
    if (p->tipc) {
        b[0] = p->type;
        b[1] = 0x80000001u;
        b[2] = 0x20; // one moved handle
        b[3] = handle;
        b[4] = 0;
    } else {
        b[0] = 0;
        b[1] = 0x8000000au;
        b[2] = 0x20;
        b[3] = handle;
        b[4] = 0x4f434653u; // SFCO
        b[5] = 0;
        b[6] = 0;
        b[7] = 0;
    }
}

static void ipc_capture_stop(const char *service, const IpcParsed *p, uint32_t *b) {
    snprintf(g_ipc_stop_service, sizeof(g_ipc_stop_service), "%s", service ? service : "?");
    g_ipc_stop_command = p ? p->command : UINT32_MAX;
    g_ipc_stop_type = p ? p->type : UINT32_MAX;
    for (unsigned i = 0; i < 16; ++i) g_ipc_stop_words[i] = b ? b[i] : 0;
}

static int ipc_sm_request(uint32_t *b, const IpcParsed *p) {
    if (p->command == 0) { // SM::Initialize
        g_sm_initialized = 1;
        ipc_reply_success(b, p);
        return 1;
    }

    if (p->command == 1 && g_sm_initialized) { // SM::GetService
        char name[9];
        memset(name, 0, sizeof(name));
        memcpy(name, &b[p->params_word], 8);
        for (int i = 7; i >= 0 && (name[i] == 0 || name[i] == ' '); --i) name[i] = 0;
        if (!name[0]) snprintf(name, sizeof(name), "unknown");

        uint32_t h = ipc_open_service(name);
        if (!h) return 0;
        ipc_reply_move_handle(b, p, h);
        return 1;
    }

    return 0;
}


static int ipc_control_request(
        uint32_t handle, uint32_t *b, const IpcParsed *p) {
    // HIPC Control / ControlWithContext commands operate on the session itself.
    switch (p->command) {
    case 2: // DuplicateSession
    case 4: { // DuplicateSessionEx
        uint32_t h = ipc_clone_session(handle);
        if (!h) return 0;
        ipc_reply_move_handle(b, p, h);
        return 1;
    }
    case 3: // QueryPointerBufferSize
        ipc_reply_success_u16(b, p, (uint16_t)g_pointer_buffer_size);
        return 1;
    case 5: // SetPointerBufferSize (newer Horizon)
        if (p->params_word < 64) {
            uint32_t requested = b[p->params_word];
            g_pointer_buffer_size = requested > 0xffffu ? 0xffffu : requested;
            ipc_reply_success(b, p);
            return 1;
        }
        return 0;
    default:
        return 0;
    }
}

static int ipc_service_request(
        IpcSession *session, uint32_t *b, const IpcParsed *p) {
    if (!session) return 0;

    if (!strcmp(session->name, "lm")) {
        if (p->command == 0) { // LM::OpenLogger
            uint32_t h = ipc_open_service("ILogger");
            if (!h) return 0;
            ipc_reply_move_handle(b, p, h);
            return 1;
        }
        return 0;
    }

    if (!strcmp(session->name, "ILogger")) {
        switch (p->command) {
        case 0: // Log
        case 1: // SetDestination
        case 2: // TransmitHashedLog (20.0.0+)
        case 3: // DevNotify (20.0.0+)
            ipc_reply_success(b, p);
            return 1;
        case 11: { // Unknown11 returns another interface in current Eden
            uint32_t h = ipc_open_service("ILogger11");
            if (!h) return 0;
            ipc_reply_move_handle(b, p, h);
            return 1;
        }
        default:
            return 0;
        }
    }

    if (!strcmp(session->name, "ILogger11")) {
        ipc_reply_success(b, p);
        return 1;
    }

    return 0;
}

typedef struct {
    uint64_t base_address;
    uint64_t size;
    uint32_t state;
    uint32_t attribute;
    uint32_t permission;
    uint32_t ipc_count;
    uint32_t device_count;
    uint32_t padding;
} HorizonMemoryInfo;

static int range_contains(uint64_t base, uint64_t size, uint64_t addr) {
    return size && addr >= base && addr < base + size;
}

static uint64_t phys_mapped_total(void) {
    uint64_t total = 0;
    for (unsigned i = 0; i < g_phys_count; ++i) {
        total += g_phys[i].size;
    }
    return total;
}

static uint64_t guest_used_physical_memory(void) {
    uint64_t used = GUEST_STACK_SIZE + g_heap_current + phys_mapped_total();
    for (int i = 0; i < g_nmod; ++i) {
        used += g_mod[i].map_size;
    }
    if (used > HEAP_REGION_SIZE) used = HEAP_REGION_SIZE;
    return used;
}

static int phys_find_containing(uint64_t addr) {
    for (unsigned i = 0; i < g_phys_count; ++i) {
        uint64_t end = g_phys[i].base + g_phys[i].size;
        if (addr >= g_phys[i].base && addr < end) return (int)i;
        if (g_phys[i].base > addr) break;
    }
    return -1;
}

static int phys_add(uint64_t base, uint64_t size) {
    if (!size) return 1;
    uint64_t end = base + size;
    if (end < base) return 0;

    unsigned first = 0;
    while (first < g_phys_count
            && g_phys[first].base + g_phys[first].size < base) {
        first++;
    }

    unsigned last = first;
    uint64_t new_base = base;
    uint64_t new_end = end;
    while (last < g_phys_count && g_phys[last].base <= new_end) {
        if (g_phys[last].base < new_base) new_base = g_phys[last].base;
        uint64_t e = g_phys[last].base + g_phys[last].size;
        if (e > new_end) new_end = e;
        last++;
    }

    unsigned remove = last - first;
    if (!remove && g_phys_count >= MAX_PHYS_RANGES) return 0;

    if (remove != 1) {
        memmove(
            &g_phys[first + 1],
            &g_phys[last],
            (g_phys_count - last) * sizeof(g_phys[0]));
        g_phys_count = g_phys_count - remove + 1;
    }

    g_phys[first].base = new_base;
    g_phys[first].size = new_end - new_base;
    return 1;
}

static int phys_remove(uint64_t base, uint64_t size) {
    if (!size) return 1;
    uint64_t end = base + size;
    if (end < base) return 0;

    for (unsigned i = 0; i < g_phys_count; ) {
        uint64_t rb = g_phys[i].base;
        uint64_t re = rb + g_phys[i].size;

        if (re <= base) {
            i++;
            continue;
        }
        if (rb >= end) break;

        if (base <= rb && end >= re) {
            memmove(
                &g_phys[i], &g_phys[i + 1],
                (g_phys_count - i - 1) * sizeof(g_phys[0]));
            g_phys_count--;
            continue;
        }

        if (base <= rb) {
            g_phys[i].base = end;
            g_phys[i].size = re - end;
            i++;
            continue;
        }

        if (end >= re) {
            g_phys[i].size = base - rb;
            i++;
            continue;
        }

        if (g_phys_count >= MAX_PHYS_RANGES) return 0;
        memmove(
            &g_phys[i + 2], &g_phys[i + 1],
            (g_phys_count - i - 1) * sizeof(g_phys[0]));
        g_phys[i].size = base - rb;
        g_phys[i + 1].base = end;
        g_phys[i + 1].size = re - end;
        g_phys_count++;
        return 1;
    }
    return 1;
}

static int alias_phys_info(
        uint64_t addr, uint64_t *base, uint64_t *size,
        uint32_t *state, uint32_t *perm) {
    if (!g_alias_region) return 0;

    uint64_t ab = (uint64_t)(uintptr_t)g_alias_region;
    uint64_t ae = ab + g_alias_region_size;
    if (addr < ab || addr >= ae) return 0;

    int k = phys_find_containing(addr);
    if (k >= 0) {
        *base = g_phys[k].base;
        *size = g_phys[k].size;
        *state = 6; // Normal
        *perm = 3;  // RW
        return 1;
    }

    uint64_t lo = ab;
    uint64_t hi = ae;
    for (unsigned i = 0; i < g_phys_count; ++i) {
        uint64_t rb = g_phys[i].base;
        uint64_t re = rb + g_phys[i].size;
        if (re <= addr && re > lo) lo = re;
        if (rb > addr && rb < hi) {
            hi = rb;
            break;
        }
    }
    *base = lo;
    *size = hi - lo;
    *state = 0;
    *perm = 0;
    return 1;
}

static int map_physical(uint64_t addr, uint64_t size) {
    if (!g_alias_region || !size || (addr & 0xfff) || (size & 0xfff)) return 0;

    uint64_t ab = (uint64_t)(uintptr_t)g_alias_region;
    uint64_t ae = ab + g_alias_region_size;
    if (addr < ab || addr + size < addr || addr + size > ae) return 0;

    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uintptr_t lo = align_down_ptr((uintptr_t)addr, page);
    uintptr_t hi = align_up((size_t)(addr + size), page);

    if (mprotect((void *)lo, hi - lo, PROT_READ | PROT_WRITE) != 0) return 0;
    if (!phys_add(addr, size)) return 0;
    return 1;
}

static int unmap_physical(uint64_t addr, uint64_t size) {
    if (!g_alias_region || !size || (addr & 0xfff) || (size & 0xfff)) return 0;

    uint64_t ab = (uint64_t)(uintptr_t)g_alias_region;
    uint64_t ae = ab + g_alias_region_size;
    if (addr < ab || addr + size < addr || addr + size > ae) return 0;

    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uintptr_t lo = align_up((size_t)addr, page);
    uintptr_t hi = align_down_ptr((uintptr_t)(addr + size), page);

    if (hi > lo) {
        madvise((void *)lo, hi - lo, MADV_DONTNEED);
        if (mprotect((void *)lo, hi - lo, PROT_NONE) != 0) return 0;
    }

    return phys_remove(addr, size);
}

static void classify_query_memory(
        uint64_t addr,
        uint64_t *base, uint64_t *size,
        uint32_t *state, uint32_t *perm) {
    *base = addr & ~0xfffULL;
    *size = 0x1000;
    *state = 0;  // Free
    *perm = 0;

    if (g_guest_stack
            && range_contains(
                (uint64_t)(uintptr_t)g_guest_stack,
                GUEST_STACK_SIZE, addr)) {
        *base = (uint64_t)(uintptr_t)g_guest_stack;
        *size = GUEST_STACK_SIZE;
        *state = 0x0b;  // Stack
        *perm = 3;      // RW
        return;
    }

    if (g_guest_tls
            && range_contains((uint64_t)(uintptr_t)g_guest_tls, 0x4000, addr)) {
        *base = (uint64_t)(uintptr_t)g_guest_tls;
        *size = 0x4000;
        *state = 0x0c;  // ThreadLocal
        *perm = 3;
        return;
    }

    if (g_guest_tlr
            && range_contains((uint64_t)(uintptr_t)g_guest_tlr, 0x4000, addr)) {
        *base = (uint64_t)(uintptr_t)g_guest_tlr;
        *size = 0x4000;
        *state = 0x0c;
        *perm = 3;
        return;
    }

    for (int i = 0; i < g_nmod; ++i) {
        Module *m = &g_mod[i];
        uint64_t mb = (uint64_t)(uintptr_t)m->base;
        if (!range_contains(mb, m->map_size, addr)) continue;

        uint64_t off = addr - mb;
        uint64_t ro = m->ro_off;
        uint64_t da = m->data_off;

        if (off < ro) {
            *base = mb;
            *size = ro;
            *state = 3;  // Code
            *perm = 5;   // RX
        } else if (off < da) {
            *base = mb + ro;
            *size = da - ro;
            *state = 3;  // Code
            *perm = 1;   // R
        } else {
            *base = mb + da;
            *size = m->map_size - da;
            *state = 4;  // CodeData
            *perm = 3;   // RW
        }
        return;
    }

    if (g_heap_region
            && range_contains(
                (uint64_t)(uintptr_t)g_heap_region,
                g_heap_region_size, addr)) {
        uint64_t hb = (uint64_t)(uintptr_t)g_heap_region;
        if (addr < hb + g_heap_current) {
            *base = hb;
            *size = g_heap_current;
            *state = 5;  // Heap
            *perm = 3;
        } else {
            *base = hb + g_heap_current;
            *size = g_heap_region_size - g_heap_current;
            *state = 0;
            *perm = 0;
        }
        return;
    }

    if (alias_phys_info(addr, base, size, state, perm)) {
        return;
    }

    if (g_stack_region
            && range_contains(
                (uint64_t)(uintptr_t)g_stack_region,
                g_stack_region_size, addr)) {
        *base = (uint64_t)(uintptr_t)g_stack_region;
        *size = g_stack_region_size;
        *state = 0;
        *perm = 0;
        return;
    }

    if (g_stub_page
            && range_contains(
                (uint64_t)(uintptr_t)g_stub_page,
                g_stub_map_size, addr)) {
        *base = (uint64_t)(uintptr_t)g_stub_page;
        *size = g_stub_map_size;
        *state = 4; // CodeData
        *perm = 5;  // RX
        return;
    }
}

static int emulate_query_memory(ucontext_t *uc) {
#if defined(__aarch64__)
    uint64_t out_info = uc->uc_mcontext.regs[0];
    uint64_t out_page = uc->uc_mcontext.regs[1];
    uint64_t addr = uc->uc_mcontext.regs[2];

    if (!out_info) return 0;

    uint64_t base = 0, size = 0;
    uint32_t state = 0, perm = 0;
    classify_query_memory(addr, &base, &size, &state, &perm);

    HorizonMemoryInfo mi;
    memset(&mi, 0, sizeof(mi));
    mi.base_address = base;
    mi.size = size;
    mi.state = state;
    mi.permission = perm;

    memcpy((void *)(uintptr_t)out_info, &mi, sizeof(mi));
    if (out_page) {
        uint32_t page_flags = 0;
        memcpy((void *)(uintptr_t)out_page, &page_flags, sizeof(page_flags));
    }

    // Horizon QueryMemory returns Result in x0 and PageInfo flags in x1.
    uc->uc_mcontext.regs[0] = 0;
    uc->uc_mcontext.regs[1] = 0;
    return 1;
#else
    (void)uc;
    return 0;
#endif
}

static int emulate_get_info(ucontext_t *uc) {
#if defined(__aarch64__)
    uint64_t id = uc->uc_mcontext.regs[1];
    uint64_t subid = uc->uc_mcontext.regs[3];
    uint64_t value = 0;
    int handled = 1;

    if (id < 64) g_getinfo_hits[id]++;

    switch (id) {
    case 0:   // CoreMask: this title's NPDM declares cores 0..5.
        value = 0x3f;
        break;
    case 2:   // AliasRegionAddress
        if (g_alias_region) value = (uint64_t)(uintptr_t)g_alias_region;
        else handled = 0;
        break;
    case 3:   // AliasRegionSize
        if (g_alias_region) value = g_alias_region_size;
        else handled = 0;
        break;
    case 4:   // HeapRegionAddress
        if (g_heap_region) value = (uint64_t)(uintptr_t)g_heap_region;
        else handled = 0;
        break;
    case 5:   // HeapRegionSize
        if (g_heap_region) value = g_heap_region_size;
        else handled = 0;
        break;
    case 6:   // TotalMemorySize
        value = HEAP_REGION_SIZE;
        break;
    case 7:   // UsedMemorySize
        value = guest_used_physical_memory();
        break;
    case 8:   // DebuggerAttached
        value = 0;
        break;
    case 11: { // RandomEntropy
        static const uint64_t entropy[4] = {
            0x4e58325354473143ULL,
            0x4e58325354473243ULL,
            0x4e58325354473343ULL,
            0x4e58325354473443ULL,
        };
        if (subid < 4) value = entropy[subid];
        else handled = 0;
        break;
    }
    case 12:  // AslrRegionAddress
        if (g_aslr_region_size) value = g_aslr_region_addr;
        else handled = 0;
        break;
    case 13:  // AslrRegionSize
        if (g_aslr_region_size) value = g_aslr_region_size;
        else handled = 0;
        break;
    case 14:  // StackRegionAddress
        if (g_stack_region) value = (uint64_t)(uintptr_t)g_stack_region;
        else handled = 0;
        break;
    case 15:  // StackRegionSize
        if (g_stack_region) value = g_stack_region_size;
        else handled = 0;
        break;
    case 16:  // SystemResourceSizeTotal: NPDM declared zero.
    case 17:  // SystemResourceSizeUsed
        value = 0;
        break;
    case 18:  // ProgramId
        value = EXPECT_TITLE_ID;
        break;
    case 19:  // InitialProcessIdRange
        if (subid == 0) value = 0;
        else if (subid == 1) value = 64;
        else handled = 0;
        break;
    case 20:  // UserExceptionContextAddress
        value = (uint64_t)(uintptr_t)g_user_exception_ctx;
        break;
    case 21:  // TotalNonSystemMemorySize; NPDM system-resource size is zero.
        value = HEAP_REGION_SIZE;
        break;
    case 22:  // UsedNonSystemMemorySize
        value = guest_used_physical_memory();
        break;
    case 23:  // IsApplication
        value = 1;
        break;
    case 24:  // FreeThreadCount: conservative application limit before guest threads exist.
        value = 96;
        break;
    case 28:  // AliasRegionExtraSize
        value = 0;
        break;
    case 29:  // Switch 2 VAMM/NX2 region address
        if (g_nx2_region) value = (uint64_t)(uintptr_t)g_nx2_region;
        else handled = 0;
        break;
    case 30:  // Switch 2 VAMM/NX2 region size
        if (g_nx2_region) value = g_nx2_region_size;
        else handled = 0;
        break;
    case 31:  // Switch 2 remote-memory size (not backed yet)
    case 32:  // Switch 2 remote-memory used
        value = 0;
        break;
    case 33:  // Switch 2 VAMM page granule
        value = 0x1000;
        break;
    default:
        handled = 0;
        break;
    }

    if (!handled) return 0;

    // Horizon GetInfo ABI: x0 = ResultSuccess, x1 = returned value.
    uc->uc_mcontext.regs[0] = 0;
    uc->uc_mcontext.regs[1] = value;
    return 1;
#else
    (void)uc;
    return 0;
#endif
}

static Module *module_for_pc(uint64_t pc, uint64_t *off) {
    for (int i = 0; i < g_nmod; ++i) {
        Module *m = &g_mod[i];
        uint64_t lo = (uint64_t)m->base;
        uint64_t hi = lo + m->map_size;
        if (pc >= lo && pc < hi) {
            if (off) *off = pc - lo;
            return m;
        }
    }
    return NULL;
}

static int word_at_pc(uint64_t pc, uint32_t *out) {
    uint64_t off;
    Module *m = module_for_pc(pc, &off);
    if (m && off + 4 <= m->map_size) {
        memcpy(out, (void *)pc, 4);
        return 1;
    }

    uint64_t slo = (uint64_t)g_stub_page;
    uint64_t shi = slo + g_stub_map_size;
    if (pc >= slo && pc + 4 <= shi) {
        memcpy(out, (void *)pc, 4);
        return 1;
    }
    return 0;
}

static void stop_now(int type, int sig, int arg, uint64_t pc, uint64_t fault, uint32_t insn) {
    g_stop.type = type;
    g_stop.sig = sig;
    g_stop.arg = arg;
    g_stop.pc = pc;
    g_stop.fault = fault;
    g_stop.insn = insn;
    g_guest_active = 0;
    siglongjmp(g_jmp, 1);
}

static void guest_signal_handler(int sig, siginfo_t *si, void *ctx) {
    if (!g_guest_active) _exit(180 + sig);

#if defined(__aarch64__)
    ucontext_t *uc = (ucontext_t *)ctx;
    uint64_t pc = uc->uc_mcontext.pc;
    uint32_t insn = 0;
    uint64_t trap_pc = pc;

    if (!word_at_pc(pc, &insn)
            || (insn & 0xFFE0001F) != 0xD4200000u) {
        if (pc >= 4 && word_at_pc(pc - 4, &insn)
                && (insn & 0xFFE0001F) == 0xD4200000u) {
            trap_pc = pc - 4;
        } else {
            stop_now(4, sig, 0, pc, (uint64_t)(uintptr_t)si->si_addr, insn);
        }
    }

    unsigned imm = (insn >> 5) & 0xffffu;
    unsigned reg = imm & 31u;
    uint64_t resume = trap_pc + 4;

    if ((imm & 0xffe0u) == TR_TPIDR_R) {
        uc->uc_mcontext.regs[reg] = (uint64_t)g_guest_tls;
        uc->uc_mcontext.pc = resume;
        return;
    }
    if ((imm & 0xffe0u) == TR_TPIDR_W) {
        g_guest_tls = (uint8_t *)(uintptr_t)uc->uc_mcontext.regs[reg];
        uc->uc_mcontext.pc = resume;
        return;
    }
    if ((imm & 0xffe0u) == TR_TPIDRRO_R) {
        uc->uc_mcontext.regs[reg] = (uint64_t)g_guest_tlr;
        uc->uc_mcontext.pc = resume;
        return;
    }
    if ((imm & 0xffe0u) == TR_CNTPCT
            || (imm & 0xffe0u) == TR_CNTVCT) {
        uc->uc_mcontext.regs[reg] = switch2_counter();
        uc->uc_mcontext.pc = resume;
        return;
    }
    if ((imm & 0xffe0u) == TR_CNTFRQ) {
        uc->uc_mcontext.regs[reg] = 31250000ULL;
        uc->uc_mcontext.pc = resume;
        return;
    }
    if ((imm & 0xffe0u) == TR_CTR) {
        // The SDK uses CTR_EL0 here for cache line geometry. Report 64-byte I/D lines.
        uc->uc_mcontext.regs[reg] = 0x000000008444c004ULL;
        uc->uc_mcontext.pc = resume;
        return;
    }
    if ((imm & 0xf000u) == TR_SVC) {
        unsigned svc = imm & 0x0fffu;

        if (svc < 256) g_svc_hits[svc]++;
        for (int i = 0; i < 8; ++i) g_stop.regs[i] = uc->uc_mcontext.regs[i];

        if (svc == 0x06) { // QueryMemory
            if (emulate_query_memory(uc)) {
                uc->uc_mcontext.pc = resume;
                return;
            }
            stop_now(7, sig, 0, trap_pc, uc->uc_mcontext.regs[2], insn);
        }
        if (svc == 0x0c) { // GetThreadPriority
            uint64_t handle = uc->uc_mcontext.regs[1];
            if (handle == 1 || handle == 0xffff8000ULL) {
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.regs[1] = (uint64_t)(uint32_t)g_main_thread_priority;
                uc->uc_mcontext.pc = resume;
                return;
            }
        }
        if (svc == 0x0d) { // SetThreadPriority
            uint64_t handle = uc->uc_mcontext.regs[0];
            int32_t prio = (int32_t)uc->uc_mcontext.regs[1];
            if (handle == 1 || handle == 0xffff8000ULL) {
                g_main_thread_priority = prio;
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.pc = resume;
                return;
            }
        }
        if (svc == 0x0e) { // GetThreadCoreMask
            uint64_t handle = uc->uc_mcontext.regs[2];
            if (handle == 1 || handle == 0xffff8000ULL) {
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.regs[1] = (uint64_t)(uint32_t)g_main_thread_core;
                uc->uc_mcontext.regs[2] = g_main_thread_mask;
                uc->uc_mcontext.pc = resume;
                return;
            }
        }
        if (svc == 0x0f) { // SetThreadCoreMask
            uint64_t handle = uc->uc_mcontext.regs[0];
            int32_t core = (int32_t)uc->uc_mcontext.regs[1];
            uint64_t mask = uc->uc_mcontext.regs[2];

            if (handle == 1 || handle == 0xffff8000ULL) {
                if (core == -2) {
                    g_main_thread_core = 0;
                    g_main_thread_mask = 1;
                } else {
                    if (core >= 0) g_main_thread_core = core;
                    if (mask) g_main_thread_mask = mask;
                    if (g_main_thread_mask
                            && (g_main_thread_core < 0
                                || !(g_main_thread_mask >> g_main_thread_core & 1))) {
                        g_main_thread_core = __builtin_ctzll(g_main_thread_mask);
                    }
                }
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.pc = resume;
                return;
            }
        }
        if (svc == 0x10) { // GetCurrentProcessorNumber
            uc->uc_mcontext.regs[0] =
                (uint64_t)(uint32_t)(g_main_thread_core >= 0
                    ? g_main_thread_core
                    : g_main_thread_mask
                        ? __builtin_ctzll(g_main_thread_mask)
                        : 0);
            uc->uc_mcontext.pc = resume;
            return;
        }
        if (svc == 0x25) { // GetThreadId
            uint64_t handle = uc->uc_mcontext.regs[1];
            if (handle == 1 || handle == 0xffff8000ULL) {
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.regs[1] = g_main_thread_id;
                uc->uc_mcontext.pc = resume;
                return;
            }
        }
        if (svc == 0x0b) { // SleepThread: bootstrap-safe yield/sleep shim
            // The controlled bootstrap is single-threaded. Returning immediately is
            // sufficient until actual guest thread creation is enabled.
            uc->uc_mcontext.pc = resume;
            return;
        }
        if (svc == 0x24) { // GetProcessId
            uint64_t handle = uc->uc_mcontext.regs[1];
            if (handle == 0xffff8001ULL || handle == 0xffff8000ULL || handle == 1) {
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.regs[1] = 1;
                uc->uc_mcontext.pc = resume;
                return;
            }
        }
        if (svc == 0x16) { // CloseHandle
            uint32_t handle = (uint32_t)uc->uc_mcontext.regs[0];
            if (handle == SM_SESSION_HANDLE || ipc_find(handle)) {
                ipc_close(handle);
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.pc = resume;
                return;
            }
        }
        if (svc == 0x1f) { // ConnectToNamedPort
            char port[16];
            memset(port, 0, sizeof(port));
            if (!guest_copy_cstring(port, sizeof(port), uc->uc_mcontext.regs[1])) {
                stop_now(9, sig, 0x1f, trap_pc, uc->uc_mcontext.regs[1], insn);
            }

            if (!strcmp(port, "sm:")) {
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.regs[1] = SM_SESSION_HANDLE;
                uc->uc_mcontext.pc = resume;
                return;
            }

            IpcParsed none = {0};
            uint32_t *b = ipc_command_buffer();
            ipc_capture_stop(port, &none, b);
            stop_now(9, sig, 0x1f, trap_pc, uc->uc_mcontext.regs[1], insn);
        }
        if (svc == 0x21) { // SendSyncRequest
            uint32_t handle = (uint32_t)uc->uc_mcontext.regs[0];
            uint32_t *b = ipc_command_buffer();
            IpcParsed p;
            if (!ipc_parse_request(b, &p)) {
                ipc_capture_stop("malformed", NULL, b);
                stop_now(9, sig, 0x21, trap_pc, handle, insn);
            }

            if (p.type == 5 || p.type == 7) { // Control / ControlWithContext
                if (ipc_control_request(handle, b, &p)) {
                    uc->uc_mcontext.regs[0] = 0;
                    uc->uc_mcontext.pc = resume;
                    return;
                }
            }

            if (handle == SM_SESSION_HANDLE) {
                if (ipc_sm_request(b, &p)) {
                    uc->uc_mcontext.regs[0] = 0;
                    uc->uc_mcontext.pc = resume;
                    return;
                }
                ipc_capture_stop("sm:", &p, b);
                stop_now(9, sig, 0x21, trap_pc, handle, insn);
            }

            IpcSession *session = ipc_find(handle);
            if (session) {
                if (ipc_service_request(session, b, &p)) {
                    uc->uc_mcontext.regs[0] = 0;
                    uc->uc_mcontext.pc = resume;
                    return;
                }
                ipc_capture_stop(session->name, &p, b);
                stop_now(9, sig, 0x21, trap_pc, handle, insn);
            }

            ipc_capture_stop("bad-handle", &p, b);
            stop_now(9, sig, 0x21, trap_pc, handle, insn);
        }
        if (svc == 0x2c) { // MapPhysicalMemory
            uint64_t addr = uc->uc_mcontext.regs[0];
            uint64_t size = uc->uc_mcontext.regs[1];
            if (map_physical(addr, size)) {
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.pc = resume;
                return;
            }
            stop_now(8, sig, 0x2c, trap_pc, addr, insn);
        }
        if (svc == 0x2d) { // UnmapPhysicalMemory
            uint64_t addr = uc->uc_mcontext.regs[0];
            uint64_t size = uc->uc_mcontext.regs[1];
            if (unmap_physical(addr, size)) {
                uc->uc_mcontext.regs[0] = 0;
                uc->uc_mcontext.pc = resume;
                return;
            }
            stop_now(8, sig, 0x2d, trap_pc, addr, insn);
        }
        if (svc == 0x01) { // SetHeapSize
            size_t requested = (size_t)uc->uc_mcontext.regs[1];
            size_t page = (size_t)sysconf(_SC_PAGESIZE);
            size_t old_aligned = align_up(g_heap_current, page);
            size_t new_aligned = align_up(requested, page);

            if (!g_heap_region || requested > g_heap_region_size) {
                stop_now(6, sig, 1, trap_pc, requested, insn);
            }

            if (new_aligned > old_aligned) {
                if (mprotect(
                        g_heap_region + old_aligned,
                        new_aligned - old_aligned,
                        PROT_READ | PROT_WRITE) != 0) {
                    stop_now(6, sig, 2, trap_pc, requested, insn);
                }
            } else if (new_aligned < old_aligned) {
                madvise(
                    g_heap_region + new_aligned,
                    old_aligned - new_aligned,
                    MADV_DONTNEED);
                if (mprotect(
                        g_heap_region + new_aligned,
                        old_aligned - new_aligned,
                        PROT_NONE) != 0) {
                    stop_now(6, sig, 3, trap_pc, requested, insn);
                }
            }

            g_heap_current = requested;
            uc->uc_mcontext.regs[0] = 0;
            uc->uc_mcontext.regs[1] = (uint64_t)(uintptr_t)g_heap_region;
            uc->uc_mcontext.pc = resume;
            return;
        }
        if (svc == 0x1e) { // GetSystemTick
            uc->uc_mcontext.regs[0] = 0;
            uc->uc_mcontext.regs[1] = switch2_counter();
            uc->uc_mcontext.pc = resume;
            return;
        }
        if (svc == 0x27) { // OutputDebugString: no-op in the headless harness.
            uc->uc_mcontext.regs[0] = 0;
            uc->uc_mcontext.pc = resume;
            return;
        }
        if (svc == 0x29) { // GetInfo
            if (emulate_get_info(uc)) {
                uc->uc_mcontext.pc = resume;
                return;
            }
            stop_now(5, sig, (int)uc->uc_mcontext.regs[1], trap_pc, 0, insn);
        }
        if (svc == 0x2a || svc == 0x2b) { // cache maintenance
            uc->uc_mcontext.regs[0] = 0;
            uc->uc_mcontext.pc = resume;
            return;
        }

        stop_now(1, sig, (int)svc, trap_pc, 0, insn);
    }
    if ((imm & 0x8000u) == TR_IMPORT) {
        stop_now(2, sig, imm & 0x7fffu, trap_pc, 0, insn);
    }

    stop_now(3, sig, (int)imm, trap_pc, 0, insn);
#else
    stop_now(4, sig, 0, 0, (uint64_t)(uintptr_t)si->si_addr, 0);
#endif
}

typedef struct {
    struct sigaction old_trap;
    struct sigaction old_segv;
    struct sigaction old_ill;
    struct sigaction old_bus;
    stack_t old_stack;
    void *alt_mem;
} SignalGuard;

static int signal_guard_install(SignalGuard *g) {
    memset(g, 0, sizeof(*g));
    g->alt_mem = malloc(128 * 1024);
    if (!g->alt_mem) return 0;

    stack_t ss = {
        .ss_sp = g->alt_mem,
        .ss_size = 128 * 1024,
        .ss_flags = 0
    };
    if (sigaltstack(&ss, &g->old_stack) != 0) return 0;

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sigemptyset(&sa.sa_mask);
    sa.sa_sigaction = guest_signal_handler;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;

    if (sigaction(SIGTRAP, &sa, &g->old_trap) != 0) return 0;
    if (sigaction(SIGSEGV, &sa, &g->old_segv) != 0) return 0;
    if (sigaction(SIGILL, &sa, &g->old_ill) != 0) return 0;
    if (sigaction(SIGBUS, &sa, &g->old_bus) != 0) return 0;
    return 1;
}

static void signal_guard_remove(SignalGuard *g) {
    sigaction(SIGTRAP, &g->old_trap, NULL);
    sigaction(SIGSEGV, &g->old_segv, NULL);
    sigaction(SIGILL, &g->old_ill, NULL);
    sigaction(SIGBUS, &g->old_bus, NULL);
    sigaltstack(&g->old_stack, NULL);
    free(g->alt_mem);
}

static void report_stop(Out *o) {
    uint64_t off = 0;
    Module *m = module_for_pc(g_stop.pc, &off);
    const char *mn = m ? m->name : "host/stub";

    add(o, "STOP phase=%s\n",
        (g_phase >= 0 && g_phase < (int)(sizeof(PHASES)/sizeof(PHASES[0])))
            ? PHASES[g_phase] : "?");
    add(o, "stop_pc=%s+0x%" PRIx64 " absolute=0x%" PRIx64 "\n",
        mn, off, g_stop.pc);

    switch (g_stop.type) {
    case 1:
        add(o, "FIRST_DEPENDENCY=svc 0x%x\n", g_stop.arg);
        add(o,
            "svc_regs x0=0x%" PRIx64 " x1=0x%" PRIx64
            " x2=0x%" PRIx64 " x3=0x%" PRIx64
            " x4=0x%" PRIx64 " x5=0x%" PRIx64
            " x6=0x%" PRIx64 " x7=0x%" PRIx64 "\n",
            g_stop.regs[0], g_stop.regs[1], g_stop.regs[2], g_stop.regs[3],
            g_stop.regs[4], g_stop.regs[5], g_stop.regs[6], g_stop.regs[7]);
        break;
    case 5:
        add(o, "FIRST_DEPENDENCY=GetInfo id=%d\n", g_stop.arg);
        add(o,
            "getinfo_regs x0=0x%" PRIx64 " x1(id)=0x%" PRIx64
            " x2(handle)=0x%" PRIx64 " x3(subid)=0x%" PRIx64 "\n",
            g_stop.regs[0], g_stop.regs[1], g_stop.regs[2], g_stop.regs[3]);
        break;
    case 6:
        add(o,
            "FIRST_DEPENDENCY=SetHeapSize host_mapping_failure stage=%d "
            "requested=0x%" PRIx64 " errno=%d\n",
            g_stop.arg, g_stop.fault, errno);
        break;
    case 7:
        add(o,
            "FIRST_DEPENDENCY=QueryMemory host_write_failure "
            "address=0x%" PRIx64 "\n",
            g_stop.fault);
        break;
    case 8:
        add(o,
            "FIRST_DEPENDENCY=%s host_mapping_failure address=0x%" PRIx64 "\n",
            g_stop.arg == 0x2c ? "MapPhysicalMemory" : "UnmapPhysicalMemory",
            g_stop.fault);
        break;
    case 9:
        add(o,
            "FIRST_DEPENDENCY=IPC service=%s command=%u type=0x%x\n",
            g_ipc_stop_service, g_ipc_stop_command, g_ipc_stop_type);
        add(o, "ipc_words:");
        for (int i = 0; i < 16; ++i) add(o, " %08x", g_ipc_stop_words[i]);
        add(o, "\n");
        break;
    case 2:
        if (g_stop.arg >= 0 && (unsigned)g_stop.arg < g_nimport) {
            add(o, "FIRST_DEPENDENCY=unresolved_import %s\n",
                g_import_name[g_stop.arg]);
        } else {
            add(o, "FIRST_DEPENDENCY=unresolved_import index=%d\n", g_stop.arg);
        }
        break;
    case 3:
        add(o, "FIRST_DEPENDENCY=unexpected_brk 0x%x\n", g_stop.arg);
        break;
    default:
        add(o,
            "FIRST_DEPENDENCY=signal %d fault=0x%" PRIx64
            " instruction=0x%08x\n",
            g_stop.sig, g_stop.fault, g_stop.insn);
        break;
    }
}

static int execute_phase(
        Out *o, int phase, const char *symbol,
        uint64_t a0, uint64_t a1) {
    void *fn = guest_symbol(symbol);
    add(o, "\nphase_%s_symbol=%p\n", symbol, fn);
    if (!fn) {
        add(o, "BLOCKED missing_guest_symbol=%s\n", symbol);
        return 0;
    }

    memset(&g_stop, 0, sizeof(g_stop));
    g_phase = phase;

    if (sigsetjmp(g_jmp, 1) == 0) {
        g_guest_active = 1;
        uint64_t ret = nx2_guest_call(fn, a0, a1, g_guest_stack_top);
        g_guest_active = 0;
        add(o, "phase_%s_returned=0x%" PRIx64 "\n", symbol, ret);
        return 1;
    }

    report_stop(o);
    return 0;
}

static int execute_init(Out *o, Module *m) {
    if (!m->init) return 1;

    void *fn = m->base + m->init;
    memset(&g_stop, 0, sizeof(g_stop));
    g_phase = 7;

    add(o, "\nphase_DT_INIT_%s=%p\n", m->name, fn);
    if (sigsetjmp(g_jmp, 1) == 0) {
        g_guest_active = 1;
        uint64_t ret = nx2_guest_call(fn, 0, 0, g_guest_stack_top);
        g_guest_active = 0;
        add(o, "phase_DT_INIT_%s_returned=0x%" PRIx64 "\n", m->name, ret);
        return 1;
    }

    report_stop(o);
    return 0;
}

static void cleanup(void) {
    for (int i = 0; i < g_nmod; ++i) {
        if (g_mod[i].base) munmap(g_mod[i].base, g_mod[i].map_size);
    }
    g_nmod = 0;

    if (g_stub_page) munmap(g_stub_page, g_stub_map_size);
    g_stub_page = NULL;
    g_stub_map_size = 0;

    for (unsigned i = 0; i < g_nimport; ++i) {
        free(g_import_name[i]);
        g_import_name[i] = NULL;
    }
    g_nimport = 0;

    if (g_guest_stack) munmap(g_guest_stack, GUEST_STACK_SIZE);
    g_guest_stack = NULL;
    g_guest_stack_top = NULL;

    if (g_heap_region) munmap(g_heap_region, g_heap_region_size);
    g_heap_region = NULL;
    g_heap_region_size = 0;
    g_heap_current = 0;

    if (g_alias_region) munmap(g_alias_region, g_alias_region_size);
    g_alias_region = NULL;
    g_alias_region_size = 0;

    if (g_stack_region) munmap(g_stack_region, g_stack_region_size);
    g_stack_region = NULL;
    g_stack_region_size = 0;

    g_nx2_region = NULL;
    g_nx2_region_size = 0;
    g_aslr_region_addr = 0;
    g_aslr_region_size = 0;

    free(g_guest_tls);
    free(g_guest_tlr);
    g_guest_tls = NULL;
    g_guest_tlr = NULL;
}

JNIEXPORT jstring JNICALL
Java_org_nx2a_stage1c_MainActivity_runBootstrap(
        JNIEnv *env, jobject thiz,
        jint main_fd, jlong main_size,
        jint sdk_fd, jlong sdk_size,
        jint rtld_fd, jlong rtld_size,
        jint npdm_fd, jlong npdm_size) {
    (void)thiz;

    cleanup();

    g_main_thread_priority = 44;
    g_main_thread_core = 0;
    g_main_thread_mask = 0x3f;
    g_main_thread_id = 1;
    memset(g_ipc_sessions, 0, sizeof(g_ipc_sessions));
    g_ipc_session_count = 0;
    g_next_ipc_handle = 0x200;
    g_sm_initialized = 0;
    g_pointer_buffer_size = 0x10000;
    memset(g_ipc_stop_service, 0, sizeof(g_ipc_stop_service));
    g_ipc_stop_command = 0;
    g_ipc_stop_type = 0;
    memset(g_ipc_stop_words, 0, sizeof(g_ipc_stop_words));

    char *report = (char *)calloc(1, REPORT_CAP);
    if (!report) {
        return (*env)->NewStringUTF(env, "Stage 1C report allocation failed");
    }
    Out o = {report, REPORT_CAP};

    add(&o, "NX2 Switch 2 Native Stage 1C Controlled Bootstrap\n");
    add(&o, "harness_build=9\n");
    add(&o, "guest_execution=yes\n");
    add(&o, "graphics=no\n");
    add(&o, "romfs=no\n");
    add(&o, "host_loader_replaces_rtld=yes\n");
    add(&o, "title_lock=0x%016" PRIx64 "\n\n", EXPECT_TITLE_ID);

    int identity =
        check_npdm(&o, npdm_fd, (int64_t)npdm_size)
        && check_nso_identity(
            &o, main_fd, (int64_t)main_size, "main", EXPECT_MAIN_BUILD)
        && check_nso_identity(
            &o, sdk_fd, (int64_t)sdk_size, "sdk", EXPECT_SDK_BUILD)
        && check_nso_identity(
            &o, rtld_fd, (int64_t)rtld_size, "rtld", EXPECT_RTLD_BUILD);

    if (!identity) {
        add(&o, "\nSTAGE1C_RESULT=REFUSED_IDENTITY_MISMATCH\n");
        jstring s = (*env)->NewStringUTF(env, report);
        free(report);
        return s;
    }

    add(&o, "identity_gate=PASS\n");
    add(&o, "rtld_policy=validated_only_not_executed\n");

    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    g_stub_map_size = align_up(STUB_BYTES, page);
    g_stub_page = (uint8_t *)mmap(
        NULL, g_stub_map_size,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS,
        -1, 0);
    if (g_stub_page == MAP_FAILED) {
        g_stub_page = NULL;
        add(&o, "BLOCKED import_stub_mmap errno=%d\n", errno);
        goto done;
    }

    g_nmod = 2;
    if (!load_module(
            &o, &g_mod[0], "main",
            main_fd, (int64_t)main_size, EXPECT_MAIN_BUILD)
            || !load_module(
            &o, &g_mod[1], "sdk",
            sdk_fd, (int64_t)sdk_size, EXPECT_SDK_BUILD)) {
        add(&o, "STAGE1C_RESULT=BLOCKED_MODULE_LOAD\n");
        goto done;
    }

    if (!relocate_all(&o)) {
        add(&o, "STAGE1C_RESULT=BLOCKED_RELOCATION\n");
        goto done;
    }

    if (!seal_modules(&o)) {
        add(&o, "STAGE1C_RESULT=BLOCKED_MEMORY_PROTECTION\n");
        goto done;
    }

    add(&o, "module_seal=PASS\n");

    g_guest_tls = (uint8_t *)calloc(1, 0x4000);
    g_guest_tlr = (uint8_t *)calloc(1, 0x4000);
    g_guest_stack = (uint8_t *)mmap(
        NULL, GUEST_STACK_SIZE,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS | MAP_STACK,
        -1, 0);

    if (!g_guest_tls || !g_guest_tlr || g_guest_stack == MAP_FAILED) {
        if (g_guest_stack == MAP_FAILED) g_guest_stack = NULL;
        add(&o, "STAGE1C_RESULT=BLOCKED_GUEST_THREAD_MEMORY\n");
        goto done;
    }

    g_guest_stack_top =
        (void *)align_down_ptr(
            (uintptr_t)(g_guest_stack + GUEST_STACK_SIZE - 16), 16);

    // Horizon stores the current thread handle in the TLR at +0x110.
    *(uint32_t *)(g_guest_tlr + 0x110) = 1;

    if (!reserve_guest_geometry(&o)) {
        add(&o, "STAGE1C_RESULT=BLOCKED_GUEST_GEOMETRY\n");
        goto done;
    }

    memset((void *)g_getinfo_hits, 0, sizeof(g_getinfo_hits));
    memset((void *)g_svc_hits, 0, sizeof(g_svc_hits));
    g_phys_count = 0;

    add(&o, "guest_tls_seed=%p guest_tlr_seed=%p guest_stack_top=%p\n",
        g_guest_tls, g_guest_tlr, g_guest_stack_top);

    SignalGuard guard;
    if (!signal_guard_install(&guard)) {
        add(&o, "STAGE1C_RESULT=BLOCKED_SIGNAL_GUARD\n");
        goto done;
    }

    add(&o, "signal_guard=PASS\n");
    add(&o, "\n== controlled SDK bootstrap ==\n");

    int running = 1;
    running = running && execute_phase(&o, 1, "_init_libc0", 0, 0);
    running = running && execute_phase(&o, 2, "nnosInitialize", 1, 0);
    running = running && execute_phase(&o, 3, "_init_libc1", 0, 0);
    running = running && execute_phase(&o, 4, "nninitInitializeSdkModule", 0, 0);
    running = running && execute_phase(&o, 5, "nninitStartup", 0, 0);
    running = running && execute_phase(&o, 6, "_init_libc2", 0, 0);

    if (running) {
        for (int i = 0; i < g_nmod && running; ++i) {
            running = execute_init(&o, &g_mod[i]);
        }
    }

    if (running) {
        running = execute_phase(&o, 8, "nnMain", 0, 0);
    }

    signal_guard_remove(&guard);

    add(&o, "\nSVC hits:");
    int any_svc = 0;
    for (int i = 0; i < 256; ++i) {
        if (g_svc_hits[i]) {
            add(&o, " 0x%02x=%d", i, (int)g_svc_hits[i]);
            any_svc = 1;
        }
    }
    if (!any_svc) add(&o, " none");
    add(&o, "\n");

    add(&o, "ipc_sessions=%u sm_initialized=%s pointer_buffer=0x%x\n",
        g_ipc_session_count, g_sm_initialized ? "yes" : "no",
        g_pointer_buffer_size);
    for (unsigned i = 0; i < g_ipc_session_count && i < 12; ++i) {
        if (g_ipc_sessions[i].active) {
            add(&o, "  ipc_handle=0x%x service=%s\n",
                g_ipc_sessions[i].handle, g_ipc_sessions[i].name);
        }
    }

    add(&o, "physical_alias_ranges=%u mapped=0x%" PRIx64 "\n",
        g_phys_count, phys_mapped_total());
    add(&o, "accounted_used_memory=0x%" PRIx64 " total_memory=0x%llx\n",
        guest_used_physical_memory(), (unsigned long long)HEAP_REGION_SIZE);

    add(&o, "GetInfo hits:");
    int any_getinfo = 0;
    for (int i = 0; i < 64; ++i) {
        if (g_getinfo_hits[i]) {
            add(&o, " id%d=%d", i, (int)g_getinfo_hits[i]);
            any_getinfo = 1;
        }
    }
    if (!any_getinfo) add(&o, " none");
    add(&o, "\n");

    if (running) {
        add(&o, "\nSTAGE1C_RESULT=GUEST_RETURNED_THROUGH_ALL_PHASES\n");
        add(&o, "NEXT=This exceeded the expected bring-up milestone; add RomFS/HLE before further execution.\n");
    } else {
        add(&o, "\nSTAGE1C_RESULT=FIRST_GUEST_DEPENDENCY_CAPTURED\n");
        add(&o, "NEXT=Implement or emulate the reported first dependency, then repeat Stage 1C.\n");
    }

done:
    cleanup();

    jstring s = (*env)->NewStringUTF(env, report);
    free(report);
    return s;
}
