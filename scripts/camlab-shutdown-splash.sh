#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Arm the Plymouth shutdown splash from camlab.service ExecStop, so no tty
# text shows between Cage exiting and the splash. No-op unless the system is
# shutting down. Starts the plymouth unit rather than plymouthd directly,
# which would die with camlab's cgroup.

set -euo pipefail

state="$(systemctl is-system-running 2>/dev/null || true)"
[ "$state" = "stopping" ] || exit 0

if systemctl list-jobs --no-legend 2>/dev/null | grep -qE '(reboot|kexec)\.target'; then
    unit="plymouth-reboot.service"
else
    unit="plymouth-poweroff.service"
fi

systemctl start --no-block "$unit" 2>/dev/null || exit 0

# Give plymouthd a moment to fork, then queue the splash.
for _ in $(seq 1 20); do
    if plymouth --ping 2>/dev/null; then
        break
    fi
    sleep 0.1
done
plymouth show-splash 2>/dev/null || true
