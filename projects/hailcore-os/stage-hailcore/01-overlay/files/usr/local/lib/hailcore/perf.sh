#!/bin/sh
# Apply runtime tuning that can't live in config.txt or sysctl.d.

gov=${HAILCORE_GOVERNOR:-performance}
for f in /sys/devices/system/cpu/cpufreq/policy*/scaling_governor; do
    [ -w "$f" ] && echo "$gov" > "$f" 2>/dev/null || true
done

# Wi-Fi power save adds 100+ ms of latency to SSH and API calls.
if command -v iw >/dev/null; then
    for dev in /sys/class/net/wl*; do
        [ -e "$dev" ] && iw dev "$(basename "$dev")" set power_save off 2>/dev/null || true
    done
fi

# The Hailo is on PCIe; disabling link power management avoids wake-up stalls
# between tokens.
if [ -w /sys/module/pcie_aspm/parameters/policy ]; then
    echo performance > /sys/module/pcie_aspm/parameters/policy 2>/dev/null || true
fi
exit 0
