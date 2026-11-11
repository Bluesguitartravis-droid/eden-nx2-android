// SPDX-License-Identifier: MIT
// Switch 2 native Android feasibility probe.
// This contains no game code, keys, firmware, or copyrighted console data.

#include <android/log.h>
#include <android/native_activity.h>
#include <errno.h>
#include <inttypes.h>
#include <jni.h>
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
static char g_text[16384];
static size_t g_text_len;

static void line(const char* fmt, ...) {
    char rendered[2048];

    va_list ap;
    va_start(ap, fmt);
    vsnprintf(rendered, sizeof(rendered), fmt, ap);
    va_end(ap);

    if (g_report) {
        fputs(rendered, g_report);
        fputc('\n', g_report);
        fflush(g_report);
    }

    const size_t n = strlen(rendered);
    if (g_text_len < sizeof(g_text) - 2) {
        const size_t room = sizeof(g_text) - g_text_len - 2;
        const size_t copy = n < room ? n : room;
        memcpy(g_text + g_text_len, rendered, copy);
        g_text_len += copy;
        g_text[g_text_len++] = '\n';
        g_text[g_text_len] = '\0';
    }

    __android_log_print(ANDROID_LOG_INFO, TAG, "%s", rendered);
}

static void copy_results_to_clipboard(ANativeActivity* activity) {
    JNIEnv* env = activity->env;
    if (env == NULL || activity->clazz == NULL) {
        return;
    }

    jclass context_class = (*env)->GetObjectClass(env, activity->clazz);
    jmethodID get_service = (*env)->GetMethodID(
        env,
        context_class,
        "getSystemService",
        "(Ljava/lang/String;)Ljava/lang/Object;"
    );
    jstring clipboard_name = (*env)->NewStringUTF(env, "clipboard");
    jobject clipboard = (*env)->CallObjectMethod(
        env,
        activity->clazz,
        get_service,
        clipboard_name
    );

    jclass clipdata_class = (*env)->FindClass(env, "android/content/ClipData");
    jmethodID new_plain_text = (*env)->GetStaticMethodID(
        env,
        clipdata_class,
        "newPlainText",
        "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Landroid/content/ClipData;"
    );
    jstring label = (*env)->NewStringUTF(env, "NX2 Switch 2 Native Probe");
    jstring text = (*env)->NewStringUTF(env, g_text);
    jobject clip = (*env)->CallStaticObjectMethod(
        env,
        clipdata_class,
        new_plain_text,
        label,
        text
    );

    if (clipboard != NULL && clip != NULL) {
        jclass clipboard_class = (*env)->GetObjectClass(env, clipboard);
        jmethodID set_primary_clip = (*env)->GetMethodID(
            env,
            clipboard_class,
            "setPrimaryClip",
            "(Landroid/content/ClipData;)V"
        );
        (*env)->CallVoidMethod(env, clipboard, set_primary_clip, clip);
        (*env)->DeleteLocalRef(env, clipboard_class);
    }

    jclass toast_class = (*env)->FindClass(env, "android/widget/Toast");
    jmethodID make_text = (*env)->GetStaticMethodID(
        env,
        toast_class,
        "makeText",
        "(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;"
    );
    jstring toast_message = (*env)->NewStringUTF(
        env,
        "Probe complete - results copied to clipboard. Paste them into ChatGPT."
    );
    jobject toast = (*env)->CallStaticObjectMethod(
        env,
        toast_class,
        make_text,
        activity->clazz,
        toast_message,
        1
    );
    if (toast != NULL) {
        jmethodID show = (*env)->GetMethodID(env, toast_class, "show", "()V");
        (*env)->CallVoidMethod(env, toast, show);
    }

    (*env)->DeleteLocalRef(env, clipboard_name);
    (*env)->DeleteLocalRef(env, clipboard);
    (*env)->DeleteLocalRef(env, clipdata_class);
    (*env)->DeleteLocalRef(env, label);
    (*env)->DeleteLocalRef(env, text);
    (*env)->DeleteLocalRef(env, clip);
    (*env)->DeleteLocalRef(env, toast_class);
    (*env)->DeleteLocalRef(env, toast_message);
    (*env)->DeleteLocalRef(env, toast);
    (*env)->DeleteLocalRef(env, context_class);
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

    g_text_len = 0;
    g_text[0] = '\0';
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

    copy_results_to_clipboard(activity);
    LOGI("Probe complete: %s", path);
}
