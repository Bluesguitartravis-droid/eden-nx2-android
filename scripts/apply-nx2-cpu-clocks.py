#!/usr/bin/env python3
"""Log host CPU clocks every 10 s next to NX2_GPU (is the phone throttling?).

NX2_CPU cur=<MHz per core> max=<MHz cap per cluster policy>. A cap below the
cluster's normal top speed means Android/thermal limits are in force.
Usage: python3 scripts/apply-nx2-cpu-clocks.py <upstream-root>
"""
from pathlib import Path
import sys

core = Path(sys.argv[1]) / "src/core/core.cpp"
c = core.read_text()
anchor = '                            LOG_INFO(Core, "NX2_GPU busy={} clk={} max={} temp={} switches=[{}]",\n'
if "NX2_CPU cur=" in c:
    print("already patched")
    raise SystemExit(0)
if c.count(anchor) != 1:
    raise SystemExit("core.cpp: NX2_GPU log line not found exactly once")
c = c.replace(anchor, r'''                            {
                                std::string nx2_cur, nx2_max;
                                std::string nx2_last_max;
                                for (int cpu = 0; cpu < 16; ++cpu) {
                                    const std::string base =
                                        "/sys/devices/system/cpu/cpu" + std::to_string(cpu) + "/cpufreq/";
                                    const std::string cur = read_sys((base + "scaling_cur_freq").c_str());
                                    if (cur == "?" && cpu >= 8) {
                                        break;
                                    }
                                    const auto mhz = [](const std::string& khz) {
                                        char* end = nullptr;
                                        const long v = std::strtol(khz.c_str(), &end, 10);
                                        return end == khz.c_str() ? std::string{"?"} : std::to_string(v / 1000);
                                    };
                                    nx2_cur += (nx2_cur.empty() ? "" : ",") + mhz(cur);
                                    const std::string cap = mhz(read_sys((base + "scaling_max_freq").c_str()));
                                    // One cap per cluster: print it when it changes between cores.
                                    if (cap != nx2_last_max) {
                                        nx2_max += (nx2_max.empty() ? "" : " ") +
                                                   std::string{"cpu"} + std::to_string(cpu) + "+:" + cap;
                                        nx2_last_max = cap;
                                    }
                                }
                                LOG_INFO(Core, "NX2_CPU cur={} max={}", nx2_cur, nx2_max);
                            }
''' + anchor, 1)
core.write_text(c)
print("NX2_CPU clock logging installed")
