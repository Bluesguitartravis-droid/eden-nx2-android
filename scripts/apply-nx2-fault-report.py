#!/usr/bin/env python3
"""Android arm64: when the emulator crashes in host (non-JIT) code, log where.

dynarmic's signal handler only prints the faulting PC. This adds the library,
offset and nearest symbol for the PC, LR and a frame-pointer backtrace, read
with process_vm_readv so a bad frame cannot fault again. Lines go to the Eden
log (NX2_FAULT) and to logcat synchronously.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
f = root / "src/dynarmic/src/dynarmic/backend/exception_handler_posix.cpp"
if not f.exists():
    f = root / "externals/dynarmic/src/dynarmic/backend/exception_handler_posix.cpp"
t = f.read_text()
if "NX2_FAULT" in t:
    print("fault report already applied"); sys.exit(0)

helper = r'''
#if defined(__ANDROID__) && defined(__aarch64__)
namespace {
std::string Nx2Describe(u64 pc) {
    pc &= 0x0000ffffffffffffULL; // drop tag / pointer-auth bits
    Dl_info dl{};
    if (dladdr(reinterpret_cast<void*>(pc), &dl) && dl.dli_fname) {
        const char* slash = std::strrchr(dl.dli_fname, '/');
        return fmt::format("{:#x} {}+{:#x} {}+{:#x}", pc, slash ? slash + 1 : dl.dli_fname,
                           pc - reinterpret_cast<u64>(dl.dli_fbase),
                           dl.dli_sname ? dl.dli_sname : "?",
                           dl.dli_saddr ? pc - reinterpret_cast<u64>(dl.dli_saddr) : 0);
    }
    return fmt::format("{:#x} (no image)", pc);
}

void Nx2FaultLine(const std::string& line) {
    __android_log_write(ANDROID_LOG_ERROR, "NX2_FAULT", line.c_str());
    LOG_ERROR(Core, "NX2_FAULT {}", line);
}

void Nx2ReportHostFault(int sig, siginfo_t* info, void* raw_context) {
    auto* uc = static_cast<ucontext_t*>(raw_context);
    const auto& mc = uc->uc_mcontext;
    Nx2FaultLine(fmt::format("sig={} code={} addr={:#x} tid={}", sig, info->si_code,
                             reinterpret_cast<u64>(info->si_addr), gettid()));
    Nx2FaultLine(fmt::format("pc {}", Nx2Describe(mc.pc)));
    Nx2FaultLine(fmt::format("lr {}", Nx2Describe(mc.regs[30])));
    u64 fp = mc.regs[29];
    for (int i = 0; i < 32 && fp != 0 && (fp & 7) == 0; ++i) {
        u64 frame[2]{};
        iovec local{frame, sizeof(frame)};
        iovec remote{reinterpret_cast<void*>(fp), sizeof(frame)};
        if (process_vm_readv(getpid(), &local, 1, &remote, 1, 0) != ssize_t(sizeof(frame)) ||
            frame[1] == 0) {
            break;
        }
        Nx2FaultLine(fmt::format("#{:02} {}", i, Nx2Describe(frame[1])));
        if (frame[0] <= fp) {
            break;
        }
        fp = frame[0];
    }
}
} // namespace
#endif
'''
top = "#include <signal.h>\n"
if top not in t:
    raise SystemExit("exception_handler_posix.cpp: signal.h include missing")
t = t.replace(top, top + """#if defined(__ANDROID__) && defined(__aarch64__)
#include <string>
#include <android/log.h>
#include <dlfcn.h>
#include <sys/uio.h>
#include <ucontext.h>
#include <unistd.h>
#endif
""", 1)
anchor = "std::optional<SigHandler> sig_handler;\n"
if anchor not in t:
    raise SystemExit("exception_handler_posix.cpp: sig_handler anchor missing")
t = t.replace(anchor, helper + "\n" + anchor, 1)

log = '    LOG_ERROR(Core, "Unhandled {} at {:#018x}\\n", sig == SIGSEGV ? "SIGSEGV" : "SIGBUS", CTX_PC);\n'
if log not in t:
    raise SystemExit("exception_handler_posix.cpp: Unhandled log line missing")
t = t.replace(log, log + "#if defined(__ANDROID__) && defined(__aarch64__)\n    Nx2ReportHostFault(sig, info, raw_context);\n#endif\n", 1)
f.write_text(t)
print("NX2_FAULT host crash report installed")
