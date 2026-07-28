# -*- coding: utf-8 -*-
"""
USB HS/FS demo validation for PIC32CK Curiosity Ultra boards.

For every USB device demo (see demos.py), and for each requested USB speed
(HS and/or FS), this tool:

  1. BUILD  - builds the sample with twister (``--build-only``); this is the
              "build test must pass" gate. Falls back to ``west build`` if
              twister is unavailable.
  2. FLASH  - flashes the freshly built image with ``west flash`` (the
              mplab_ipe / PKOB runner proven in the SG01 blinky bring-up).
              Optionally uses ``twister --device-testing`` instead.
  3. ENUM   - watches the Windows USB device list and validates that the board
              re-enumerates as a Zephyr USB device (VID 0x2FE3). Enumeration is
              the pass criterion for now.

Results are written to results\\usb_validation_report.html and .json, and any
failing demo is listed at the end.

HS vs FS
--------
Both boards route USB through the high-speed controller (hsusb0) by default,
exposed to the samples via the ``zephyr_udc0`` device-tree *node label*.
Because samples bind the controller with ``DT_NODELABEL(zephyr_udc0)``, FS
cannot be selected with a plain overlay (that would duplicate the label). For
FS runs this tool therefore temporarily patches the board .dts so the label
points at the full-speed controller (usb0), and always restores the original
file afterwards (even on Ctrl-C / crash).

Usage (from an activated Zephyr venv, cmd.exe recommended on Windows):

    python run_usb_validation.py --list
    python run_usb_validation.py                       # all demos, HS + FS
    python run_usb_validation.py --speeds hs           # HS only
    python run_usb_validation.py --demos cdc_acm,hid_mouse
    python run_usb_validation.py --build-only          # build test only, no HW
    python run_usb_validation.py --restore-dts         # emergency dts restore
"""

import argparse
import datetime
import glob
import html
import json
import os
import shutil
import subprocess
import sys
import time

import demos

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ZEPHYR_BASE = r"D:\ZephyrProject\mchp_zephyrproject\zephyr"

# Resolved at startup by resolve_toolchain(); default to bare names (PATH lookup).
WEST = "west"
PYEXE = sys.executable
IPECMD = ""  # full path to ipecmd.exe once located
BOARD_DTS_REL = os.path.join(
    "boards", "microchip", "pic32c",
    "pic32ck_sg01_cult", "pic32ck_sg01_cult.dts"
)
BAK_SUFFIX = ".usbval.bak"

# ---- status constants -------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
NA = "N/A"


# =============================================================================
# small helpers
# =============================================================================
def ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_line(msg):
    print("[{}] {}".format(ts(), msg), flush=True)


def run_cmd(cmd, log_path, cwd=None, env=None, timeout=1800):
    """Run a command, tee output to a log file, return (rc, combined_output)."""
    with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("$ {}\n\n".format(" ".join(cmd)))
        fh.flush()
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError as exc:
            fh.write("FAILED TO LAUNCH: {}\n".format(exc))
            return 127, str(exc)

        out_lines = []
        try:
            for line in proc.stdout:
                out_lines.append(line)
                fh.write(line)
                fh.flush()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            fh.write("\n*** TIMEOUT after {}s ***\n".format(timeout))
            return 124, "".join(out_lines)
        return proc.returncode, "".join(out_lines)


# =============================================================================
# Windows USB enumeration
# =============================================================================
def query_usb_devices():
    """Return a dict {PNPDeviceID: Name} for all present USB PnP devices."""
    ps = (
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.PNPDeviceID -like 'USB\\*' } | "
        "Select-Object PNPDeviceID,Name | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        log_line("WARNING: could not query USB devices: {}".format(exc))
        return {}
    out = out.strip()
    if not out:
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        data = [data]
    result = {}
    for d in data:
        pid = (d.get("PNPDeviceID") or "").strip()
        if pid:
            result[pid] = (d.get("Name") or "").strip()
    return result


def find_vid(devices, vid):
    """Return list of (id, name) whose PNPDeviceID contains VID_<vid>."""
    token = "VID_{}".format(vid.upper())
    return [(i, n) for i, n in devices.items() if token in i.upper()]


def wait_for_enumeration(baseline, vid, timeout, settle=1.0, interval=1.0):
    """
    Poll the USB device list until a Zephyr device (VID) shows up.
    Returns (status, matched_list, new_devices) and returns as soon as the
    device appears (so a passing enumeration is fast).
    """
    time.sleep(settle)
    deadline = time.time() + timeout
    last = {}
    while True:
        last = query_usb_devices()
        matched = find_vid(last, vid)
        if matched:
            new = {i: n for i, n in last.items() if i not in baseline}
            return PASS, matched, new
        if time.time() >= deadline:
            break
        time.sleep(interval)
    new = {i: n for i, n in last.items() if i not in baseline}
    return FAIL, [], new


# =============================================================================
# serial console (for demos that need shell commands to bring USB up)
# =============================================================================
def find_console_port():
    """
    Best-effort auto-detect of the board's VCOM/console COM port on Windows.
    Prefers ports whose name/ID looks like an on-board debugger VCOM, and never
    picks a Zephyr CDC device (VID_2FE3). Returns "COMx" or None.
    """
    ps = ("Get-CimInstance Win32_PnPEntity | "
          "Where-Object { $_.Name -match '\\(COM\\d+\\)' } | "
          "Select-Object Name,PNPDeviceID | ConvertTo-Json -Compress")
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            text=True, encoding="utf-8", errors="replace", timeout=30).strip()
    except Exception:  # noqa: BLE001
        return None
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data = [data]

    import re
    cands = []
    for d in data:
        name = d.get("Name") or ""
        pnp = (d.get("PNPDeviceID") or "").upper()
        m = re.search(r"\((COM\d+)\)", name)
        if not m or "VID_2FE3" in pnp:  # skip Zephyr CDC devices
            continue
        cands.append((m.group(1), name, pnp))

    keywords = ("curiosity", "pkob", "virtual com", "microchip", "mchp",
                "edbg", "embedded debugger", "mcp2221", "j-link", "jlink")
    for com, name, pnp in cands:
        blob = (name + " " + pnp).lower()
        if any(k in blob for k in keywords):
            return com
    if len(cands) == 1:
        return cands[0][0]
    return None


_SERIAL_SNIPPET = (
    "import sys, time\n"
    "import serial\n"
    "port = sys.argv[1]; baud = int(sys.argv[2]); boot = float(sys.argv[3]); cmds = sys.argv[4:]\n"
    "ser = serial.Serial(port, baud, timeout=1)\n"
    "try:\n"
    "    time.sleep(boot)\n"
    "    ser.reset_input_buffer()\n"
    "    ser.write(b'\\r\\n'); time.sleep(0.3)\n"
    "    for c in cmds:\n"
    "        ser.write(c.encode() + b'\\r\\n'); time.sleep(0.7)\n"
    "    time.sleep(0.5)\n"
    "    data = ser.read(8192)\n"
    "    sys.stdout.write('OK\\n')\n"
    "    sys.stdout.write(data.decode('utf-8', 'replace'))\n"
    "finally:\n"
    "    ser.close()\n"
)


def send_console_commands(port, cmds, baud=115200, boot_wait=2.0):
    """
    Open the serial console with pyserial (run under the venv Python that has it)
    and send the given shell commands. Returns (ok, output).
    """
    cmd = [PYEXE, "-c", _SERIAL_SNIPPET, port, str(baud), str(boot_wait)] + list(cmds)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=40)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    out = (res.stdout or "") + (res.stderr or "")
    return (res.returncode == 0 and "OK" in (res.stdout or "")), out


# =============================================================================
# data transactions (post-enumeration: verify real transfer, not just enum)
# =============================================================================
def find_cdc_port(vid):
    """Return the COM port of the enumerated Zephyr CDC device (VID_<vid>), or None."""
    import re
    ps = ("Get-CimInstance Win32_PnPEntity | Where-Object {{ $_.Name -match '\\(COM\\d+\\)'"
          " -and $_.PNPDeviceID -like '*VID_{}*' }} | Select-Object Name |"
          " ConvertTo-Json -Compress").format(vid.upper())
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            text=True, encoding="utf-8", errors="replace", timeout=30).strip()
    except Exception:  # noqa: BLE001
        return None
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data = [data]
    for d in data:
        m = re.search(r"\((COM\d+)\)", d.get("Name") or "")
        if m:
            return m.group(1)
    return None


# pyserial snippet: threaded write + concurrent read of a known N-byte payload,
# then compare. Concurrency avoids the deadlock the 1 KB echo ring buffer would
# otherwise cause on a write-all-then-read-all.
_CDC_ECHO_SNIPPET = (
    "import sys, time, threading\n"
    "import serial\n"
    "port = sys.argv[1]; baud = int(sys.argv[2]); size = int(sys.argv[3])\n"
    "payload = bytes(i & 0xFF for i in range(size))\n"
    "ser = serial.Serial(port, baud, timeout=1, write_timeout=30)\n"
    "try:\n"
    "    try:\n"
    "        ser.set_buffer_size(rx_size=1 << 20, tx_size=1 << 20)\n"
    "    except Exception:\n"
    "        pass\n"
    "    ser.dtr = True; ser.rts = True\n"       # cdc_acm waits for DTR before echoing
    "    time.sleep(0.5)\n"
    "    ser.reset_input_buffer(); ser.reset_output_buffer()\n"
    "    def _writer():\n"
    "        try:\n"
    "            for i in range(0, size, 4096):\n"
    "                ser.write(payload[i:i + 4096])\n"
    "            ser.flush()\n"
    "        except Exception as e:\n"
    "            sys.stderr.write('writer: %r\\n' % e)\n"
    "    t = threading.Thread(target=_writer, daemon=True); t.start()\n"
    "    received = bytearray(); last = time.time()\n"
    "    while len(received) < size:\n"
    "        chunk = ser.read(min(4096, size - len(received)))\n"
    "        if chunk:\n"
    "            received += chunk; last = time.time()\n"
    "        elif time.time() - last > 5.0:\n"
    "            break\n"
    "    t.join(timeout=5)\n"
    "    ok = bytes(received) == payload\n"
    "    mism = -1\n"
    "    if not ok:\n"
    "        for j in range(min(len(received), size)):\n"
    "            if received[j] != payload[j]:\n"
    "                mism = j; break\n"
    "    print('RESULT %s sent=%d recv=%d mismatch_at=%d' %\n"
    "          ('PASS' if ok else 'FAIL', size, len(received), mism))\n"
    "finally:\n"
    "    ser.close()\n"
)


def cdc_echo_transaction(port, size, baud=115200):
    """Send `size` bytes to the CDC echo device and read them back. Returns (ok, detail)."""
    cmd = [PYEXE, "-c", _CDC_ECHO_SNIPPET, port, str(baud), str(size)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    out = ((res.stdout or "") + (res.stderr or "")).strip()
    line = next((l for l in out.splitlines() if l.startswith("RESULT")), "")
    ok = res.returncode == 0 and line.startswith("RESULT PASS")
    return ok, (line or out[:160])


def _ps_lines(ps):
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            text=True, encoding="utf-8", errors="replace", timeout=30)
    except Exception:  # noqa: BLE001
        return set()
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def list_drive_letters():
    """Set of ALL mounted drive letters (any type) — the MSC disk may mount fixed or removable."""
    return _ps_lines("(Get-CimInstance Win32_LogicalDisk).DeviceID")


def list_usb_disks():
    """Set of USB-backed physical disks (present even with no filesystem/letter)."""
    return _ps_lines("Get-Disk | Where-Object BusType -eq 'USB' | "
                     "ForEach-Object { \"$($_.Number):$($_.FriendlyName)\" }")


def usb_device_error_code(vid):
    """
    If a VID_<vid> device is in an Error state, return its ConfigManagerErrorCode
    string (e.g. 'CM_PROB_FAILED_START'), else ''. FAILED_START/DISABLED on a
    mass-storage device typically means host USB device-control / DLP blocked it.
    """
    ps = ("Get-PnpDevice | Where-Object {{ $_.InstanceId -like '*VID_{}*' -and "
          "$_.Status -eq 'Error' }} | Select-Object -First 1 -ExpandProperty "
          "ConfigManagerErrorCode").format(vid.upper())
    return next(iter(_ps_lines(ps)), "")


def wait_for_new_drive(pre_drives, timeout=20):
    """Poll for a new drive letter (any type) that wasn't present in `pre_drives`."""
    deadline = time.time() + timeout
    while True:
        new = list_drive_letters() - pre_drives
        if new:
            return sorted(new)[0]
        if time.time() >= deadline:
            return None
        time.sleep(1)


def mass_file_transaction(drive, size):
    """Write `size` bytes to a file on `drive`, read it back, compare. Returns (ok, detail)."""
    path = os.path.join(drive + "\\", "usbval_test.bin")
    payload = bytes(i & 0xFF for i in range(size))
    try:
        with open(path, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())        # push to the device (removable = write-through)
        with open(path, "rb") as fh:
            back = fh.read()
        ok = back == payload
        try:
            os.remove(path)
        except OSError:
            pass
        return ok, "wrote {} B to {} , read {} B , match={}".format(
            size, path, len(back), ok)
    except OSError as exc:
        return False, "file I/O error on {}: {}".format(drive, exc)


# =============================================================================
# YKUSH switchable USB hub (optional, opt-in via --ykush-port-hs/-fs)
# =============================================================================
def ykush_power(port, on, serial=None, timeout=30):
    """
    Power the board's USB *device* cable on/off via ykush.py, run under the venv
    Python (which has the `hid` package). Returns (ok, output).
    """
    script = os.path.join(HERE, "ykush.py")
    cmd = [PYEXE, script, ("on" if on else "off"), str(port)]
    if serial:
        cmd += ["--serial", serial]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return res.returncode == 0, ((res.stdout or "") + (res.stderr or "")).strip()


def ykush_enabled(args):
    return bool(args.ykush_port_hs or args.ykush_port_fs)


def ykush_present(serial=None, timeout=20):
    """Return (found, info) — is a YKUSH hub actually attached?"""
    cmd = [PYEXE, os.path.join(HERE, "ykush.py"), "present"]
    if serial:
        cmd += ["--serial", serial]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
    except Exception:  # noqa: BLE001
        return False, ""
    return res.returncode == 0, ((res.stdout or "") + (res.stderr or "")).strip()


def ykush_set_for_speed(speed, args):
    """
    Connect the USB device connector for `speed` and disconnect the other, so
    only the connector under test is live. No-op unless ykush is active.
    HS -> USB-C, FS -> Micro-B.
    """
    if not getattr(args, "ykush_active", False):
        return
    serial = args.ykush_serial or None
    want = args.ykush_port_hs if speed == "hs" else args.ykush_port_fs
    other = args.ykush_port_fs if speed == "hs" else args.ykush_port_hs
    if other and other != want:
        ok, _ = ykush_power(other, False, serial)
        log_line("ykush       : {} connector (port {}) -> OFF ({})".format(
            "FS" if speed == "hs" else "HS", other, "ok" if ok else "fail"))
    if want:
        ok, out = ykush_power(want, True, serial)
        log_line("ykush       : {} connector (port {}) -> ON ({})".format(
            speed.upper(), want, "ok" if ok else "FAILED " + out[:60]))
        time.sleep(2)  # let the host enumerate the newly-powered connector
    else:
        log_line("ykush       : no port set for {} - manage that cable manually".format(
            speed.upper()))


def ykush_restore_all(args):
    """Leave both configured connectors powered on at the end of a run."""
    if not getattr(args, "ykush_active", False):
        return
    serial = args.ykush_serial or None
    for p in (args.ykush_port_hs, args.ykush_port_fs):
        if p:
            ykush_power(p, True, serial)


# =============================================================================
# FS device-tree patch (temporary, always restored)
# =============================================================================
FS_USB0_BLOCK = """zephyr_udc0: &usb0 {
\tpinctrl-0 = <&usb_dc_default>;
\tpinctrl-names = "default";
\t/* USBFS (usb0) selected as the USB device controller for FS validation. */
\tstatus = "okay";
};"""

FS_HSUSB_BLOCK = """&hsusb0 {
\tstatus = "disabled";
};"""


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def apply_fs_patch(dts_path):
    """Patch the board .dts so zephyr_udc0 points at usb0 (FS). Returns True on success."""
    import re

    bak = dts_path + BAK_SUFFIX
    if os.path.exists(bak):
        # A previous run did not clean up; recover the pristine file first.
        log_line("Found stale backup, restoring original .dts before patching.")
        shutil.copyfile(bak, dts_path)
    else:
        shutil.copyfile(dts_path, bak)

    text = _read(dts_path)

    usb0_re = re.compile(r'&usb0\s*\{.*?status\s*=\s*"disabled";\s*\};', re.DOTALL)
    hsusb_re = re.compile(r'zephyr_udc0:\s*&hsusb0\s*\{.*?\};', re.DOTALL)

    if len(usb0_re.findall(text)) != 1 or len(hsusb_re.findall(text)) != 1:
        log_line("ERROR: board .dts USB blocks not in expected form; "
                 "skipping FS patch. Restoring original.")
        shutil.copyfile(bak, dts_path)
        os.remove(bak)
        return False

    text = usb0_re.sub(FS_USB0_BLOCK, text, count=1)
    text = hsusb_re.sub(FS_HSUSB_BLOCK, text, count=1)
    _write(dts_path, text)
    log_line("Applied FS device-tree patch (zephyr_udc0 -> usb0).")
    return True


def restore_dts(dts_path):
    """Restore the board .dts from backup if present."""
    bak = dts_path + BAK_SUFFIX
    if os.path.exists(bak):
        shutil.copyfile(bak, dts_path)
        os.remove(bak)
        log_line("Restored original board .dts.")
        return True
    return False


# =============================================================================
# portability: locate the Zephyr base and west across machines
# =============================================================================
def is_valid_zephyr_base(p):
    """A Zephyr base is a dir containing Kconfig.zephyr and samples/."""
    return bool(p) and os.path.isfile(os.path.join(p, "Kconfig.zephyr")) \
        and os.path.isdir(os.path.join(p, "samples"))


def normalize_zephyr_base(p):
    """Accept either the zephyr dir or a workspace root that contains 'zephyr'."""
    if not p:
        return None
    p = os.path.abspath(os.path.expanduser(p.strip().strip('"')))
    if is_valid_zephyr_base(p):
        return p
    cand = os.path.join(p, "zephyr")
    if is_valid_zephyr_base(cand):
        return cand
    return None


def git_commit_info(zephyr_base):
    """Return {commit, short, branch, subject} for the Zephyr repo, or {} if not a git repo."""
    import shutil
    if not shutil.which("git"):
        return {}

    def _git(*a):
        try:
            return subprocess.check_output(
                ["git", "-C", zephyr_base, *a], text=True, encoding="utf-8",
                stderr=subprocess.DEVNULL, timeout=15).strip()
        except Exception:  # noqa: BLE001
            return ""

    commit = _git("rev-parse", "HEAD")
    if not commit:
        return {}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")  # "HEAD" when detached
    return dict(commit=commit, short=commit[:12],
                branch=("detached" if branch == "HEAD" else branch),
                subject=_git("log", "-1", "--pretty=%s"))


def _west_topdir_zephyr():
    import shutil
    if not shutil.which("west"):
        return None
    try:
        top = subprocess.check_output(["west", "topdir"], text=True,
                                      encoding="utf-8", timeout=30).strip()
    except Exception:  # noqa: BLE001
        return None
    return normalize_zephyr_base(top)


def resolve_zephyr_base(args):
    """
    Find a valid Zephyr base without hardcoding a machine-specific path.
    Order: --zephyr-base -> $ZEPHYR_BASE -> `west topdir` -> search up from CWD
    -> built-in default -> interactive prompt. Returns an absolute path or None.
    """
    if getattr(args, "zephyr_base", ""):
        n = normalize_zephyr_base(args.zephyr_base)
        if n:
            return n
        log_line("Provided --zephyr-base is not a valid Zephyr tree: {}".format(args.zephyr_base))

    for src in (os.environ.get("ZEPHYR_BASE", ""),):
        n = normalize_zephyr_base(src)
        if n:
            log_line("Using ZEPHYR_BASE from environment.")
            return n

    n = _west_topdir_zephyr()
    if n:
        log_line("Detected Zephyr base via 'west topdir'.")
        return n

    d = os.getcwd()
    for _ in range(8):
        if is_valid_zephyr_base(d):
            return d
        cand = os.path.join(d, "zephyr")
        if is_valid_zephyr_base(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent

    n = normalize_zephyr_base(DEFAULT_ZEPHYR_BASE)
    if n:
        return n

    if sys.stdin and sys.stdin.isatty():
        while True:
            raw = input("Enter path to the Zephyr base (…\\zephyr) or workspace root "
                        "(blank to abort): ").strip()
            if not raw:
                return None
            n = normalize_zephyr_base(raw)
            if n:
                return n
            print("  not a valid Zephyr tree (need Kconfig.zephyr + samples/); try again.")
    return None


def _scan_for_west(zephyr_base, extra_venv):
    """Search common locations for west without requiring venv activation."""
    import shutil
    w = shutil.which("west")
    if w:
        return w
    cand_dirs = []
    if extra_venv:
        cand_dirs.append(extra_venv)
    if os.environ.get("VIRTUAL_ENV"):
        cand_dirs.append(os.environ["VIRTUAL_ENV"])
    # walk up from the zephyr base (workspace, its parent, ...) looking for a venv
    d = os.path.abspath(zephyr_base)
    for _ in range(4):
        d = os.path.dirname(d)
        for name in (".venv", "venv", "env"):
            cand_dirs.append(os.path.join(d, name))
    for vd in cand_dirs:
        for rel in (("Scripts", "west.exe"), ("bin", "west")):
            cand = os.path.join(vd, *rel)
            if os.path.isfile(cand):
                return cand
    return None


def find_usbfs_snippet(zephyr_base):
    """
    Return the name of the snippet that selects the Full-Speed USB controller
    (the one that defines PIC32CK_USBFS), or None on branches without it.

    Newer branches replace the manual board-.dts patch with an official snippet
    (e.g. `microchip-udc-usbfs`). When present it is the correct, supported way
    to build for FS: `west build -S <name> ...`.
    """
    root = os.path.join(zephyr_base, "snippets")
    for yml in glob.glob(os.path.join(root, "**", "snippet.yml"), recursive=True):
        try:
            text = _read(yml)
        except Exception:  # noqa: BLE001
            continue
        if "PIC32CK_USBFS" in text:
            name = _yaml_scalar(text, "name")
            if name:
                return name
    return None


# =============================================================================
# toolchain resolution & preflight
# =============================================================================
def find_ipecmd(explicit):
    """Locate ipecmd.exe (MPLAB IPE). Returns full path or None."""
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        cand = os.path.join(explicit, "ipecmd.exe")
        if os.path.isfile(cand):
            return cand
    pats = [
        r"C:\Program Files\Microchip\MPLABX\*\mplab_platform\mplab_ipe\ipecmd.exe",
        r"C:\Program Files (x86)\Microchip\MPLABX\*\mplab_platform\mplab_ipe\ipecmd.exe",
        r"C:\Program Files\Microchip\MPLABX\*\mplab_platform\bin\ipecmd.exe",
    ]
    hits = []
    for p in pats:
        hits += glob.glob(p)
    if hits:
        hits.sort(key=os.path.getmtime, reverse=True)  # most recently installed first
        return hits[0]
    return None


def resolve_toolchain(args):
    """
    Locate `west`, the venv Python, and ipecmd.exe so the tool works even when
    the Zephyr virtual environment / MPLAB IPE are not on PATH.
    Returns (env, west_found). Sets module globals WEST, PYEXE, IPECMD.
    """
    global WEST, PYEXE, IPECMD
    import shutil

    env = os.environ.copy()
    env["ZEPHYR_BASE"] = args.zephyr_base

    # -- ipecmd.exe (needed by west flash) ------------------------------------
    ip = find_ipecmd(args.ipecmd)
    if ip:
        IPECMD = ip
        env["PATH"] = os.path.dirname(ip) + os.pathsep + env.get("PATH", "")

    west = args.west or _scan_for_west(args.zephyr_base, args.venv)

    # last resort: ask the user rather than failing silently
    if not west and sys.stdin and sys.stdin.isatty():
        print("Could not auto-locate 'west'.")
        print("  Tip: activate your Zephyr venv and re-run, or paste a path below.")
        raw = input("  Path to west.exe or venv folder (blank to skip): ").strip().strip('"')
        if raw:
            if os.path.isfile(raw):
                west = raw
            else:
                for rel in (("Scripts", "west.exe"), ("bin", "west")):
                    cand = os.path.join(raw, *rel)
                    if os.path.isfile(cand):
                        west = cand
                        break

    if west and os.path.isfile(west):
        WEST = west
        scripts = os.path.dirname(west)
        env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
        pyc = os.path.join(scripts, "python.exe")
        if os.path.isfile(pyc):
            PYEXE = pyc
        return env, True

    # last resort: maybe west is somehow runnable by name
    WEST = "west"
    return env, shutil.which("west") is not None


def preflight(env, need_flash):
    """Check required build/flash tools. Returns (ok, fatal_msgs, warn_msgs)."""
    import shutil
    path = env.get("PATH", "")
    fatal, warn = [], []

    if not (WEST != "west" and os.path.isfile(WEST)) and not shutil.which("west", path=path):
        fatal.append(
            "west not found. Activate the Zephyr venv first:\n"
            "        D:\\ZephyrProject\\.venv\\Scripts\\activate.bat\n"
            "    or pass --west <path to west.exe> / --venv <venv dir>.")
    for tool in ("cmake", "ninja"):
        if not shutil.which(tool, path=path):
            fatal.append("{} not found on PATH (required to build).".format(tool))
    if not shutil.which("dtc", path=path):
        warn.append("dtc not found on PATH; some builds may fail.")
    if need_flash and not shutil.which("ipecmd", path=path) and not shutil.which("ipecmd.exe", path=path):
        warn.append("ipecmd.exe not found on PATH; flashing will fail "
                    "(add MPLAB IPE to PATH). Use --build-only to skip flashing.")
    return (len(fatal) == 0), fatal, warn


# =============================================================================
# board discovery & selection
# =============================================================================
def _yaml_scalar(text, key):
    import re
    m = re.search(r'^\s*{}\s*:\s*(.+?)\s*$'.format(re.escape(key)), text, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def usb_speed_support(dts_text):
    """Return dict describing USB device support for a board given its .dts text."""
    import re
    usb0_re = re.compile(r'&usb0\s*\{.*?status\s*=\s*"disabled";\s*\};', re.DOTALL)
    hs_default = bool(re.search(r'zephyr_udc0:\s*&hsusb0', dts_text))
    fs_default = bool(re.search(r'zephyr_udc0:\s*&usb0\b', dts_text))
    fs_patchable = (hs_default and len(usb0_re.findall(dts_text)) == 1
                    and len(re.findall(r'zephyr_udc0:\s*&hsusb0\s*\{.*?\};', dts_text, re.DOTALL)) == 1)
    speeds = []
    if hs_default:
        speeds.append("hs")
    if fs_default or fs_patchable:
        speeds.append("fs")
    return dict(
        usb=("zephyr_udc0" in dts_text),
        speeds=speeds,
        fs_needs_patch=(fs_patchable and not fs_default),
    )


def discover_boards(zephyr_base):
    """
    Scan boards/microchip/** for USB-device-capable boards.

    Returns a list of dicts: name, full_name, dts, speeds, fs_needs_patch.
    """
    boards = []
    root = os.path.join(zephyr_base, "boards", "microchip")
    for board_yml in glob.glob(os.path.join(root, "**", "board.yml"), recursive=True):
        bdir = os.path.dirname(board_yml)
        try:
            meta = _read(board_yml)
        except Exception:  # noqa: BLE001
            continue
        name = _yaml_scalar(meta, "name")
        full = _yaml_scalar(meta, "full_name") or name
        if not name:
            continue
        dts = os.path.join(bdir, name + ".dts")
        if not os.path.exists(dts):
            hits = glob.glob(os.path.join(bdir, "*.dts"))
            dts = hits[0] if hits else None
        if not dts:
            continue
        try:
            caps = usb_speed_support(_read(dts))
        except Exception:  # noqa: BLE001
            continue
        if not caps["usb"] or not caps["speeds"]:
            continue
        boards.append(dict(name=name, full_name=full, dts=dts,
                           speeds=caps["speeds"], fs_needs_patch=caps["fs_needs_patch"]))
    boards.sort(key=lambda b: b["name"])
    return boards


def print_board_table(boards):
    print("\nAvailable USB-capable Microchip boards:\n")
    print("   {:3s} {:24s} {:12s} {}".format("#", "BOARD", "SPEEDS", "FULL NAME"))
    print("   " + "-" * 74)
    for i, b in enumerate(boards, 1):
        rec = " (recommended)" if b["name"] == "pic32ck_sg01_cult" else ""
        print("   {:<3d} {:24s} {:12s} {}{}".format(
            i, b["name"], "/".join(s.upper() for s in b["speeds"]), b["full_name"], rec))
    print()


def select_board(boards, requested):
    """Resolve the board to use. Interactive menu when not specified and a TTY is present."""
    if requested:
        for b in boards:
            if b["name"] == requested:
                return b
        # allow an unknown/other board name; treat as HS-default best effort
        log_line("Board '{}' not in discovered USB list; using it as given.".format(requested))
        return dict(name=requested, full_name=requested, dts=None,
                    speeds=["hs", "fs"], fs_needs_patch=True)
    if not boards:
        log_line("No USB-capable boards discovered; defaulting to pic32ck_sg01_cult.")
        return dict(name="pic32ck_sg01_cult", full_name="PIC32CK SG01 Curiosity Ultra",
                    dts=None, speeds=["hs", "fs"], fs_needs_patch=True)
    print_board_table(boards)
    default = next((b for b in boards if b["name"] == "pic32ck_sg01_cult"), boards[0])
    if not sys.stdin or not sys.stdin.isatty():
        log_line("Non-interactive; selecting default board: {}".format(default["name"]))
        return default
    while True:
        try:
            raw = input("Select board [1-{}] (Enter = {}): ".format(
                len(boards), default["name"])).strip()
        except EOFError:
            return default
        if raw == "":
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(boards):
            return boards[int(raw) - 1]
        for b in boards:
            if b["name"] == raw:
                return b
        print("  invalid selection, try again.")


# =============================================================================
# build & flash
# =============================================================================
def twister_path(zephyr_base):
    p = os.path.join(zephyr_base, "scripts", "twister")
    return p if os.path.exists(p) else None


def build_with_twister(demo, speed, board, zephyr_base, outdir, env, timeout,
                       snippet=None, extra_args=None, variant=""):
    """Build one sample with twister --build-only. Returns (status, build_dir, log_path, reason)."""
    tw = twister_path(zephyr_base)
    vtag = ("_" + variant) if variant else ""
    log_path = os.path.join(outdir, "logs", "{}_{}{}_build.log".format(demo["key"], speed, vtag))
    if tw is None:
        return build_with_west(demo, speed, board, zephyr_base, outdir, env, timeout,
                               snippet=snippet, extra_args=extra_args, variant=variant)

    tw_out = os.path.join(outdir, "twister", "{}_{}{}".format(demo["key"], speed, vtag))
    sample_dir = os.path.join(zephyr_base, "samples", demo["path"])
    cmd = [
        PYEXE, tw,
        "-p", board,
        "-s", demo["scenario"],
        "-T", sample_dir,
        "--build-only",
        "-O", tw_out,
        "--clobber-output",
        "-v",
    ]
    if snippet:
        cmd += ["--extra-args=SNIPPET={}".format(snippet)]
    for a in (extra_args or []):          # translate "-DX=Y" -> "--extra-args=X=Y"
        cmd += ["--extra-args={}".format(a[2:] if a.startswith("-D") else a)]
    rc, _ = run_cmd(cmd, log_path, cwd=zephyr_base, env=env, timeout=timeout)

    # Ground truth: did a flashable image get produced?
    build_dir = _find_build_dir(tw_out)
    if build_dir:
        return PASS, build_dir, log_path, ""

    reason = _twister_reason(tw_out)
    if reason and "filtered" in reason.lower():
        return SKIP, None, log_path, reason
    return FAIL, None, log_path, reason or "twister build produced no image (rc={})".format(rc)


def build_with_west(demo, speed, board, zephyr_base, outdir, env, timeout,
                    pristine="always", cache_dir=None, snippet=None,
                    extra_args=None, variant=""):
    """
    Build one sample with west build. Returns (status, build_dir, log_path, reason).

    pristine : "auto" (west decides, enables fast incremental rebuilds),
               "always" (force clean, original behaviour), or "never".
    cache_dir: when set, build artifacts live in a stable per-(board,demo,speed)
               directory here so repeat runs are incremental. Logs stay in outdir.
    extra_args: extra CMake -D args appended after '--' (e.g. FAT config for mass).
    variant  : tag appended to the build-dir name so variant builds (e.g. the FAT
               mass build) don't collide with the plain build in the cache.
    """
    tag = "_{}_{}".format(demo["key"], speed) + (("_" + variant) if variant else "")
    log_path = os.path.join(outdir, "logs", "{}_{}{}_build.log".format(
        demo["key"], speed, ("_" + variant) if variant else ""))
    if cache_dir:
        build_dir = os.path.join(cache_dir, board + tag)
    else:
        build_dir = os.path.join(outdir, "build", tag.lstrip("_"))
    sample_dir = os.path.join(zephyr_base, "samples", demo["path"])
    cmd = [
        WEST, "build", "-p", pristine,
        "-b", board,
        "-d", build_dir,
    ]
    if snippet:
        cmd += ["-S", snippet]
    cmd += [sample_dir]
    if extra_args:
        cmd += ["--"] + list(extra_args)
    rc, _ = run_cmd(cmd, log_path, cwd=zephyr_base, env=env, timeout=timeout)
    if rc == 0 and (os.path.exists(os.path.join(build_dir, "zephyr", "zephyr.hex"))
                    or os.path.exists(os.path.join(build_dir, "zephyr", "zephyr.elf"))):
        return PASS, build_dir, log_path, ""
    return FAIL, None, log_path, "west build failed (rc={})".format(rc)


def _find_build_dir(tw_out):
    """Locate the build directory twister produced (the one containing zephyr.hex/elf)."""
    for pat in ("**/zephyr/zephyr.hex", "**/zephyr/zephyr.elf"):
        hits = glob.glob(os.path.join(tw_out, pat), recursive=True)
        if hits:
            hits.sort(key=os.path.getmtime, reverse=True)
            # build_dir is the parent of the "zephyr" folder
            return os.path.dirname(os.path.dirname(hits[0]))
    return None


def _twister_reason(tw_out):
    """Extract a status/reason from twister.json if present."""
    jpath = os.path.join(tw_out, "twister.json")
    if not os.path.exists(jpath):
        return ""
    try:
        with open(jpath, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return ""
    suites = data.get("testsuites", [])
    if not suites:
        return ""
    s = suites[0]
    status = s.get("status", "")
    reason = s.get("reason", "")
    return (status + (": " + reason if reason else "")).strip()


def _twister_status(tw_out):
    """Return just the twister testsuite status string ('passed'/'failed'/'error'/...)."""
    jpath = os.path.join(tw_out, "twister.json")
    try:
        with open(jpath, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return ""
    suites = data.get("testsuites", [])
    return suites[0].get("status", "") if suites else ""


def flash_demo(build_dir, demo, speed, outdir, env, timeout):
    """west flash the built image. Returns (status, log_path, reason)."""
    log_path = os.path.join(outdir, "logs", "{}_{}_flash.log".format(demo["key"], speed))
    cmd = [WEST, "flash", "-d", build_dir]
    rc, out = run_cmd(cmd, log_path, cwd=os.path.dirname(build_dir), env=env, timeout=timeout)
    if rc == 0:
        return PASS, log_path, ""
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return FAIL, log_path, "west flash failed (rc={}) {}".format(rc, tail)


# =============================================================================
# report generation
# =============================================================================
def _badge(status):
    color = {
        PASS: "#1a7f37", FAIL: "#cf222e", SKIP: "#9a6700", NA: "#57606a",
    }.get(status, "#57606a")
    return '<span class="badge" style="background:{}">{}</span>'.format(color, status)


def write_reports(results, meta, outdir):
    stamp = meta.get("stamp", "")
    base = "usb_validation_report" + ("_" + stamp if stamp else "")
    json_path = os.path.join(outdir, base + ".json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "results": results}, fh, indent=2)

    rows = []
    for r in results:
        note = r.get("device") or r.get("txn_detail") or r.get("reason") or r.get("note") or ""
        rows.append(
            "<tr>"
            "<td>{demo}</td><td class='c'>{speed}</td>"
            "<td class='c'>{build}</td><td class='c'>{flash}</td><td class='c'>{enum}</td>"
            "<td class='c'>{txn}</td><td>{note}</td>"
            "</tr>".format(
                demo=html.escape(r["demo"]),
                speed=html.escape(r["speed"].upper()),
                build=_badge(r["build"]),
                flash=_badge(r["flash"]),
                enum=_badge(r["enum"]),
                txn=_badge(r.get("txn", NA)),
                note=html.escape(note),
            )
        )

    total = len(results)
    enum_pass = sum(1 for r in results if r["enum"] == PASS)
    build_pass = sum(1 for r in results if r["build"] == PASS)
    flash_pass = sum(1 for r in results if r["flash"] == PASS)
    txn_total = sum(1 for r in results if r.get("txn") in (PASS, FAIL))
    txn_pass = sum(1 for r in results if r.get("txn") == PASS)
    # a run is "failed" if it should have enumerated but didn't, or its transaction failed
    failed = [r for r in results
              if (r["enum"] != PASS and r["build"] != SKIP) or r.get("txn") == FAIL]

    failed_html = "".join(
        "<li>{} <b>{}</b> &mdash; {}</li>".format(
            html.escape(r["demo"]), html.escape(r["speed"].upper()),
            html.escape(r.get("reason") or "did not enumerate"))
        for r in failed
    ) or "<li>None &#127881;</li>"

    doc = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PIC32CK USB HS/FS Validation Report</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}}
 header{{background:#0b3d5c;color:#fff;padding:22px 28px}}
 header h1{{margin:0;font-size:20px}} header p{{margin:6px 0 0;opacity:.85;font-size:13px}}
 .wrap{{max-width:1100px;margin:22px auto;padding:0 18px}}
 .cards{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px}}
 .card{{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:14px 18px;min-width:130px}}
 .card .n{{font-size:26px;font-weight:700}} .card .l{{font-size:12px;color:#57606a;text-transform:uppercase}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d0d7de;border-radius:10px;overflow:hidden}}
 th,td{{padding:9px 12px;border-bottom:1px solid #eaeef2;font-size:14px;text-align:left}}
 th{{background:#eef2f6;font-size:12px;text-transform:uppercase;color:#4b5563}}
 td.c{{text-align:center}}
 .badge{{color:#fff;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}}
 .fail{{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:8px 20px;margin-top:18px}}
 code{{background:#eef2f6;padding:1px 5px;border-radius:4px}}
 footer{{max-width:1100px;margin:12px auto 40px;padding:0 18px;color:#57606a;font-size:12px}}
</style></head><body>
<header>
 <h1>PIC32CK USB HS / FS Demo Validation</h1>
 <p>Board: <b>{board}</b> &nbsp;|&nbsp; Flash: <b>{flasher}</b> &nbsp;|&nbsp; Generated: {when}</p>
 <p>Zephyr commit: <code>{short}</code> [{branch}] {subject}</p>
 <p>Enumeration criterion: a USB device with <code>VID_{vid}</code> appears after flashing.</p>
</header>
<div class="wrap">
 <div class="cards">
  <div class="card"><div class="n">{total}</div><div class="l">Runs</div></div>
  <div class="card"><div class="n">{build_pass}/{total}</div><div class="l">Build OK</div></div>
  <div class="card"><div class="n">{flash_pass}/{total}</div><div class="l">Flash OK</div></div>
  <div class="card"><div class="n">{enum_pass}/{total}</div><div class="l">Enumerated</div></div>
  <div class="card"><div class="n">{txn_pass}/{txn_total}</div><div class="l">Transactions</div></div>
 </div>
 <table>
  <thead><tr><th>Demo</th><th>Speed</th><th>Build</th><th>Flash</th><th>Enumerate</th><th>Transact</th><th>Detail</th></tr></thead>
  <tbody>{rows}</tbody>
 </table>
 <div class="fail"><h3>Failed demos</h3><ul>{failed}</ul></div>
</div>
<footer>Report: usb_validation_report.html &middot; machine-readable: usb_validation_report.json &middot;
per-run logs in <code>logs\\</code>.</footer>
</body></html>""".format(
        board=html.escape(meta["board"]), flasher=html.escape(meta["flasher"]),
        when=html.escape(meta["generated"]), vid=html.escape(meta["vid"]),
        short=html.escape(meta.get("short", "") or "n/a"),
        branch=html.escape(meta.get("branch", "") or "?"),
        subject=html.escape(meta.get("commit_subject", "") or ""),
        total=total, build_pass=build_pass, flash_pass=flash_pass, enum_pass=enum_pass,
        txn_pass=txn_pass, txn_total=txn_total,
        rows="".join(rows), failed=failed_html,
    )
    html_path = os.path.join(outdir, base + ".html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return html_path, json_path


# =============================================================================
# udc driver-test mode (tests/drivers/udc via twister --device-testing)
# =============================================================================
def write_udc_report(results, meta, outdir):
    """Write a small HTML/JSON report for the udc driver-test run."""
    stamp = meta.get("stamp", "")
    base = "udc_report" + ("_" + stamp if stamp else "")
    json_path = os.path.join(outdir, base + ".json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "results": results}, fh, indent=2)

    rows = "".join(
        "<tr><td class='c'>{sp}</td><td class='c'>{res}</td><td>{det}</td></tr>".format(
            sp=html.escape(r["speed"].upper()), res=_badge(r["result"]),
            det=html.escape(r.get("detail", "")))
        for r in results)
    passed = sum(1 for r in results if r["result"] == PASS)
    doc = """<!doctype html><html><head><meta charset="utf-8">
<title>PIC32CK UDC driver test</title><style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}}
 header{{background:#0b3d5c;color:#fff;padding:22px 28px}} header h1{{margin:0;font-size:20px}}
 header p{{margin:6px 0 0;opacity:.85;font-size:13px}} .wrap{{max-width:900px;margin:22px auto;padding:0 18px}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d0d7de;border-radius:10px;overflow:hidden}}
 th,td{{padding:9px 12px;border-bottom:1px solid #eaeef2;font-size:14px;text-align:left}} td.c{{text-align:center}}
 th{{background:#eef2f6;font-size:12px;text-transform:uppercase;color:#4b5563}}
 .badge{{color:#fff;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}}</style></head><body>
<header><h1>PIC32CK UDC driver test (tests/drivers/udc)</h1>
<p>Board: <b>{board}</b> | Commit: <code>{short}</code> [{branch}] | {when}</p>
<p>Run via <code>twister --device-testing</code> with the USB device cable disconnected (ztest verdict over serial).</p></header>
<div class="wrap"><p><b>{passed}/{total}</b> speed(s) passed.</p>
<table><thead><tr><th>Speed</th><th>Result</th><th>Detail</th></tr></thead><tbody>{rows}</tbody></table>
</div></body></html>""".format(
        board=html.escape(meta["board"]), short=html.escape(meta.get("short", "") or "n/a"),
        branch=html.escape(meta.get("branch", "") or "?"), when=html.escape(meta["generated"]),
        passed=passed, total=len(results), rows=rows)
    html_path = os.path.join(outdir, base + ".html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return html_path, json_path


def run_udc_tests(args, board, req_speeds, dts_path, env, run_stamp, commit_info):
    """
    Run tests/drivers/udc as a ztest via twister --device-testing, with the USB
    device cable disconnected (ykush off) per speed. Returns process exit code.
    """
    tw = twister_path(args.zephyr_base)
    if not tw:
        log_line("ERROR: twister not found; --udc requires twister.")
        return 2
    serial = args.device_serial or args.console or find_console_port()
    if not serial:
        log_line("ERROR: --udc needs the board serial console; pass --device-serial COMx")
        return 2

    udc_root = os.path.join(args.zephyr_base, "tests", "drivers", "udc")
    if not os.path.isdir(udc_root):
        log_line("ERROR: {} not found in this tree.".format(udc_root))
        return 2
    usbfs_snippet = find_usbfs_snippet(args.zephyr_base)

    log_line("Mode        : UDC driver tests (tests/drivers/udc)")
    log_line("device-serial: {} @ {} baud".format(serial, args.console_baud))
    log_line("USB cable   : will be DISCONNECTED per speed (no host during udc test)")

    def _cable(speed, on):
        if not getattr(args, "ykush_active", False):
            return
        port = args.ykush_port_hs if speed == "hs" else args.ykush_port_fs
        if port:
            ykush_power(port, on, args.ykush_serial or None)
            log_line("ykush       : {} connector (port {}) -> {}".format(
                speed.upper(), port, "ON" if on else "OFF"))

    results = []
    for speed in req_speeds:
        if speed not in board["speeds"]:
            results.append(dict(speed=speed, result=SKIP,
                                detail="board has no {} USB controller".format(speed.upper())))
            continue
        if not getattr(args, "ykush_active", False):
            log_line("NOTE: disconnect the {} USB device cable from the host before this "
                     "udc test (no ykush configured).".format(speed.upper()))
        _cable(speed, False)   # disconnect host for the test

        snippet = usbfs_snippet if (speed == "fs" and usbfs_snippet) else None
        need_patch = (speed == "fs" and not usbfs_snippet and board["fs_needs_patch"])
        patched = apply_fs_patch(dts_path) if need_patch else False
        try:
            if need_patch and not patched:
                results.append(dict(speed=speed, result=SKIP,
                                    detail="FS device-tree patch unavailable"))
                continue
            # Keep twister's deep build tree OUT of the long timestamped run
            # path (Windows MAX_PATH: the SDK archiver isn't long-path aware).
            # Use a short scratch dir near the workspace root.
            tw_out = os.path.join(os.path.dirname(HERE), ".udc", speed)
            log_path = os.path.join(args.outdir, "logs", "udc_{}.log".format(speed))
            log_line("UDC    [{}] build dir: {}".format(speed.upper(), tw_out))
            cmd = [PYEXE, tw, "-p", board["name"], "-s", "drivers.usb.udc", "-T", udc_root,
                   "--device-testing", "--device-serial", serial,
                   "--device-serial-baud", str(args.console_baud),
                   "-O", tw_out, "--clobber-output", "-v"]
            if snippet:
                cmd += ["--extra-args=SNIPPET={}".format(snippet)]
            log_line("UDC    [{}] twister device-testing ...".format(speed.upper()))
            rc, _ = run_cmd(cmd, log_path, cwd=args.zephyr_base, env=env,
                            timeout=args.build_timeout + args.flash_timeout + 300)
            status = _twister_status(tw_out)
            result = PASS if status == "passed" else FAIL
            detail = _twister_reason(tw_out) or "twister rc={}".format(rc)
            results.append(dict(speed=speed, result=result, detail=detail,
                                log=os.path.relpath(log_path, args.outdir)))
            log_line("UDC    [{}] -> {}  ({})".format(speed.upper(), result, detail))
        finally:
            if patched:
                restore_dts(dts_path)
            _cable(speed, True)    # reconnect

    meta = dict(board=board["name"], generated=ts(), stamp=run_stamp,
                short=commit_info.get("short", ""), branch=commit_info.get("branch", ""))
    hpath, jpath = write_udc_report(results, meta, args.outdir)

    print("\n" + "=" * 60)
    print("UDC DRIVER TESTS  (tests/drivers/udc)")
    print("=" * 60)
    for r in results:
        print("  {:4s} {:6s} {}".format(r["speed"].upper(), r["result"], r.get("detail", "")))
    bad = [r["speed"] for r in results if r["result"] == FAIL]
    print("-" * 60)
    print("Failed: {}".format(", ".join(bad) or "none"))
    print("\nHTML report: {}".format(hpath))
    print("JSON report: {}".format(jpath))
    return 0


def run_samples_phase(args, board, selected, demo_index, req_speeds, dts_path,
                      env, run_stamp, commit_info, usbfs_snippet):
    """The samples sweep: build -> flash -> enumerate (+ optional transactions)."""
    board_speeds = board["speeds"]
    if "fs" in req_speeds and "fs" in board_speeds:
        if usbfs_snippet:
            log_line("FS method   : snippet '{}' (-S {})".format(usbfs_snippet, usbfs_snippet))
        elif board["fs_needs_patch"]:
            log_line("FS method   : temporary board .dts patch")
        else:
            log_line("FS method   : board default (FS is native)")

    results = []
    try:
        for speed in req_speeds:
            if speed not in board_speeds:
                log_line("Speed {} not supported by {}; skipping those runs.".format(
                    speed.upper(), board["name"]))
                for key in selected:
                    d = demo_index[key]
                    results.append(dict(demo=key, speed=speed, note=d["note"],
                                        build=SKIP, flash=SKIP, enum=SKIP,
                                        reason="board has no {} USB controller".format(speed.upper())))
                continue

            if not args.build_only:
                ykush_set_for_speed(speed, args)

            snippet = usbfs_snippet if (speed == "fs" and usbfs_snippet) else None
            need_patch = (speed == "fs" and not usbfs_snippet and board["fs_needs_patch"])
            fs_active = True
            if need_patch:
                fs_active = apply_fs_patch(dts_path)
                if not fs_active:
                    log_line("FS patch failed; recording FS runs as skipped.")

            try:
                if need_patch and not fs_active:
                    for key in selected:
                        d = demo_index[key]
                        results.append(dict(demo=key, speed=speed, note=d["note"],
                                            build=SKIP, flash=SKIP, enum=SKIP,
                                            reason="FS device-tree patch unavailable"))
                    continue
                for key in selected:
                    run_one_demo(demo_index[key], speed, board["name"], args, env,
                                 results, snippet=snippet)
            finally:
                if need_patch and fs_active:
                    restore_dts(dts_path)
    finally:
        restore_dts(dts_path)
        ykush_restore_all(args)

    meta = dict(board=board["name"], flasher=args.flasher, vid=args.vid,
                generated=ts(), stamp=run_stamp, zephyr_base=args.zephyr_base,
                build_only=args.build_only,
                commit=commit_info.get("commit", ""), short=commit_info.get("short", ""),
                branch=commit_info.get("branch", ""), commit_subject=commit_info.get("subject", ""))
    html_path, json_path = write_reports(results, meta, args.outdir)

    print("\n" + "=" * 68)
    print("SAMPLES SUMMARY  ({} runs)".format(len(results)))
    print("=" * 68)
    print("{:20s} {:5s} {:6s} {:6s} {:6s} {:6s}".format(
        "DEMO", "SPD", "BUILD", "FLASH", "ENUM", "TXN"))
    for r in results:
        print("{:20s} {:5s} {:6s} {:6s} {:6s} {:6s} {}".format(
            r["demo"], r["speed"].upper(), r["build"], r["flash"], r["enum"],
            r.get("txn", NA), r.get("reason", "")))
    print("-" * 68)
    if args.build_only:
        bad = [r for r in results if r["build"] == FAIL]
        print("Build failures: {}".format(
            ", ".join("{}[{}]".format(r["demo"], r["speed"]) for r in bad) or "none"))
    else:
        failed = [r for r in results
                  if (r["enum"] != PASS and r["build"] != SKIP) or r.get("txn") == FAIL]
        print("Failed demos: {}".format(
            ", ".join("{}[{}]".format(r["demo"], r["speed"]) for r in failed) or "none"))
    print("HTML report: {}".format(html_path))
    print("JSON report: {}".format(json_path))


# =============================================================================
# main
# =============================================================================
def parse_args():
    ap = argparse.ArgumentParser(description="PIC32CK USB HS/FS demo validation")
    ap.add_argument("--zephyr-base", default="",
                    help="Zephyr base or workspace root (default: auto-detect / prompt)")
    ap.add_argument("--board", default="",
                    help="board name (default: prompt with a selection menu)")
    ap.add_argument("--demo", default="",
                    help="run a single demo by key, e.g. --demo cdc_acm (default: all)")
    ap.add_argument("--demos", default="", help="comma list of demo keys (default: all)")
    ap.add_argument("--speeds", default=",".join(demos.SPEEDS), help="comma list: hs,fs")
    ap.add_argument("--vid", default=demos.DEFAULT_VID, help="USB VID to look for (hex, no 0x)")
    ap.add_argument("--flasher", choices=["west", "twister"], default="west")
    ap.add_argument("--west", default="", help="path to west.exe (default: auto-detect venv)")
    ap.add_argument("--venv", default="", help="path to the Zephyr venv (contains Scripts\\west.exe)")
    ap.add_argument("--ipecmd", default="",
                    help="path to ipecmd.exe or its folder (default: auto-detect MPLAB IPE)")
    ap.add_argument("--build-only", action="store_true", help="build test only; no flash/enum")
    ap.add_argument("--pristine", choices=["auto", "always", "never"], default="auto",
                    help="west build pristine mode (default: auto = fast incremental)")
    ap.add_argument("--build-cache", default="",
                    help="dir for cached build artifacts (default: <outdir>\\.build_cache)")
    ap.add_argument("--no-cache", action="store_true",
                    help="disable the build cache (build inside the timestamped run dir)")
    ap.add_argument("--console", default="",
                    help="serial console COM port for demos that need shell init "
                         "(e.g. shell); auto-detected if omitted")
    ap.add_argument("--console-baud", type=int, default=115200,
                    help="baud rate for --console (default 115200)")
    ap.add_argument("--ykush-port-hs", type=int, default=0,
                    help="YKUSH port for the HS (USB-C) device connector; "
                         "enables ykush for HS runs (0 = disabled)")
    ap.add_argument("--ykush-port-fs", type=int, default=0,
                    help="YKUSH port for the FS (Micro-B) device connector; "
                         "enables ykush for FS runs (0 = disabled)")
    ap.add_argument("--ykush-serial", default="",
                    help="target a specific YKUSH hub by serial number")
    ap.add_argument("--udc", action="store_true",
                    help="ALSO run the tests/drivers/udc driver ztests (via twister "
                         "--device-testing, cable disconnected) after the samples sweep")
    ap.add_argument("--udc-only", action="store_true",
                    help="run ONLY the udc driver tests (skip the samples sweep)")
    ap.add_argument("--device-serial", default="",
                    help="serial console COM port for --udc (default: --console / auto-detect)")
    ap.add_argument("--transactions", action="store_true",
                    help="also run post-enumeration data transactions (e.g. CDC 64 KiB "
                         "echo) for demos that support them; OFF by default "
                         "(default is build + flash + enumerate only)")
    ap.add_argument("--transaction-size", type=int, default=65536,
                    help="bytes for data transactions when --transactions is set "
                         "(default 65536 = 64 KiB)")
    ap.add_argument("--enum-timeout", type=int, default=30)
    ap.add_argument("--build-timeout", type=int, default=1800)
    ap.add_argument("--flash-timeout", type=int, default=600)
    ap.add_argument("--outdir", default=os.path.join(HERE, "results"))
    ap.add_argument("--list", action="store_true", help="list the demo matrix and exit")
    ap.add_argument("--list-boards", action="store_true",
                    help="list discovered USB-capable boards and exit")
    ap.add_argument("--restore-dts", metavar="BOARD", nargs="?", const="",
                    help="restore a board's .dts from backup and exit "
                         "(optionally name the board)")
    return ap.parse_args()


def run_one_demo(demo, speed, board, args, env, results, snippet=None):
    """Build+flash+enumerate a single demo. Never raises: records the outcome and returns."""
    key = demo["key"]
    label = "{} [{}]".format(key, speed.upper())
    log_line("================ {} ================".format(label))
    rec = dict(demo=key, speed=speed, note=demo["note"],
               build=NA, flash=NA, enum=NA, txn=NA, reason="", device="")

    # the mass_file transaction needs the FAT build variant (RAM disk + fatfs)
    txn_kind = demos.TRANSACTIONS.get(key)
    extra_args, variant = None, ""
    if args.transactions and txn_kind == "mass_file":
        overlay = os.path.join(HERE, "overlays", "mass_ramdisk.overlay").replace("\\", "/")
        extra_args = ["-DCONFIG_APP_MSC_STORAGE_RAM=y",
                      "-DEXTRA_DTC_OVERLAY_FILE=" + overlay]
        variant = "fat"

    try:
        # ---- build ----
        log_line("BUILD  {} ...".format(label))
        if args.flasher == "west":
            bstatus, build_dir, blog, breason = build_with_west(
                demo, speed, board, args.zephyr_base, args.outdir, env, args.build_timeout,
                pristine=args.pristine, cache_dir=getattr(args, "build_cache_dir", None),
                snippet=snippet, extra_args=extra_args, variant=variant)
        else:
            bstatus, build_dir, blog, breason = build_with_twister(
                demo, speed, board, args.zephyr_base, args.outdir, env, args.build_timeout,
                snippet=snippet, extra_args=extra_args, variant=variant)
        rec["build"] = bstatus
        rec["build_log"] = os.path.relpath(blog, args.outdir)
        if breason:
            rec["reason"] = breason
        log_line("BUILD  {} -> {} {}".format(label, bstatus, breason))

        if bstatus != PASS or args.build_only:
            if bstatus == SKIP:
                rec["flash"] = rec["enum"] = SKIP
            else:
                rec["flash"] = rec["enum"] = NA
            return

        # ---- flash ----
        baseline = query_usb_devices()
        _mass_txn = args.transactions and txn_kind == "mass_file"
        pre_drives = list_drive_letters() if _mass_txn else set()
        pre_usb_disks = list_usb_disks() if _mass_txn else set()
        log_line("FLASH  {} ...".format(label))
        fstatus, flog, freason = flash_demo(
            build_dir, demo, speed, args.outdir, env, args.flash_timeout)
        rec["flash"] = fstatus
        rec["flash_log"] = os.path.relpath(flog, args.outdir)
        if freason:
            rec["reason"] = freason
        log_line("FLASH  {} -> {} {}".format(label, fstatus, freason))
        if fstatus != PASS:
            rec["enum"] = NA
            return

        # ---- console init: some samples need shell commands to bring USB up ----
        # (e.g. the USB shell sample does not auto-enable; it waits at uart:~$
        #  for `usbd defcfg` then `usbd enable` before it enumerates.)
        init_cmds = demos.CONSOLE_INIT.get(demo["key"])
        if init_cmds:
            port = args.console or find_console_port()
            if port:
                log_line("CONSOLE {} -> {} @ {} baud : {}".format(
                    label, port, args.console_baud, " ; ".join(init_cmds)))
                ok_c, cout = send_console_commands(port, init_cmds, args.console_baud)
                if not ok_c:
                    busy = "access is denied" in cout.lower() or "permissionerror" in cout.lower()
                    hint = ("port busy - close any serial terminal on {p} (or replug the "
                            "DEBUG USB to reset the VCOM)".format(p=port) if busy else
                            "check the console port / baud, or pass --console COMx")
                    log_line("       console send failed ({}): {}".format(hint, cout.strip()[:160]))
                    rec["reason"] = "console init failed on {}: {}".format(port, hint)
                else:
                    conf = [ln.strip() for ln in cout.splitlines()
                            if "enabl" in ln.lower() or "initialized" in ln.lower()]
                    if conf:
                        log_line("       console: {}".format(conf[-1][:120]))
            else:
                log_line("       no serial console auto-detected - pass --console COMx "
                         "(the '{}' demo needs shell 'usbd enable' to enumerate)".format(demo["key"]))
                rec["reason"] = ("needs serial console to run {}; pass --console COMx".format(
                    "/".join(init_cmds)))

        # ---- enumerate ----
        log_line("ENUM   {} (waiting up to {}s) ...".format(label, args.enum_timeout))
        estatus, matched, new = wait_for_enumeration(baseline, args.vid, args.enum_timeout)
        rec["enum"] = estatus
        if matched:
            rec["device"] = "; ".join(
                "{} ({})".format(n or "?", i.split("\\")[1] if "\\" in i else i)
                for i, n in matched[:3])
        elif new:
            broken = [(i, n) for i, n in new.items()
                      if "VID_0000" in i.upper() or "descriptor" in (n or "").lower()
                      or "unknown usb" in (n or "").lower()]
            if broken:
                rec["device"] = "enumeration FAILED at host: " + "; ".join(
                    "{} [{}]".format(n or "?", i) for i, n in broken[:3])
                rec["reason"] = ("device attached but Windows could not read its "
                                 "descriptors (likely USB driver/enumeration bug)")
            else:
                rec["device"] = "new (no VID_{}): ".format(args.vid) + "; ".join(
                    list(new.values())[:3])
                rec["reason"] = "new USB device(s) appeared but none with VID_{}".format(args.vid)
        else:
            rec["reason"] = ("no USB device appeared within {}s - is the board's USB "
                             "*device* port (not the debugger) connected?".format(args.enum_timeout))
        log_line("ENUM   {} -> {} {}".format(label, estatus, rec.get("device", "")))
        if estatus != PASS:
            if new:
                log_line("       new USB devices seen after flash:")
                for i, n in list(new.items())[:6]:
                    log_line("         - {}  [{}]".format(n or "?", i))
            else:
                log_line("       no new USB devices detected (check the USB *device* "
                         "cable / try increasing --enum-timeout)")

        # ---- data transaction (verify real transfer, not just enumeration) ----
        if txn_kind and estatus == PASS and args.transactions:
            if txn_kind == "cdc_echo":
                cport = find_cdc_port(args.vid)
                if not cport:
                    rec["txn"] = FAIL
                    rec["reason"] = rec.get("reason") or \
                        "no CDC COM port (VID_{}) found for transaction".format(args.vid)
                    log_line("TXN    {} -> FAIL ({})".format(label, rec["reason"]))
                else:
                    log_line("TXN    {} cdc-echo {} bytes on {} ...".format(
                        label, args.transaction_size, cport))
                    ok_t, det = cdc_echo_transaction(cport, args.transaction_size, args.console_baud)
                    rec["txn"] = PASS if ok_t else FAIL
                    rec["txn_detail"] = det
                    log_line("TXN    {} -> {}  {}".format(label, rec["txn"], det))
                    if not ok_t and not rec.get("reason"):
                        rec["reason"] = "CDC echo transaction failed: {}".format(det)
            elif txn_kind == "mass_file":
                drive = wait_for_new_drive(pre_drives, 20)
                if drive:
                    log_line("TXN    {} mass-file {} bytes on {} ...".format(
                        label, args.transaction_size, drive))
                    ok_t, det = mass_file_transaction(drive, args.transaction_size)
                    rec["txn"] = PASS if ok_t else FAIL
                    rec["txn_detail"] = det
                    log_line("TXN    {} -> {}  {}".format(label, rec["txn"], det))
                    if not ok_t and not rec.get("reason"):
                        rec["reason"] = "mass-storage file transaction failed: {}".format(det)
                else:
                    rec["txn"] = FAIL
                    new_usb = list_usb_disks() - pre_usb_disks
                    err = usb_device_error_code(args.vid)
                    if err:
                        why = ("USB mass storage BLOCKED by host policy (device status "
                               "Error: {}) - USB device-control / DLP (e.g. SentinelOne, "
                               "Microsoft Purview). Allow-list the device or use an "
                               "unmanaged PC.".format(err))
                    elif new_usb:
                        why = ("MSC disk present ({}) but Windows mounted no volume "
                               "(RAW/unformatted/offline)".format("; ".join(sorted(new_usb))[:80]))
                    else:
                        why = ("device enumerated as USB Mass Storage but Windows created "
                               "no disk (no drive letter / no USB disk)")
                    rec["txn_detail"] = why
                    rec["reason"] = rec.get("reason") or why
                    log_line("TXN    {} -> FAIL  {}".format(label, why))
    except Exception as exc:  # noqa: BLE001 - one demo must never abort the whole run
        import traceback
        rec["reason"] = "unexpected error: {}".format(exc)
        if rec["build"] == NA:
            rec["build"] = FAIL
        log_line("ERROR  {} -> {}".format(label, exc))
        log_line(traceback.format_exc())
    finally:
        results.append(rec)


def main():
    args = parse_args()

    # resolve a valid Zephyr base (portable; no hardcoded machine path)
    args.zephyr_base = resolve_zephyr_base(args)
    if not args.zephyr_base:
        log_line("ERROR: could not locate a valid Zephyr base. "
                 "Pass --zephyr-base <path to ...\\zephyr>.")
        return 2

    # auto-discover USB device demos from the tree (falls back to a static list)
    discovered = demos.discover_usb_demos(args.zephyr_base)
    all_demos = discovered if discovered else list(demos.FALLBACK_DEMOS)
    demo_index = {d["key"]: d for d in all_demos}
    all_keys = [d["key"] for d in all_demos]

    # demo matrix listing
    if args.list:
        src = "auto-discovered from tree" if discovered else "fallback list (discovery found nothing)"
        print("USB device demos ({}; speeds: {}):\n".format(src, ", ".join(demos.SPEEDS)))
        for d in all_demos:
            bo = "  [build-only]" if d.get("build_only") else ""
            print("  {:22s} {:30s} {}{}".format(d["key"], d["path"], d["note"], bo))
        return 0

    # ---- restore-dts (emergency) -------------------------------------------
    if args.restore_dts is not None:
        boards = discover_boards(args.zephyr_base)
        targets = []
        if args.restore_dts:  # a board name was given
            b = next((x for x in boards if x["name"] == args.restore_dts), None)
            if b and b["dts"]:
                targets = [b["dts"]]
            else:
                targets = [os.path.join(args.zephyr_base, BOARD_DTS_REL)]
        else:  # restore any board that still has a backup
            targets = [b["dts"] for b in boards if b["dts"]
                       and os.path.exists(b["dts"] + BAK_SUFFIX)]
            if not targets:
                targets = [os.path.join(args.zephyr_base, BOARD_DTS_REL)]
        any_done = False
        for t in targets:
            if restore_dts(t):
                print("Restored:", t)
                any_done = True
        if not any_done:
            print("No backup found; nothing to restore.")
        return 0

    boards = discover_boards(args.zephyr_base)

    if args.list_boards:
        print_board_table(boards)
        return 0

    # ---- board selection ----------------------------------------------------
    board = select_board(boards, args.board)
    dts_path = board["dts"] or os.path.join(args.zephyr_base, BOARD_DTS_REL)
    board_speeds = board["speeds"]

    # --demo (single) takes precedence over --demos (list); default is all
    if args.demo.strip():
        selected = [args.demo.strip()]
    else:
        selected = [d.strip() for d in args.demos.split(",") if d.strip()] or all_keys

    unknown = [k for k in selected if k not in demo_index]
    if unknown:
        log_line("ERROR: unknown demo(s): {}".format(", ".join(unknown)))
        log_line("Valid demos: {}".format(", ".join(all_keys)))
        return 2

    req_speeds = [s.strip().lower() for s in args.speeds.split(",") if s.strip()]

    # ---- timestamped per-run output directory (nothing is overwritten) ------
    run_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_outdir = args.outdir
    args.outdir = os.path.join(base_outdir, "run_" + run_stamp)
    os.makedirs(os.path.join(args.outdir, "logs"), exist_ok=True)

    # ---- build cache (stable dir -> fast incremental rebuilds) --------------
    if args.no_cache:
        args.build_cache_dir = None
    else:
        args.build_cache_dir = args.build_cache or os.path.join(base_outdir, ".build_cache")
        os.makedirs(args.build_cache_dir, exist_ok=True)

    # ---- resolve toolchain (find west even if the venv isn't activated) -----
    env, west_found = resolve_toolchain(args)
    ok, fatal, warn = preflight(env, need_flash=not args.build_only)
    for w in warn:
        log_line("WARNING: " + w)
    if not ok:
        log_line("PREFLIGHT FAILED - not starting builds:")
        for f in fatal:
            for ln in f.splitlines():
                print("    " + ln)
        return 2

    import shutil as _sh
    ccache = _sh.which("ccache", path=env.get("PATH", ""))

    commit_info = git_commit_info(args.zephyr_base)
    log_line("Zephyr base : {}".format(args.zephyr_base))
    if commit_info:
        log_line("Git commit  : {}  [{}]".format(commit_info["short"], commit_info["branch"]))
        if commit_info.get("subject"):
            log_line("             {}".format(commit_info["subject"]))
    log_line("Output dir  : {}".format(args.outdir))
    log_line("Build cache : {}".format(args.build_cache_dir or "(disabled)"))
    log_line("Pristine    : {}".format(args.pristine))
    log_line("ccache      : {}".format(
        ccache or "(not found - 'choco install ccache' speeds the first build a lot)"))
    log_line("west        : {}".format(WEST))
    if not args.build_only:
        log_line("ipecmd      : {}".format(IPECMD or "(not found - flashing will fail)"))
    if args.flasher == "twister":
        log_line("python      : {}".format(PYEXE))
    log_line("Board       : {}  ({})".format(board["name"], board["full_name"]))
    log_line("Board speeds: {}".format("/".join(s.upper() for s in board_speeds)))
    log_line("Demos found : {} usbd samples {}".format(
        len(all_keys), "(auto-discovered)" if discovered else "(fallback list)"))
    log_line("Demos       : {}".format(", ".join(selected)))
    log_line("Speeds req. : {}".format(", ".join(req_speeds)))
    log_line("Flasher     : {}".format(args.flasher))
    log_line("Build-only  : {}".format(args.build_only))

    # detect the hub once; if requested but absent, warn and continue (assume
    # cables are connected manually) rather than failing per speed.
    args.ykush_active = False
    if ykush_enabled(args):
        found, info = ykush_present(args.ykush_serial or None)
        if found:
            args.ykush_active = True
            log_line("ykush       : {} - HS->port {}  FS->port {}".format(
                info or "hub detected", args.ykush_port_hs or "-", args.ykush_port_fs or "-"))
        else:
            log_line("WARNING: --ykush-port-* set but no YKUSH hub found; cable "
                     "switching DISABLED - assuming device cables are connected manually.")

    # ---- phases: samples sweep (default) and/or udc driver tests -----------
    usbfs_snippet = find_usbfs_snippet(args.zephyr_base)
    dts_path = board["dts"] or os.path.join(args.zephyr_base, BOARD_DTS_REL)

    if not args.udc_only:
        run_samples_phase(args, board, selected, demo_index, req_speeds, dts_path,
                          env, run_stamp, commit_info, usbfs_snippet)

    if args.udc or args.udc_only:
        try:
            run_udc_tests(args, board, req_speeds, dts_path, env, run_stamp, commit_info)
        finally:
            restore_dts(dts_path)      # udc FS-patch fallback safety
            ykush_restore_all(args)    # leave connectors powered on

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # ensure every board .dts is restored on Ctrl-C
        base = os.environ.get("ZEPHYR_BASE", DEFAULT_ZEPHYR_BASE)
        restored = False
        try:
            for b in discover_boards(base):
                if b["dts"] and os.path.exists(b["dts"] + BAK_SUFFIX):
                    restore_dts(b["dts"])
                    restored = True
        except Exception:  # noqa: BLE001
            pass
        if not restored:
            restore_dts(os.path.join(base, BOARD_DTS_REL))
        print("\nInterrupted; board .dts restored if it was patched.")
        sys.exit(130)
