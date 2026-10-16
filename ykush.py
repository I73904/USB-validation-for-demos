# -*- coding: utf-8 -*-
"""
YKUSH switchable USB hub control (Yepkit).

Use as a module::

    import ykush
    ykush.power(port=1, on=False)     # cut power to the USB device cable
    ykush.power(port=1, on=True)      # restore power (device re-enumerates)
    state = ykush.status(port=1)      # True=on, False=off, None=error

or from the command line::

    python ykush.py off 1             # power off port 1
    python ykush.py on  1             # power on  port 1
    python ykush.py status 1          # query port 1
    python ykush.py cycle 1 --off-duration 3

Requires the ``hid`` package (present in the Zephyr venv). ``hid`` is imported
lazily so importing this module never fails when YKUSH isn't being used.

Protocol (from the Yepkit ykushcmd firmware, verified against a working script):
  - 64-byte HID output report, first byte is the Report ID (0x00), command next.
  - Response[0] == 0x01 on success; for status, upper nibble of response[1]
    non-zero means the port is powered.
"""

import argparse
import sys
import time

YKUSH_VID = 0x04D8
KNOWN_PIDS = {
    0xF2F7: "YKUSH",
    0xF11B: "YKUSH3",
    0xF0CD: "YKUSHXS",
    0x0042: "YKUSH (legacy)",
}
HID_REPORT_SIZE = 64


# ---- command byte helpers ---------------------------------------------------
def _validate(port, allow_all=True):
    if port == 0 and allow_all:
        return
    if port not in (1, 2, 3):
        raise ValueError("Invalid YKUSH port {} (use 1-3, or 0 for all)".format(port))


def _down_cmd(port):
    _validate(port)
    return 0x0A if port == 0 else port            # 0x01/0x02/0x03, 0x0A=all


def _up_cmd(port):
    _validate(port)
    return 0x1A if port == 0 else 0x10 + port      # 0x11/0x12/0x13, 0x1A=all


def _status_cmd(port):
    _validate(port, allow_all=False)
    return 0x20 + port                             # 0x21/0x22/0x23


# ---- device access ----------------------------------------------------------
def open_ykush(serial=None):
    """Open the first YKUSH found (optionally by serial). Returns (device, pid, name)."""
    import hid  # lazy: only needed when actually talking to the hub
    last_err = None
    for pid, name in KNOWN_PIDS.items():
        try:
            dev = hid.device()
            if serial:
                dev.open(YKUSH_VID, pid, serial)
            else:
                dev.open(YKUSH_VID, pid)
            return dev, pid, name
        except OSError as exc:
            last_err = exc
            continue
    raise RuntimeError(
        "No YKUSH hub found (VID 0x{:04X}). Check the hub is connected"
        " and the HID driver is installed. Last error: {}".format(YKUSH_VID, last_err))


def _transact(dev, command):
    dev.write([0x00, command] + [0x00] * (HID_REPORT_SIZE - 1))
    return dev.read(HID_REPORT_SIZE)


def set_port(dev, port, on):
    """Power a port on/off using an open device handle. Returns True on success."""
    resp = _transact(dev, _up_cmd(port) if on else _down_cmd(port))
    return bool(resp) and resp[0] == 0x01


def get_status_dev(dev, port):
    """Return True (on) / False (off) / None (error) for an open device handle."""
    resp = _transact(dev, _status_cmd(port))
    if not resp or resp[0] != 0x01:
        return None
    return bool(resp[1] >> 4)


# ---- one-shot convenience (open + act + close) ------------------------------
def power(port, on, serial=None):
    """Open, set the port on/off, close. Returns True on success."""
    dev, _, _ = open_ykush(serial)
    try:
        return set_port(dev, port, on)
    finally:
        dev.close()


def status(port, serial=None):
    """Open, query the port, close. Returns True/False/None."""
    dev, _, _ = open_ykush(serial)
    try:
        return get_status_dev(dev, port)
    finally:
        dev.close()


def cycle(port, off_duration=3.0, serial=None):
    """Power a port off, wait, then on. Returns True if both steps succeeded."""
    dev, _, _ = open_ykush(serial)
    try:
        ok_off = set_port(dev, port, False)
        time.sleep(off_duration)
        ok_on = set_port(dev, port, True)
        return ok_off and ok_on
    finally:
        dev.close()


# ---- CLI --------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser(description="Control a Yepkit YKUSH switchable USB hub port.")
    ap.add_argument("action", choices=["on", "off", "status", "cycle", "present"])
    ap.add_argument("port", type=int, nargs="?", default=1,
                    help="port 1-3 (0 = all, not for status); ignored for 'present'")
    ap.add_argument("--serial", default=None, help="target a specific hub by serial")
    ap.add_argument("--off-duration", type=float, default=3.0)
    args = ap.parse_args()

    try:
        if args.action == "present":
            _, pid, name = open_ykush(args.serial)
            print("found {} (PID 0x{:04X})".format(name, pid))
            return 0
        if args.action == "on":
            ok = power(args.port, True, args.serial)
        elif args.action == "off":
            ok = power(args.port, False, args.serial)
        elif args.action == "cycle":
            ok = cycle(args.port, args.off_duration, args.serial)
        else:  # status
            st = status(args.port, args.serial)
            print("port {}: {}".format(args.port,
                  "ON" if st else "OFF" if st is False else "ERROR"))
            return 0 if st is not None else 1
        print("{} port {}: {}".format(args.action, args.port, "OK" if ok else "FAILED"))
        return 0 if ok else 1
    except (RuntimeError, ValueError) as exc:
        print("ERROR: {}".format(exc))
        return 1


if __name__ == "__main__":
    sys.exit(_main())
