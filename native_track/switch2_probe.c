// SPDX-License-Identifier: MIT
// Switch 2 native Android feasibility probe.
// This contains no game code, keys, firmware, or copyrighted console data.

#include <android/log.h>
#include <android/native_activity.h>
#include <errno.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

#define TAG "NX2NativeProbe"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define GIB(x) ((size_t)(x) << 30)

static FILE* g_report;

static void line(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    if (g_report) {
        vfprintf(g_report, fmt, ap);
        fputc('\n', g_report);
        fflush(g_report);
    }
    va_end(ap);

    va_start(ap, fmt);
    __android_log_vprint(ANDROID_LOG_INFO, TAG, fmt, ap);
    va_end(ap);
}

static int test_va_reservation(void) {
    const size_t size = GIB(12);
    void* p = mmap(NULL, size, PROT_NONE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (p == MAP_FAILED) {
        line("FAIL address_space: 12 GiB PROT_NONE reservation: %s", strerror(errno));
        return 0;
    }
    line("PASS address_space: 12 GiB PROT_NONE reservation at %p", p);
    munmap(p, size);
    return 1;
}

typedef int (*probe_fn_t)(void);

static int test_exec_mapping(void) {
#if defined(__aarch64__)
    const uint32_t code[] = {
        0x52800540u,
        0xd65f03c0u
    };
    const size_t page = (size_t)sysconf(_SC_PAGESIZE);
    void* p = mmap(NULL, page, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) {
        line("FAIL exec_mapping: mmap RW: %s", strerror(errno));
        return 0;
    }
    memcpy(p, code, sizeof(code));
    __builtin___clear_cache((char*)p, (char*)p + sizeof(code));
    if (mprotect(p, page, PROT_READ | PROT_EXEC) != 0) {
        line("FAIL exec_mapping: mprotect RX: %s", strerror(errno));
        munmap(p, page);
        return 0;
    }
    const int got = ((probe_fn_t)p)();
    munmap(p, page);
    if (got != 42) {
        line("FAIL exec_mapping: ARM64 code returned %d, expected 42", got);
        return 0;
    }
    line("PASS exec_mapping: rewritten ARM64 code can execute from app memory");
    return 1;
#else
    line("FAIL exec_mapping: APK is not running as AArch64");
    return 0;
#endif
}

static void test_counter(void) {
#if defined(__aarch64__)
    uint64_t freq = 0;
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    line("INFO host_counter: cntfrq_el0=%" PRIu64 " Hz", freq);
    line("INFO switch2_guest_counter: runtime target=31250000 Hz (must be virtualized)");
#else
    line("FAIL host_counter: non-AArch64 process");
#endif
}

static int test_vulkan(void) {
    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "NX2 Native Probe",
        .applicationVersion = 1,
        .pEngineName = "NX2 Native",
        .engineVersion = 1,
        .apiVersion = VK_API_VERSION_1_1,
    };
    VkInstanceCreateInfo ci = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };
    VkInstance instance = VK_NULL_HANDLE;
    VkResult vr = vkCreateInstance(&ci, NULL, &instance);
    if (vr != VK_SUCCESS) {
        line("FAIL vulkan: vkCreateInstance=%d", (int)vr);
        return 0;
    }

    uint32_t count = 0;
    vr = vkEnumeratePhysicalDevices(instance, &count, NULL);
    if (vr != VK_SUCCESS || count == 0) {
        line("FAIL vulkan: no Vulkan physical device (%d, count=%u)", (int)vr, count);
        vkDestroyInstance(instance, NULL);
        return 0;
    }

    VkPhysicalDevice devs[8];
    if (count > 8) count = 8;
    vkEnumeratePhysicalDevices(instance, &count, devs);

    int pass = 0;
    for (uint32_t i = 0; i < count; ++i) {
        VkPhysicalDeviceProperties props;
        VkPhysicalDeviceFeatures features;
        VkPhysicalDeviceMemoryProperties mem;
        vkGetPhysicalDeviceProperties(devs[i], &props);
        vkGetPhysicalDeviceFeatures(devs[i], &features);
        vkGetPhysicalDeviceMemoryProperties(devs[i], &mem);

        uint64_t largest_heap = 0;
        for (uint32_t h = 0; h < mem.memoryHeapCount; ++h) {
            if (mem.memoryHeaps[h].size > largest_heap) largest_heap = mem.memoryHeaps[h].size;
        }

        line("INFO vulkan_device[%u]: %s api=%u.%u.%u largest_heap=%" PRIu64 " MiB",
             i, props.deviceName,
             VK_VERSION_MAJOR(props.apiVersion),
             VK_VERSION_MINOR(props.apiVersion),
             VK_VERSION_PATCH(props.apiVersion),
             largest_heap / (1024u * 1024u));
        line("%s vulkan_bc: textureCompressionBC=%s",
             features.textureCompressionBC ? "PASS" : "WARN",
             features.textureCompressionBC ? "yes" : "no");

        if (features.textureCompressionBC) pass = 1;
    }

    vkDestroyInstance(instance, NULL);
    return pass;
}

__attribute__((visibility("default")))
void ANativeActivity_onCreate(ANativeActivity* activity,
                              void* savedState,
                              size_t savedStateSize) {
    (void)savedState;
    (void)savedStateSize;

    char path[1024];
    const char* base = activity->externalDataPath ? activity->externalDataPath
                                                  : activity->internalDataPath;
    snprintf(path, sizeof(path), "%s/switch2-native-probe.txt",
             base ? base : "/data/local/tmp");

    g_report = fopen(path, "w");
    line("NX2 Switch 2 Native Android Probe");
    line("report=%s", path);
    line("abi=%s", sizeof(void*) == 8 ? "64-bit" : "32-bit");
    line("page_size=%ld", sysconf(_SC_PAGESIZE));
    line("goal: validate Android runtime assumptions before loading any user-owned Switch 2 executable");

    int score = 0;
    score += test_va_reservation();
    score += test_exec_mapping();
    test_counter();
    score += test_vulkan();

    line("RESULT core_checks=%d/3", score);
    if (score == 3) {
        line("READY stage1: device is suitable for the headless rewritten-ARM64 bootstrap experiment");
    } else {
        line("BLOCKED stage1: one or more required runtime assumptions failed");
    }

    if (g_report) {
        fclose(g_report);
        g_report = NULL;
    }
    LOGI("Probe complete: %s", path);
}
