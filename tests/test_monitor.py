import os
import subprocess
import sys
import time

import psutil
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor  # noqa: E402


def test_human_bytes():
    assert monitor.human_bytes(None) == "-"
    assert monitor.human_bytes(512) == "512 B"
    assert monitor.human_bytes(1536) == "1.5 KB"
    assert monitor.human_bytes(5 * 1024 ** 3) == "5.0 GB"


def test_human_duration():
    assert monitor.human_duration(59) == "0m 59s"
    assert monitor.human_duration(3 * 3600 + 120) == "3h 2m"
    assert monitor.human_duration(86400 + 7200 + 180) == "1d 2h 3m"


def test_snapshots_have_expected_shape():
    snap = monitor.full_snapshot()
    assert 0 <= snap["cpu"]["total"] <= 100
    assert len(snap["cpu"]["per_core"]) == psutil.cpu_count()
    assert snap["memory"]["total"] > 0
    assert 0 <= snap["memory"]["percent"] <= 100
    assert isinstance(snap["disks"], list)
    pids = {p["pid"] for p in snap["processes"]}
    assert os.getpid() in pids


def test_system_info():
    info = monitor.system_info()
    assert info["logical_cores"] >= 1
    assert info["total_ram"] > 0
    assert info["hostname"]


def test_refuses_core_and_self():
    for pid in monitor.PROTECTED_PIDS:
        assert not monitor.check_can_terminate(pid).allowed
    assert not monitor.check_can_terminate(os.getpid()).allowed
    assert not monitor.check_can_terminate(os.getppid()).allowed


def test_refuses_protected_names():
    name = "svchost.exe" if monitor.IS_WINDOWS else "systemd"
    check = monitor.check_can_terminate(999999, name)
    # Either refused for its name, or because the PID does not exist.
    assert not check.allowed


def test_refuses_missing_pid():
    with pytest.raises(monitor.TerminateError):
        monitor.terminate_process(2 ** 22 + 12345)


def _spawn_sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_terminate_child_process():
    child = _spawn_sleeper()
    try:
        time.sleep(0.3)
        assert monitor.check_can_terminate(child.pid).allowed
        handle = monitor.terminate_process(child.pid, psutil.Process(child.pid).create_time())
        assert monitor.wait_for_exit(handle, timeout=5)
    finally:
        if child.poll() is None:
            child.kill()


def test_pid_reuse_guard():
    child = _spawn_sleeper()
    try:
        time.sleep(0.3)
        with pytest.raises(monitor.TerminateError, match="different process"):
            monitor.terminate_process(child.pid, create_time=1.0)
        assert child.poll() is None  # still running
    finally:
        child.kill()
