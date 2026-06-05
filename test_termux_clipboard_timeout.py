import os
import re
import time
import subprocess
from pathlib import Path

ADB_PATH = r"platform-tools/adb.exe" if os.name == "nt" else "adb"
DEVICE_SERIAL = "UWJJOJLB85SO7LIZ"

TERMUX_ACTIVITY = "com.termux/.app.TermuxActivity"

REMOTE_DIR = "/sdcard/Download"
REMOTE_SCRIPT = f"{REMOTE_DIR}/tclip_timeout.sh"
REMOTE_PROBE = f"{REMOTE_DIR}/termux_clipboard_probe_timeout.txt"
REMOTE_OUT = f"{REMOTE_DIR}/termux_clipboard_value_timeout.txt"
REMOTE_ERR = f"{REMOTE_DIR}/termux_clipboard_value_timeout.err"
REMOTE_EXIT = f"{REMOTE_DIR}/termux_clipboard_value_timeout.exit"

LOCAL_DIR = Path("termux_test_output")
LOCAL_DIR.mkdir(exist_ok=True)

LOCAL_SCRIPT = LOCAL_DIR / "tclip_timeout.sh"


def run(cmd, timeout=20):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="ignore",
    )


def adb(args, timeout=20):
    cmd = [ADB_PATH, "-s", DEVICE_SERIAL] + args
    return run(cmd, timeout=timeout)


def adb_shell(cmd, timeout=20):
    return adb(["shell", cmd], timeout=timeout)


def print_result(title, r):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print("returncode:", r.returncode)
    print("stdout:")
    print((r.stdout or "").rstrip() or "(empty)")
    print("stderr:")
    print((r.stderr or "").rstrip() or "(empty)")


def get_screen_size():
    r = adb_shell("wm size", timeout=10)
    text = (r.stdout or "") + "\n" + (r.stderr or "")
    m = re.search(r"Physical size:\s*(\d+)x(\d+)", text)
    if not m:
        return 720, 1650
    return int(m.group(1)), int(m.group(2))


def create_local_script():
    content = f"""#!/data/data/com.termux/files/usr/bin/bash
echo STARTED > "{REMOTE_PROBE}"
timeout 5 termux-clipboard-get > "{REMOTE_OUT}" 2> "{REMOTE_ERR}"
printf %s $? > "{REMOTE_EXIT}"
"""
    LOCAL_SCRIPT.write_text(content, encoding="utf-8", newline="\n")
    print(f"Đã tạo local script: {LOCAL_SCRIPT}")


def cleanup_remote():
    r = adb_shell(
        f'rm -f "{REMOTE_SCRIPT}" "{REMOTE_PROBE}" "{REMOTE_OUT}" "{REMOTE_ERR}" "{REMOTE_EXIT}"',
        timeout=15,
    )
    print_result("CLEANUP REMOTE", r)


def push_script():
    r = adb(["push", str(LOCAL_SCRIPT), REMOTE_SCRIPT], timeout=20)
    print_result("PUSH SCRIPT", r)


def open_termux():
    r = adb_shell(f"am start -n {TERMUX_ACTIVITY}", timeout=15)
    print_result("OPEN TERMUX", r)
    time.sleep(3)


def focus_termux_prompt():
    w, h = get_screen_size()

    points = [
        (int(w * 0.50), int(h * 0.85)),
        (int(w * 0.50), int(h * 0.76)),
        (int(w * 0.18), int(h * 0.85)),
    ]

    for idx, (x, y) in enumerate(points, start=1):
        r = adb_shell(f"input tap {x} {y}", timeout=10)
        print_result(f"FOCUS TAP #{idx} ({x},{y})", r)
        time.sleep(0.5)

    r = adb_shell("input keyevent 66", timeout=10)
    print_result("WAKE PROMPT ENTER", r)
    time.sleep(0.8)


def type_and_run_command():
    typed = "bash%s/sdcard/Download/tclip_timeout.sh"
    r1 = adb(["shell", "input", "text", typed], timeout=15)
    print_result("TYPE COMMAND", r1)
    time.sleep(1.0)

    r2 = adb_shell("input keyevent 66", timeout=10)
    print_result("PRESS ENTER", r2)


def cat_remote(path, title):
    r = adb_shell(f'cat "{path}"', timeout=10)
    print_result(title, r)
    return r


def poll_exit(timeout_sec=12):
    for i in range(timeout_sec):
        time.sleep(1)
        r = adb_shell(f'cat "{REMOTE_EXIT}"', timeout=10)
        print_result(f"POLL EXIT ROUND {i+1}", r)
        if r.returncode == 0 and (r.stdout or "").strip() != "":
            return True
    return False


def main():
    r = adb(["get-state"], timeout=10)
    print_result("ADB GET-STATE", r)
    if r.returncode != 0 or "device" not in (r.stdout or ""):
        raise RuntimeError("ADB không thấy thiết bị")

    create_local_script()
    cleanup_remote()
    push_script()
    open_termux()
    focus_termux_prompt()
    type_and_run_command()

    ok = poll_exit(timeout_sec=12)

    print("\n" + "=" * 80)
    print("KẾT QUẢ EXIT")
    print("=" * 80)
    print("exit_found =", ok)

    cat_remote(REMOTE_PROBE, "READ PROBE FILE")
    cat_remote(REMOTE_OUT, "READ CLIPBOARD VALUE")
    cat_remote(REMOTE_ERR, "READ CLIPBOARD ERR")
    cat_remote(REMOTE_EXIT, "READ CLIPBOARD EXIT")

    print("\nXong.")


if __name__ == "__main__":
    main()