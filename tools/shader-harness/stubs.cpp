#include <cstdio>
#include "common/logging.h"
#include "common/settings.h"
namespace Common::Log {
void FmtLogMessageImpl(Class, Level, const char* file, unsigned int line, const char*,
                       fmt::string_view format, const fmt::format_args& args) {
    std::fprintf(stderr, "LOG %s:%u %s\n", file, line, fmt::vformat(format, args).c_str());
}
}
void AssertFailSoftImpl() { std::fprintf(stderr, "assert failed\n"); }
// Only referenced on paths the harness does not take (loop safety checks).
alignas(64) unsigned char nx2_settings_storage[sizeof(Settings::Values)] asm("_ZN8Settings6valuesE");
