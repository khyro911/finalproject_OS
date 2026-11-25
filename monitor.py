"""
Data collection and process control for the resource monitor.

This module has no GUI code. It can be imported by the GUI (app.py) or run
directly to print a one-off text report:

    python monitor.py
"""

from __future__ import annotations

import os
import platform
import socket
import sys
import time
from dataclasses import dataclass

import psutil

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# Processes that must never be ended from this tool. Ending any of these
# either crashes the OS, logs the user out, or breaks the desktop session.
# Names are compared in lower case.
PROTECTED_NAMES = {
    # Linux / Unix
    "systemd", "init", "kthreadd", "systemd-journald", "systemd-logind",
    "systemd-udevd", "udevd", "dbus-daemon", "dbus-broker", "login",
    "sshd", "xorg", "xwayland", "gnome-shell", "kwin_x11", "kwin_wayland",
    "plasmashell", "gdm", "gdm3", "lightdm", "sddm", "polkitd",
    # Windows
    "system", "system idle process", "registry", "memory compression",
    "secure system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe",
    "fontdrvhost.exe", "explorer.exe",
}

# PIDs that belong to the kernel / OS itself.
PROTECTED_PIDS = {0, 4} if IS_WINDOWS else {0, 1, 2}

CPU_COUNT = psutil.cpu_count(logical=True) or 1


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def human_bytes(n: float | None) -> str:
    """1536 -> '1.5 KB'. Uses 1024-based units."""
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"  # pragma: no cover


def human_duration(seconds: float) -> str:
    """93784 -> '1d 2h 3m'."""
    seconds = int(max(seconds, 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds % 60}s"


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

def system_info() -> dict:
    """Mostly static information about the machine."""
    boot = psutil.boot_time()
    freq = None
    try:
        f = psutil.cpu_freq()
        if f:
            freq = f.max or f.current
    except (NotImplementedError, OSError, FileNotFoundError):
        pass

    return {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "processor": _cpu_model(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": CPU_COUNT,
        "max_frequency_mhz": round(freq) if freq else None,
        "total_ram": psutil.virtual_memory().total,
        "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)),
        "uptime": human_duration(time.time() - boot),
        "python": platform.python_version(),
        "psutil": psutil.__version__,
    }


def _cpu_model() -> str:
    if IS_LINUX:
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or "Unknown"


def cpu_snapshot() -> dict:
    per_core = psutil.cpu_percent(percpu=True)
    total = sum(per_core) / len(per_core) if per_core else 0.0
    load = None
    if hasattr(os, "getloadavg"):
        try:
            load = os.getloadavg()
        except OSError:
            load = None
    current_freq = None
    try:
        f = psutil.cpu_freq()
        if f:
            current_freq = f.current
    except (NotImplementedError, OSError, FileNotFoundError):
        pass
    return {
        "total": total,
        "per_core": per_core,
        "load_avg": load,
        "freq_mhz": current_freq,
    }


def memory_snapshot() -> dict:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        "total": vm.total,
        "used": vm.total - vm.available,
        "available": vm.available,
        "percent": vm.percent,
        "swap_total": sw.total,
        "swap_used": sw.used,
        "swap_percent": sw.percent,
    }


def disk_snapshot() -> list[dict]:
    disks = []
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen:
            continue
        seen.add(part.mountpoint)
        # Skip snap/loop images on Linux, they clutter the list.
        if IS_LINUX and part.device.startswith("/dev/loop"):
            continue
        row = {
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype or "-",
            "total": None, "used": None, "free": None, "percent": None,
        }
        try:
            usage = psutil.disk_usage(part.mountpoint)
            row.update(total=usage.total, used=usage.used,
                       free=usage.free, percent=usage.percent)
        except (PermissionError, OSError):
            # e.g. an empty DVD drive on Windows
            pass
        disks.append(row)
    return disks


def disk_io_snapshot() -> dict | None:
    try:
        io = psutil.disk_io_counters()
    except (RuntimeError, OSError):
        return None
    if io is None:
        return None
    return {"read_bytes": io.read_bytes, "write_bytes": io.write_bytes}


_PROC_ATTRS = ["pid", "name", "username", "cpu_percent", "memory_percent",
               "memory_info", "status", "num_threads", "create_time"]


def list_processes() -> list[dict]:
    """
    Return one dict per running process.

    CPU % is normalised to the whole machine (0-100), like Windows Task
    Manager. The very first call returns 0 for every process because psutil
    needs two samples to compute a rate.
    """
    procs = []
    for p in psutil.process_iter(_PROC_ATTRS, ad_value=None):
        info = p.info
        mem = info.get("memory_info")
        user = info.get("username") or ""
        if "\\" in user:  # 'DESKTOP-ABC\\name' -> 'name'
            user = user.split("\\", 1)[1]
        procs.append({
            "pid": info["pid"],
            "name": info.get("name") or "?",
            "user": user,
            "cpu": (info.get("cpu_percent") or 0.0) / CPU_COUNT,
            "mem": info.get("memory_percent") or 0.0,
            "rss": mem.rss if mem else 0,
            "status": info.get("status") or "",
            "threads": info.get("num_threads") or 0,
            "create_time": info.get("create_time"),
        })
    return procs


def full_snapshot() -> dict:
    return {
        "time": time.time(),
        "cpu": cpu_snapshot(),
        "memory": memory_snapshot(),
        "disks": disk_snapshot(),
        "disk_io": disk_io_snapshot(),
        "processes": list_processes(),
        "uptime": human_duration(time.time() - psutil.boot_time()),
    }


# --------------------------------------------------------------------------
# Process termination with safeguards
# --------------------------------------------------------------------------

@dataclass
class TerminateCheck:
    allowed: bool
    reason: str = ""


def check_can_terminate(pid: int, name: str | None = None) -> TerminateCheck:
    """Decide whether this tool is allowed to end the given process."""
    if pid in PROTECTED_PIDS:
        return TerminateCheck(False, "This is a core operating system process.")
    if pid == os.getpid():
        return TerminateCheck(False, "This is the monitor itself. Close the window instead.")
    if pid == os.getppid():
        return TerminateCheck(False, "This process started the monitor. Ending it would close the monitor too.")

    try:
        proc = psutil.Process(pid)
        name = name or proc.name()
    except psutil.NoSuchProcess:
        return TerminateCheck(False, "The process has already exited.")
    except psutil.AccessDenied:
        pass

    if name and name.lower() in PROTECTED_NAMES:
        return TerminateCheck(False, f"'{name}' is a protected system process.")

    if IS_LINUX:
        try:
            # Kernel threads are children of kthreadd (PID 2).
            if psutil.Process(pid).ppid() == 2:
                return TerminateCheck(False, "This is a kernel thread.")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return TerminateCheck(True)


class TerminateError(Exception):
    """Raised with a user-readable message when ending a process fails."""


def terminate_process(pid: int, create_time: float | None = None,
                      force: bool = False) -> psutil.Process:
    """
    Ask a process to exit (SIGTERM on Linux) or, with force=True, kill it
    (SIGKILL). On Windows both end the process immediately.

    create_time guards against PID reuse: if the PID now belongs to a
    different process than the one the user selected, nothing is done.
    """
    check = check_can_terminate(pid)
    if not check.allowed:
        raise TerminateError(check.reason)
    try:
        proc = psutil.Process(pid)
        if create_time is not None and abs(proc.create_time() - create_time) > 0.01:
            raise TerminateError("The PID now belongs to a different process. Nothing was ended.")
        if force:
            proc.kill()
        else:
            proc.terminate()
        return proc
    except psutil.NoSuchProcess:
        raise TerminateError("The process has already exited.") from None
    except psutil.AccessDenied:
        hint = " Run the monitor as Administrator." if IS_WINDOWS else " Run the monitor with sudo."
        raise TerminateError("Permission denied." + hint) from None


def wait_for_exit(proc: psutil.Process, timeout: float = 3.0) -> bool:
    """True if the process is gone within timeout seconds."""
    try:
        proc.wait(timeout=timeout)
        return True
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True


# --------------------------------------------------------------------------
# Text report (python monitor.py)
# --------------------------------------------------------------------------

def print_report(top: int = 15) -> None:
    list_processes()                 # prime per-process CPU counters
    psutil.cpu_percent(percpu=True)  # prime system CPU counters
    time.sleep(1.0)
    snap = full_snapshot()
    info = system_info()
    cpu, mem = snap["cpu"], snap["memory"]

    print(f"{info['hostname']}  |  {info['os']}  |  up {snap['uptime']}")
    print(f"CPU   {cpu['total']:5.1f} %   ({info['logical_cores']} logical cores)")
    print(f"RAM   {mem['percent']:5.1f} %   {human_bytes(mem['used'])} / {human_bytes(mem['total'])}")
    print(f"Swap  {mem['swap_percent']:5.1f} %   {human_bytes(mem['swap_used'])} / {human_bytes(mem['swap_total'])}")
    print()
    print(f"{'DEVICE':<22}{'MOUNT':<18}{'TOTAL':>10}{'USED':>10}{'USE%':>7}")
    for d in snap["disks"]:
        pct = f"{d['percent']:.0f}%" if d["percent"] is not None else "-"
        print(f"{d['device'][:21]:<22}{d['mountpoint'][:17]:<18}"
              f"{human_bytes(d['total']):>10}{human_bytes(d['used']):>10}{pct:>7}")
    print()
    print(f"{'PID':>7}  {'NAME':<28}{'CPU%':>6}{'MEM%':>7}{'MEMORY':>11}")
    procs = sorted(snap["processes"], key=lambda p: (p["cpu"], p["mem"]), reverse=True)
    for p in procs[:top]:
        print(f"{p['pid']:>7}  {p['name'][:27]:<28}{p['cpu']:6.1f}{p['mem']:7.1f}{human_bytes(p['rss']):>11}")
    print(f"\n{len(procs)} processes")


if __name__ == "__main__":
    print_report()
