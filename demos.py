# -*- coding: utf-8 -*-
"""
USB device demo matrix for PIC32CK validation.

By default the demo list is **auto-discovered from the Zephyr tree** (see
`discover_usb_demos`), so pulling a branch that adds / renames / removes USB
samples is picked up automatically. `FALLBACK_DEMOS` below is only used if
discovery finds nothing (e.g. an unexpected tree layout).

A "USB device demo" here is any sample under ``samples/subsys/usb/<name>/``
whose ``sample.yaml`` declares a dependency on the new USB device stack
(``depends_on: usbd``). USB *host* samples (``usbh.*`` / ``host_uvc``) and the
legacy USB stack (``samples/subsys/usb/legacy/*``) are excluded automatically.

Discovered demo dict fields: key, path, scenario, note, build_only.
"""

import glob
import os
import re

# USB speeds to validate.
#   hs : high-speed controller (board default / hsusb0)
#   fs : full-speed controller (usb0) selected via snippet or a .dts patch
SPEEDS = ["hs", "fs"]

# Default USB Vendor ID that Zephyr USB samples enumerate with
# (CONFIG_SAMPLE_USBD_VID default = 0x2fe3, "Zephyr Project").
DEFAULT_VID = "2FE3"

# Directory names to always skip even if they look like usbd samples.
DEFAULT_EXCLUDE = {"host_uvc", "common"}

# Demos that do NOT auto-enable USB and need shell commands over the console
# (UART/VCOM) after flashing before they will enumerate. Keyed by demo key.
# The USB shell sample waits at `uart:~$` for these:
CONSOLE_INIT = {
    "shell": ["usbd defcfg", "usbd enable"],
}

# Optional post-enumeration data transaction per demo (verifies real data
# transfer, not just enumeration). Keyed by demo key -> transaction kind:
#   "cdc_echo"  -> open the CDC COM port, send N bytes, read them back, compare
#   "mass_file" -> write an N-byte file to the mounted drive, read back, compare
# (mass_file is added in a later step.)
TRANSACTIONS = {
    "cdc_acm": "cdc_echo",
}


# ---------------------------------------------------------------------------
# auto-discovery
# ---------------------------------------------------------------------------
def _as_list(x):
    # twister depends_on may be a space-separated string ("usbd gpio") or a list;
    # split on whitespace so multi-dep strings are tokenised correctly.
    if x is None:
        return []
    if isinstance(x, str):
        return x.split()
    if isinstance(x, (list, tuple)):
        out = []
        for it in x:
            if isinstance(it, str):
                out += it.split()
        return out
    return []


def _key_from_dir(dirname):
    # CLI-friendly key: hid-keyboard -> hid_keyboard
    return dirname.replace("-", "_")


def _parse_sample_yaml(path):
    """
    Return (scenario, note, build_only) if the sample is a usbd *device* sample,
    else None. Uses PyYAML when available, with a text-based fallback.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None

    try:
        import yaml  # PyYAML ships with the Zephyr venv (twister needs it)
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - fall back to text heuristic
        data = None

    if isinstance(data, dict):
        sample_name = ((data.get("sample") or {}).get("name")) or ""
        common = data.get("common") or {}
        common_dep = _as_list(common.get("depends_on"))
        common_restricted = bool(common.get("filter"))
        tests = data.get("tests") or {}

        candidates = []  # (restricted, is_variant, name, build_only)
        for tname, tbody in tests.items():
            tbody = tbody or {}
            deps = common_dep + _as_list(tbody.get("depends_on"))
            if "usbd" not in deps:
                continue
            restricted = common_restricted or bool(tbody.get("filter")) \
                or bool(tbody.get("platform_allow"))
            is_variant = bool(re.search(r"\.(out-report|large|workqueue|flash|encoder|camera)",
                                        tname))
            build_only = bool(tbody.get("build_only") or common.get("build_only"))
            candidates.append((restricted, is_variant, tname, build_only))

        if not candidates:
            return None
        # prefer an unrestricted, non-variant scenario (best for -S/twister mode)
        candidates.sort(key=lambda c: (c[0], c[1], c[2]))
        _, _, scenario, build_only = candidates[0]
        return scenario, sample_name, build_only

    # ---- text fallback (no PyYAML) ----
    if "usbd" not in text:
        return None
    m = re.search(r"^\s+(sample\.[A-Za-z0-9_.\-]+)\s*:", text, re.MULTILINE)
    nm = re.search(r"name:\s*(.+)", text)
    scenario = m.group(1) if m else ""
    note = nm.group(1).strip().strip('"').strip("'") if nm else ""
    return scenario, note, ("build_only" in text)


def discover_usb_demos(zephyr_base, exclude=None):
    """
    Scan <zephyr_base>/samples/subsys/usb/*/sample.yaml and return the list of
    USB *device* demos (usbd stack), sorted by key. Returns [] if none found.
    """
    exclude = set(exclude if exclude is not None else DEFAULT_EXCLUDE)
    root = os.path.join(zephyr_base, "samples", "subsys", "usb")
    found = []
    for yml in sorted(glob.glob(os.path.join(root, "*", "sample.yaml"))):
        dirname = os.path.basename(os.path.dirname(yml))
        if dirname in exclude:
            continue
        info = _parse_sample_yaml(yml)
        if not info:
            continue
        scenario, note, build_only = info
        found.append(dict(
            key=_key_from_dir(dirname),
            path="subsys/usb/" + dirname,
            scenario=scenario,
            note=note or dirname,
            build_only=build_only,
        ))
    found.sort(key=lambda d: d["key"])
    return found


# ---------------------------------------------------------------------------
# fallback (used only if discovery yields nothing)
# ---------------------------------------------------------------------------
FALLBACK_DEMOS = [
    dict(key="cdc_acm", path="subsys/usb/cdc_acm",
         scenario="sample.usb_device_next.cdc-acm", note="CDC ACM virtual serial port"),
    dict(key="cdc_acm_bridge", path="subsys/usb/cdc_acm_bridge",
         scenario="sample.usb.cdc-acm-bridge.two_devices", note="Dual CDC ACM UART bridge"),
    dict(key="console", path="subsys/usb/console",
         scenario="sample.usbd.console", note="CDC ACM console"),
    dict(key="dfu", path="subsys/usb/dfu",
         scenario="sample.usbd.dfu", note="USB DFU"),
    dict(key="hid_keyboard", path="subsys/usb/hid-keyboard",
         scenario="sample.usbd.hid-keyboard", note="HID keyboard"),
    dict(key="hid_mouse", path="subsys/usb/hid-mouse",
         scenario="sample.usb_device_next.hid-mouse", note="HID mouse"),
    dict(key="mass", path="subsys/usb/mass",
         scenario="sample.usbd.mass_ram_fat", note="USB Mass Storage"),
    dict(key="midi", path="subsys/usb/midi",
         scenario="sample.usb_device_next.midi", note="USB MIDI 2.0"),
    dict(key="shell", path="subsys/usb/shell",
         scenario="sample.usbd.shell", note="CDC ACM Zephyr shell"),
    dict(key="uac2_implicit_feedback", path="subsys/usb/uac2_implicit_feedback",
         scenario="sample.subsys.usb.uac2_implicit_feedback", note="USB Audio 2.0 (implicit)"),
    dict(key="uac2_explicit_feedback", path="subsys/usb/uac2_explicit_feedback",
         scenario="sample.subsys.usb.uac2_explicit_feedback", note="USB Audio 2.0 (explicit)"),
    dict(key="uvc", path="subsys/usb/uvc",
         scenario="sample.subsys.usb.uvc", note="USB Video"),
    dict(key="webusb", path="subsys/usb/webusb",
         scenario="sample.usb.webusb-next", build_only=True, note="WebUSB"),
]


def get_demos(zephyr_base):
    """Preferred entry point: auto-discover, falling back to the static list."""
    found = discover_usb_demos(zephyr_base)
    return found if found else list(FALLBACK_DEMOS)
