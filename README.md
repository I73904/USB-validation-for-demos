# PIC32 USB HS / FS Demo Validation

Automates **build → flash → enumerate** for every USB *device* demo in the
Microchip zSDK Zephyr tree, on a selectable **PIC32 Curiosity Ultra** board
(default **SG01** `pic32ck_sg01_cult`; **GC01** also supports HS/FS), for both
**USB High-Speed (HS)** and **USB Full-Speed (FS)**.

For each demo the tool:

1. **Builds** it (build test must pass first).
2. **Flashes** it with `west flash` (the MPLAB IPE / PKOB runner used in the
   SG01 blinky bring-up).
3. **Validates enumeration** — checks that the board re-appears in the Windows
   USB device list as a Zephyr device (`VID_2FE3`). If it enumerates, the demo
   passes. (Data transactions may be added later.)

Results go to an HTML report, a JSON report, and per-run logs. Any demo that
fails to build, flash, or enumerate is listed at the end.

---

## Files in this folder

| File | Purpose |
|------|---------|
| `run_usb_validation.py` | Main tool — the HS/FS build/flash/enumerate sweep |
| `run_blinky_check.py` | Blinky sanity check (SG01 + `samples/basic/blinky`) |
| `ykush.py` | Yepkit YKUSH switchable-USB-hub control (module + CLI) for connecting/disconnecting the USB cable |
| `demos.py` | Auto-discovers USB device demos from the tree (`depends_on: usbd`); holds a fallback list + speed/VID/transaction constants |
| `overlays\` | Devicetree overlays used by transactions (e.g. `mass_ramdisk.overlay` — larger FAT RAM disk) |
| `README.md` | This document |
| `results\` | Generated: timestamped run folders + `.build_cache\` |
| `results_blinky\` | Generated: blinky-check run folders |

---

## What gets tested

The demo set is **auto-discovered from the Zephyr tree** — the tool scans
`<zephyr>\samples\subsys\usb\*\sample.yaml` and picks every sample that depends
on the new USB device stack (`depends_on: usbd`). So pulling a branch that adds,
renames, or removes USB samples is reflected automatically; nothing is
hardcoded. USB *host* samples (`usbh.*`, `host_uvc`) and the legacy stack
(`samples\subsys\usb\legacy\*`) are skipped. All enumerate with Vendor ID
**`0x2FE3`** (Zephyr Project).

Each discovered demo is **built → flashed → enumeration-validated**, once for
**HS** and once for **FS**. Run **`python run_usb_validation.py --list`** to see
the exact set for the branch you're on. A typical set (14 on the
`Z4M-5565` branch):

| Demo (`--demo` key) | Sample path (`samples\…`) | USB device class | Enumerates on the PC as |
|---------------------|---------------------------|------------------|--------------------------|
| `cdc_acm` | `subsys\usb\cdc_acm` | CDC ACM | USB Serial Device (COM port) |
| `cdc_acm_bridge` | `subsys\usb\cdc_acm_bridge` | CDC ACM ×2 | Two USB serial ports (needs `arduino_serial`) |
| `console` | `subsys\usb\console` | CDC ACM | USB Serial Device (console output) |
| `dfu` | `subsys\usb\dfu` | DFU | USB DFU device |
| `hid_keyboard` | `subsys\usb\hid-keyboard` | HID | HID Keyboard |
| `hid_mouse` | `subsys\usb\hid-mouse` | HID | HID Mouse |
| `mass` | `subsys\usb\mass` | MSC | USB Mass Storage drive (RAM/FAT) |
| `midi` | `subsys\usb\midi` | MIDI 2.0 | USB MIDI device |
| `shell` | `subsys\usb\shell` | CDC ACM | USB Serial Device (Zephyr shell) |
| `testusb` | `subsys\usb\testusb` | Vendor (loopback) | USB test/loopback device |
| `uac2_implicit_feedback` | `subsys\usb\uac2_implicit_feedback` | Audio 2.0 | USB Audio device (needs I2S) |
| `uac2_explicit_feedback` | `subsys\usb\uac2_explicit_feedback` | Audio 2.0 | USB Audio device (needs I2S) |
| `uvc` | `subsys\usb\uvc` | Video (UVC) | USB Camera (needs `zephyr,camera`) |
| `webusb` | `subsys\usb\webusb` | Vendor / WebUSB | WinUSB / WebUSB device |

Every demo is attempted at **HS + FS**. The actual pass/fail per run is in the
generated report; some demos may legitimately fail to **build** (they need
peripherals/devicetree the board doesn't have — e.g. `arduino_serial`, I2S,
camera) or to **enumerate** — that's exactly what this surfaces.

### Demos that need console interaction

A few samples don't auto-enable USB — they wait at the shell prompt for
commands. The `shell` sample is the notable one: it only enumerates after
`usbd defcfg` then `usbd enable` are typed at `uart:~$`. The tool handles this
automatically: after flashing such a demo it opens the board's **VCOM console**
(auto-detected, or `--console COMx`) at 115200 baud and sends the required
commands before validating enumeration. These commands live in
`CONSOLE_INIT` in `demos.py` — add entries there for any other interactive
sample. If auto-detection picks the wrong port, pass `--console COMx`.

> Run just one demo with **`--demo <key>`** (the key in column 2), e.g.
> `python run_usb_validation.py --demo cdc_acm`. Omit it to run all discovered demos.

> **Excluded:** USB *host* samples (`host_uvc`, `usbh.*`) and the legacy USB
> stack (`samples\subsys\usb\legacy\*`) — the board is a USB device using the
> new `usbd` stack. `common\` is shared code, not a sample.

### HS vs FS on this board

The SoC has two USB controllers, both bound to samples via the `zephyr_udc0`
device-tree node label:

| Speed | DT node | compatible | Board default |
|-------|---------|------------|---------------|
| HS | `hsusb0` | `microchip,usb-g2` | **enabled** as `zephyr_udc0` |
| FS | `usb0`   | `microchip,usb-g1` | disabled |

**HS** is always the board default (no extra flags). The tool selects **FS**
one of two ways, auto-detected per branch, and logs which as `FS method : …`:

1. **Snippet (preferred).** If the tree provides a snippet that defines
   `PIC32CK_USBFS` (e.g. `microchip-udc-usbfs` on the
   `Z4M-5565_usb_hs_fs_selection_pic32ck` branch), FS builds add
   `-S microchip-udc-usbfs`. This is the official mechanism — **no files are
   modified**.
2. **`.dts` patch (fallback).** On older branches without that snippet, the tool
   temporarily rewrites the board `.dts` so `zephyr_udc0` points at `usb0`, then
   **always restores it** (even on Ctrl-C / crash). A one-time backup is written
   as `<board>.dts.usbval.bak`; if a run is ever killed hard, restore with:
   ```
   python run_usb_validation.py --restore-dts
   ```

Either way, **HS enumerates on the USB-C connector and FS on the Micro-B** — see
*Hardware setup*. On boards where FS is already the default (e.g. some PIC32CX),
no snippet or patch is needed.

---

## Hardware setup & connections

Three independent connections are involved. **Flashing** happens over the DEBUG
port; **enumeration validation** happens over the USB *device* port — they are
different connectors, which is why a demo can flash fine yet fail to enumerate.

```
        12 V DC adapter                         PC / host (running this script)
              |                                   ^        ^        ^
              v                                   |        |        |
     +----------------------------------------+   |        |        |
     |  [DC PWR IN]                           |   |        |        |
     |                                        |   |        |        |
     |  [DEBUG USB  / on-board PKoB4] --------|---+        |        |   (1) flash / program (+ power + VCOM)
     |                                        |            |        |
     |     PIC32CK SG01 Curiosity Ultra       |            |        |
     |                                        |            |        |
     |  [USB-C   device port]  -- HS (hsusb0)-|------------+        |   (2) enumerate: HS demos
     |  [Micro-B device port]  -- FS (usb0) --|---------------------+   (3) enumerate: FS demos
     |                                        |
     |  (o) PWR LED   [RESET]   LED0  LED1    |
     +----------------------------------------+
```

| # | Connection | From → To | Purpose | Used for |
|--:|-----------|-----------|---------|----------|
| — | **Power — 12 V DC adapter** | adapter → board DC input jack | Main supply; recommended when Ethernet / SD / USB peripherals draw current | Powering the board |
| 1 | **Debug / Program — DEBUG USB** (on-board PKoB4) | board DEBUG USB → PC | SWD program/debug, board power, VCOM | `west flash` (ipecmd/PKoB4) |
| 2 | **Target USB (HS)** — USB-C | board USB-C → PC | The USB *device* under test at High-Speed | HS enumeration validation |
| 3 | **Target USB (FS)** — Micro-B | board Micro-B → PC | The USB *device* under test at Full-Speed | FS enumeration validation |

**Steps**

1. **Power:** connect the **12 V DC adapter** to the board's DC input. The green
   **PWR LED** should light. (The board can also run powered solely from the
   DEBUG USB for light use, but the adapter is recommended for a full run.)
2. **Debugger:** connect the **DEBUG USB** port to the PC. This is what
   `west flash` uses to program the chip via the on-board PKoB4. (An external
   J32 Debug Probe on the **CORTEX DEBUG** header is an alternative.)
3. **Device port:** connect the board's **USB device connector to the same PC**:
   the **USB-C** for HS demos and the **Micro-B** for FS demos. This is the port
   whose enumeration the tool validates — if it is not connected, every
   `ENUM` check fails even though flashing succeeds.
4. Press **RESET** if you need to restart the firmware; watch **LED0/LED1** and
   the host device list.

> **Verify against the official board User Guide.** Exact connector locations,
> silkscreen labels, the DC jack voltage/polarity, and power-source jumpers vary
> by board revision — confirm them in the
> [PIC32CK SG01 Curiosity Ultra User Guide (DS70005529)](https://ww1.microchip.com/downloads/aemDocuments/documents/MCU32/ProductDocuments/UserGuides/PIC32CK-SG01-SG01-Curiosity-Ultra-User-Guide-DS70005529.pdf).
> The HS↔USB-C / FS↔Micro-B mapping above is the expected routing; if a demo does
> not enumerate on one connector, try the other and check the User Guide.

---

## Prerequisites

Same environment as the blinky bring-up (see the SG01 blinky PDF). See
**Hardware setup & connections** above for the physical wiring.

- A Zephyr workspace synced with `west` (any location — see *Portability* below)
- Python venv with `west` installed
- Ninja, CMake, dtc installed
- **MPLAB IPE** installed (`ipecmd.exe`) and the **PIC32CK-SG DFP** — needed by
  `west flash`. Auto-located from the standard MPLAB X install path.
- PKOB4 debugger connected (for flashing)
- **The board's USB *device* port connected to this PC** (the Type-C / Micro-B
  connector, separate from the DEBUG port) — this is the port whose enumeration
  is validated. Flashing uses the DEBUG port; enumeration uses the device port.

Recommended shell: **cmd.exe** (as in the blinky notes).

---

## Portability (running on another laptop)

Nothing is hardcoded to a specific machine. The scripts locate everything for
you, and only ask if auto-detection fails:

**Zephyr base** — resolved in this order: `--zephyr-base` → `$ZEPHYR_BASE` →
`west topdir` → searching up from the current directory → a built-in hint →
finally an interactive prompt. You may pass either the `…\zephyr` directory or
the workspace root (it appends `zephyr` automatically) and it is validated
(must contain `Kconfig.zephyr` + `samples\`).

**west / venv** — searched in this order: an activated venv / `west` on PATH →
`$VIRTUAL_ENV` → `.venv` / `venv` / `env` folders near the Zephyr tree →
finally a prompt. Override with `--west <path\to\west.exe>` or `--venv <dir>`.

> **Simplest reliable setup:** activate the venv, then just run the script — no
> flags needed:
> ```
> <workspace>\..\.venv\Scripts\activate.bat
> python run_usb_validation.py
> ```
> If you prefer not to activate, the auto-detection above handles it, or pass
> `--zephyr-base` / `--venv` explicitly. `ipecmd.exe` is auto-found; override
> with `--ipecmd` if MPLAB X is in a non-standard location.

---

## Build speed

- **Incremental build cache (default).** Builds use `west build -p auto` with a
  stable per-(board, demo, speed) build directory under
  `<outdir>\.build_cache`. West still does a clean build whenever it detects one
  is needed, so results are identical — but unchanged re-runs are ~40× faster
  (seconds instead of minutes). Control with `--pristine {auto,always,never}`,
  relocate with `--build-cache <dir>`, or disable with `--no-cache`.
- **ccache (recommended).** Zephyr uses `ccache` automatically if it is on PATH.
  Because every demo recompiles the same Zephyr core, ccache dramatically cuts
  the *first* full run too. Install once: `choco install ccache`. The scripts
  report whether ccache was found.

---

## USB power switching (YKUSH)

A [Yepkit YKUSH](https://www.yepkit.com/product/300110/YKUSH) switchable USB hub
lets the tests control the **USB device cable** programmatically — needed to
automate two modes: cable **connected** for the `samples` enumeration/transaction
tests, and cable **disconnected** for the `tests/drivers/udc` driver tests
(which must run with no USB host attached).

`ykush.py` is a standalone module + CLI (uses the `hid` package in the venv):

```bat
python ykush.py on  1        :: power port 1 on  (device connects / re-enumerates)
python ykush.py off 1        :: power port 1 off (device disconnects)
python ykush.py status 1     :: query port state
python ykush.py cycle 1      :: off, wait, on
python ykush.py present      :: is a YKUSH hub attached? (exit 0 = yes)
```

Because HS and FS use **different connectors** (USB-C = HS, Micro-B = FS) and only
one controller is active at a time, each connector goes on its **own YKUSH port**.
Tell the validator the mapping:

```bat
:: e.g. USB-C (HS) on port 2, Micro-B (FS) on port 3, DEBUG USB straight to the PC
python run_usb_validation.py --board pic32ck_sg01_cult --ykush-port-hs 2 --ykush-port-fs 3
```

### Wiring diagram

```
                      PC / host (runs the script)
                        |                          |
       DEBUG USB        |                          |  USB (YKUSH upstream)
   (flash + power +     |                          |
        VCOM)           |                          v
                        |                +----------------------------------+
                        |                |            YKUSH hub             |
                        |                |   port1     port2      port3     |
                        |                +-------------+----------+---------+
                        |                              | HS       | FS
                        |                              | (on/off) | (on/off)
                        v                              v          v
   +--------------------------------------------------------------------------+
   |                     PIC32CK SG01 Curiosity Ultra                          |
   |   [DEBUG / PKoB4]          [USB-C = HS / hsusb0]     [Micro-B = FS / usb0] |
   +--------------------------------------------------------------------------+

   Samples  (connect the tested connector):
       HS run  ->  port 2 ON ,  port 3 OFF
       FS run  ->  port 2 OFF,  port 3 ON
   Driver tests / tests/drivers/udc  (--udc: disconnect the tested connector):
       udc-HS  ->  port 2 OFF          udc-FS  ->  port 3 OFF
```

- **DEBUG USB goes straight to the PC** (not through the YKUSH), so flashing and
  the VCOM console keep working even when a device connector is powered off.
- Only the **device** connectors (USB-C, Micro-B) go through the YKUSH, so the
  script can connect/disconnect them per test.

The two test modes use **opposite** cable logic:

- **Samples flow (implemented).** The demo needs a host to enumerate, so the run
  **connects** the tested speed's connector and disconnects the other — only the
  connector under test is live. At the end both are restored to on.

  | Run | Port for HS (USB-C) | Port for FS (Micro-B) |
  |-----|:---:|:---:|
  | samples **HS** | **ON** | OFF |
  | samples **FS** | OFF | **ON** |

- **Driver-test flow (`tests/drivers/udc`, via `--udc`).** The test must run with
  **no host attached**, so it **disconnects** the connector for the controller
  under test (udc-HS → HS port OFF; udc-FS → FS port OFF), runs
  `twister --device-testing` over the serial console, reads the ztest verdict,
  then restores power. *Caveat to validate on hardware:* the udc test notes the
  controller "cannot be enabled without VBUS" — a ykush port-off cuts VBUS **and**
  data, so if the PIC32CK UDC needs VBUS to enable we may need a different
  disconnect (ykush is power-only).

Because ykush automates this cable toggling, the two phases **can run in a single
invocation**: `--udc` runs the samples sweep (cables connected) and *then* the
udc tests (cables disconnected). See *Running* below.

`--ykush-serial` targets a specific hub if you have more than one.

**If the hub isn't connected:**
- With **no** `--ykush-port-*` → ykush is never used; behaviour is unchanged. The
  full samples sweep still runs — you just plug the cables in by hand (USB-C for
  HS, Micro-B for FS; a speed whose cable isn't connected builds/flashes but
  fails enumeration).
- With `--ykush-port-*` set but **no hub found** → the run prints one warning,
  **disables switching, and continues** assuming the cables are connected
  manually (it never crashes or hangs). `python ykush.py present` confirms
  whether a hub is detected.

> Keep the **DEBUG USB out of the YKUSH** (plug it straight into the PC) so
> flashing keeps working when a device connector is powered off.

## Choosing the board

When you run without `--board`, the tool **discovers the USB-capable Microchip
boards** in your tree and shows a numbered menu, e.g.:

```
Available USB-capable Microchip boards:

   #   BOARD                    SPEEDS       FULL NAME
   --------------------------------------------------------------------------
   1   pic32ck_gc01_cult        HS/FS        PIC32CK GC01 Curiosity Ultra
   2   pic32ck_sg01_cult        HS/FS        PIC32CK SG01 Curiosity Ultra (recommended)
   3   pic32cx_sg41_cult        FS           PIC32CX SG41 Curiosity Ultra
   4   pic32cx_sg61_cult        FS           PIC32CX SG61 Curiosity Ultra
   5   sam_e54_xpro             FS           SAM E54 Xplained Pro
Select board [1-5] (Enter = pic32ck_sg01_cult):
```

The full set is **auto-discovered** from the tree (any Microchip board whose
`.dts` exposes `zephyr_udc0`), so it may vary by branch. On the
`Z4M-5565` branch it is:

| # | Board (`--board`) | Speeds | Full name |
|--:|-------------------|--------|-----------|
| 1 | `pic32ck_gc01_cult` | HS + FS | PIC32CK GC01 Curiosity Ultra |
| 2 | `pic32ck_sg01_cult` | HS + FS | PIC32CK SG01 Curiosity Ultra *(default / recommended)* |
| 3 | `pic32cx_sg41_cult` | FS only | PIC32CX SG41 Curiosity Ultra |
| 4 | `pic32cx_sg61_cult` | FS only | PIC32CX SG61 Curiosity Ultra |
| 5 | `sam_e54_xpro` | FS only | SAM E54 Xplained Pro |

Press Enter for the default, or type a number / board name. The **Speeds**
column is what each board supports — HS/FS boards get both; **FS-only** boards
skip the HS runs automatically (recorded as `SKIP`, never a hard failure).
Run `python run_usb_validation.py --list-boards` to print the current set.

- List boards without running anything: `python run_usb_validation.py --list-boards`
- Pick non-interactively (CI): `python run_usb_validation.py --board pic32ck_sg01_cult`

## Running

**YKUSH is optional.** All of the commands below work **without** a YKUSH — you
just connect the board's USB cable yourself (USB-C for HS, Micro-B for FS). The
`--ykush-port-*` flags only *automate* that cabling; see *With YKUSH* further
down. Nothing here requires the hub.

### Everyday runs (no YKUSH — plug the cable in manually)

```bat
:: List the demo matrix / boards (no hardware needed)
python run_usb_validation.py --list
python run_usb_validation.py --list-boards

:: Build-only pass — proves every demo compiles for HS and FS (no board needed)
python run_usb_validation.py --build-only

:: Full run: pick a board from the menu, then build + flash + enumerate (HS + FS)
python run_usb_validation.py

:: Target a specific board / one speed
python run_usb_validation.py --board pic32ck_gc01_cult
python run_usb_validation.py --speeds hs

:: A single demo, or a subset
python run_usb_validation.py --demo cdc_acm
python run_usb_validation.py --demos cdc_acm,hid_mouse,mass --speeds hs

:: Add data transactions (64 KiB): CDC echo + mass-storage file round-trip
python run_usb_validation.py --demos cdc_acm,mass --transactions
```

For HS+FS in one run without a hub, you swap the USB-C / Micro-B cable when the
tool moves to the FS phase (or just run `--speeds hs` and `--speeds fs`
separately). A speed whose cable isn't connected will build/flash but fail
enumeration — expected.

### With YKUSH (automated cable switching)

Add the port mapping (USB-C→HS port, Micro-B→FS port). The tool then connects the
right connector per speed and disconnects the other automatically. Everything
above also accepts these flags:

```bat
:: Samples HS + FS, cable switched automatically
python run_usb_validation.py --ykush-port-hs 2 --ykush-port-fs 3

:: Samples + 64 KiB transactions, both speeds, hands-free
python run_usb_validation.py --transactions --ykush-port-hs 2 --ykush-port-fs 3

:: Samples sweep AND udc driver tests in one go (ykush toggles the cable)
python run_usb_validation.py --udc --ykush-port-hs 2 --ykush-port-fs 3
```

### UDC driver tests

```bat
:: With ykush: cable auto-disconnected for the test
python run_usb_validation.py --udc-only --ykush-port-hs 2 --ykush-port-fs 3

:: Without ykush: the tool prints a note to unplug the device cable first
python run_usb_validation.py --udc-only --speeds hs --device-serial COM40
```

> **Test phases.** A run has up to two phases: the **samples sweep**
> (build → flash → enumerate, cable *connected*) and the **udc driver tests**
> (ztest via twister, cable *disconnected*). They use opposite cable states — with
> a YKUSH, `--udc` runs both hands-free; without one, you unplug/replug between
> phases. Each phase writes its own report (`usb_validation_report_*` and
> `udc_report_*`).

### Command-line options

| Option | Default | Purpose |
|--------|---------|---------|
| `--board <name>` | prompt/menu | Target board; skips the menu when given |
| `--demo <name>` | all | Run a **single** demo by key, e.g. `--demo cdc_acm` (takes precedence over `--demos`) |
| `--demos <a,b,…>` | all | Subset of demo keys to run (comma-separated) |
| `--speeds <hs,fs>` | `hs,fs` | Which USB speeds to test |
| `--build-only` | off | Compile only; no flash / enumeration |
| `--flasher {west,twister}` | `west` | Build/flash backend |
| `--pristine {auto,always,never}` | `auto` | `west build` clean mode (`auto` = fast incremental) |
| `--build-cache <dir>` | `<outdir>\.build_cache` | Location of cached build artifacts |
| `--no-cache` | off | Build inside the timestamped run dir (no caching) |
| `--console <COMx>` | auto-detect | Serial console for demos that need shell init (e.g. `shell`) |
| `--console-baud <n>` | `115200` | Baud rate for `--console` |
| `--ykush-port-hs <n>` | `0` (off) | YKUSH port for the HS (USB-C) connector; enables ykush for HS runs |
| `--ykush-port-fs <n>` | `0` (off) | YKUSH port for the FS (Micro-B) connector; enables ykush for FS runs |
| `--ykush-serial <s>` | first hub | Target a specific YKUSH hub by serial |
| `--transactions` | off | Also run post-enumeration data transactions (CDC/mass) for demos that support them |
| `--transaction-size <n>` | `65536` | Bytes for data transactions when `--transactions` is set (64 KiB) |
| `--udc` | off | **Also** run `tests/drivers/udc` ztests (cable disconnected) after the samples sweep |
| `--udc-only` | off | Run **only** the udc driver tests (skip the samples sweep) |
| `--device-serial <COMx>` | auto | Serial console for `--udc` twister device-testing |
| `--enum-timeout <s>` | `30` | How long to wait for the device to enumerate |
| `--vid <hex>` | `2FE3` | USB Vendor ID that counts as "enumerated" |
| `--zephyr-base <path>` | auto-detect | Zephyr base or workspace root |
| `--venv <dir>` / `--west <path>` | auto-detect | Locate `west` without activating the venv |
| `--ipecmd <path>` | auto-detect | Path to `ipecmd.exe` (MPLAB IPE) |
| `--outdir <path>` | `.\results` | Base output directory |
| `--list` / `--list-boards` | — | Print the demo matrix / discovered boards and exit |
| `--restore-dts [board]` | — | Restore a board `.dts` from backup and exit |

> The board holds one firmware at a time, so runs are sequential: each demo is
> flashed, checked, then overwritten by the next. **One board per run** — running
> two boards at once isn't supported (flashing has no programmer selector and
> enumeration is matched by VID only). For multiple board *types*, run them
> separately with their own `--outdir` / `--build-cache`.

### Blinky sanity check — `run_blinky_check.py`

A minimal confidence test fixed to `pic32ck_sg01_cult` + `samples/basic/blinky`,
reusing the same toolchain/build/flash/report code. Use it to prove the whole
build→flash pipeline works, isolating USB-only problems.

```bat
python run_blinky_check.py              :: build + flash blinky; LED0 should blink
python run_blinky_check.py --build-only :: just compile
```

Output goes to `results_blinky\run_<timestamp>\`. Since blinky has no USB there
is no enumeration step — PASS means build + flash succeeded.

## Failure handling — the run never stops

Every demo is isolated: a build error, a flash error, an enumeration timeout, or
even an unexpected crash in one demo is **caught, recorded, and the run moves on
to the next demo**. Nothing halts the sweep. Outcomes per demo:

- `PASS` — step succeeded
- `FAIL` — build/flash/enumerate failed (reason recorded, logs saved)
- `SKIP` — not applicable (e.g. HS run on an FS-only board, or FS patch unavailable)
- `N/A` — step not reached because an earlier step failed

At the end you get the full table, an HTML/JSON report, and an explicit
**Failed demos** list.

---

## Output

Each run writes to its **own timestamped folder** so nothing is overwritten:

```
results\
  .build_cache\                 <- shared, reused across runs (fast rebuilds)
    pic32ck_sg01_cult_cdc_acm_hs\
    ...
  run_2026-07-24_15-32-14\
    usb_validation_report_2026-07-24_15-32-14.html   <- open in a browser
    usb_validation_report_2026-07-24_15-32-14.json   <- machine-readable
    logs\
      cdc_acm_hs_build.log      <- full build output
      cdc_acm_hs_flash.log      <- full `west flash` output
      ...
  run_2026-07-24_16-05-41\
    ...
```

> `.build_cache\` persists between runs (that's what makes re-runs fast) and can
> grow to a few GB across all demos. Delete it or pass `--no-cache` to reclaim
> space. Report and log files are always kept per-run and never overwritten.

The report path (with its timestamp) is printed at the end of every run. The
HTML report shows build / flash / enumerate status per demo, summary counts, and
a **Failed demos** list. For traceability it also records the **Zephyr git
commit + branch** being tested (printed at the top of the run as `Git commit …`
and shown in the report header / JSON).

Status meaning: **PASS** ok · **FAIL** failed · **SKIP** filtered/not applicable · **N/A** not reached.

> Change the base directory with `--outdir <path>`; timestamped `run_*` folders
> are created inside it.

---

## How it decides "enumerated"

Before flashing, the tool snapshots the Windows USB device list
(`Get-CimInstance Win32_PnPEntity`). After a successful flash it polls (up to
`--enum-timeout`) for a device whose `PNPDeviceID` contains `VID_2FE3` (the
default Zephyr sample VID).

- **Device with `VID_2FE3` appears → PASS** (device name / PID recorded).
- **A device appears but Windows can't read its descriptors** (shows as
  *"Unknown USB Device (Device Descriptor Request Failed)"* / `VID_0000`) →
  **FAIL**, flagged as a likely USB driver/enumeration bug — this is the kind of
  real issue the sweep is meant to catch.
- **Nothing new appears → FAIL**, with a hint to check the USB *device* cable.

The console and report show exactly which of these happened, and any new USB
devices seen after the flash, to make triage quick.

> **Two connectors:** flashing uses the board's **DEBUG** port; enumeration is
> checked on the **USB device** port (Type-C for HS, Micro-B for FS). Both must
> be connected to this PC for a full run.

---

## Data transactions (beyond enumeration)

Data transactions are **off by default** — a normal run does just
**build + flash + enumerate**. Add **`--transactions`** to also run a **data
transfer** after each successful enumeration, proving the endpoint actually moves
data (not just that the device appears). It's a single common flag that applies
to every demo that supports a transaction. Results show in the **Transact**
column of the report (and a `Transactions` summary card); a failed transaction
marks the run as failed. Runs without `--transactions` show `N/A` there.

```bat
:: default: build + flash + enumerate only
python run_usb_validation.py --board pic32ck_sg01_cult --demo cdc_acm --speeds hs

:: opt in to the 64 KiB CDC echo transaction
python run_usb_validation.py --board pic32ck_sg01_cult --demo cdc_acm --speeds hs --transactions
```

Which demos have a transaction is defined by `TRANSACTIONS` in `demos.py`:

| Demo | Transaction | What it does |
|------|-------------|--------------|
| `cdc_acm` | `cdc_echo` | Opens the CDC COM port, sends **64 KiB**, reads it back, and byte-compares (the sample echoes what it receives). |
| `mass` | `mass_file` | Builds the FAT RAM-disk variant, waits for the mounted drive, writes a **64 KiB** file, reads it back, and byte-compares. |

Notes on the CDC echo test:
- It runs over the **enumerated CDC COM port** (VID_2FE3), *not* the debug VCOM.
- The `cdc_acm` sample blocks until **DTR** is asserted and echoes with a small
  ring buffer, so the tool asserts DTR and **reads while it writes** (a writer
  thread + concurrent reader) to move 64 KiB without deadlocking.
- Enabled only with `--transactions`; size is configurable via
  `--transaction-size <bytes>`. Without `--transactions` the run is
  enumeration-only.

Notes on the mass-storage test:
- When `--transactions` is set, `mass` is built in its **FAT RAM-disk variant**
  (`-DCONFIG_APP_MSC_STORAGE_RAM=y` + a larger `overlays\mass_ramdisk.overlay`);
  Zephyr formats the RAM disk to FAT so Windows mounts it as a drive with **no
  format prompt**. This variant builds into a separate `..._fat` cache dir so it
  doesn't clash with the plain enumeration build.
- The tool notes the removable drive letters before flashing and picks the **new**
  one that appears after enumeration, then writes/reads/compares the file there
  (removable drives are write-through, so the bytes really reach the device).
- The default RAM disk is 256 KiB — plenty for 64 KiB. If you raise
  `--transaction-size` past what fits, enlarge `overlays\mass_ramdisk.overlay`
  (bump `sector-count`); it's capped by the board's RAM.
- **Corporate / managed PCs often block USB mass storage** via device-control /
  DLP (e.g. SentinelOne, Microsoft Purview, Ivanti). The device then enumerates
  but Windows refuses to start the disk (`CM_PROB_FAILED_START`), and you may see
  a *"your organization blocks this device"* popup. The tool detects this and
  reports **"BLOCKED by host policy"** rather than blaming the firmware — CDC and
  enumeration still work because only *storage* is blocked.
- **Workaround used here (Microchip VID):** the block is per-Vendor-ID, and this
  machine's policy allows **Microchip's VID `0x04D8`**. So the `mass` sample is
  built with `CONFIG_SAMPLE_USBD_VID=0x04D8` (in `…/samples/subsys/usb/mass/prj.conf`)
  and it then mounts. Because of this, the tool identifies the mass device by
  **`VID_04D8&PID_0008`** (see `DEMO_USB_MATCH` in `demos.py`) — the precise
  VID+PID avoids clashing with the PKoB4 / YKUSH, which are also VID `04D8`.
  > This couples the tool to that sample edit: if the Zephyr tree is reset
  > (e.g. `west update`) the mass VID reverts to `2FE3`; re-apply the prj.conf
  > line, or update `DEMO_USB_MATCH["mass"]` to `VID_2FE3&PID_0008`.
  Alternatively, have your team allow-list VID `2FE3` in the console and drop the
  workaround.

---

## UDC driver tests (`tests/drivers/udc`)

**`--udc`** adds a second phase that runs the USB **device-controller driver**
ztests in `tests/drivers/udc`. These exercise the UDC driver directly (endpoint
enqueue/dequeue, etc.) and **must run with no USB host attached** — the opposite
of the samples, which need the host to enumerate. Because ykush automates the
cable, both phases run in one command:

- `--udc` → samples sweep (cable connected) **then** udc tests (cable disconnected).
- `--udc-only` → just the udc tests (skip the samples sweep).

```bat
:: samples + udc in one run; ykush toggles the cable between phases
python run_usb_validation.py --board pic32ck_sg01_cult --udc --ykush-port-hs 2 --ykush-port-fs 3

:: udc only, HS, no ykush -> the tool tells you to unplug the cable first
python run_usb_validation.py --board pic32ck_sg01_cult --udc-only --speeds hs --device-serial COM40
```

For each speed the udc phase:
1. **disconnects** the tested connector (ykush port OFF; or prints a note to unplug),
2. runs `twister --device-testing --device-serial <COM> -p <board> -s drivers.usb.udc -T tests/drivers/udc` (FS adds the usbfs snippet / dts-patch),
3. reads twister's **ztest verdict** → PASS/FAIL,
4. **reconnects** the cable (port ON).

It writes a separate **`udc_report_<stamp>.html`/`.json`**; the samples report
and its build/flash/enumerate flow are untouched.

> **Windows MAX_PATH note:** twister nests its build tree deeply, which overflows
> Windows' 260-char path limit under the timestamped run folder (the SDK archiver
> isn't long-path-aware). So the udc build uses a **short scratch dir near the
> workspace root** (`..\.udc\<speed>`, e.g. `D:\ZephyrProject\.udc\hs`), cleaned
> each run. Reports still land in the timestamped `results\run_*` folder.

> **Why two phases?** Samples need the cable *connected* (enumerate on the host);
> udc tests need it *disconnected* (no host). They also use different tooling
> (host PnP vs. twister ztest) and separate reports. `--udc` runs both back-to-back.

> **VBUS caveat:** a ykush port-off cuts VBUS *and* data. If the PIC32CK UDC
> needs VBUS to enable, the driver may not come up with the cable fully off — to
> be confirmed on hardware; if so, the disconnect strategy will need adjusting
> (ykush is power-only).

---

## Troubleshooting

- **Flash fails with "Programmer not found"** — the PKOB4/DEBUG USB isn't
  enumerated. Reseat the DEBUG cable at *both* ends, try another PC USB port /
  cable, confirm the board power LED is on. Verify it's back with:
  ```
  powershell -Command "Get-CimInstance Win32_PnPEntity | ? {$_.PNPDeviceID -like '*VID_04D8*'} | Select Name"
  ```
  Also make sure MPLAB X / IPE isn't open (it locks the programmer). If it
  persists, recover the PKOB via MPLAB IPE once (see blinky PDF, Problems 8–11).
- **`shell` (or another console demo) doesn't enumerate** — it needs shell
  commands sent over the VCOM. Check the `CONSOLE …` log line shows the right
  COM port; if it says *port busy / access denied*, close any serial terminal
  (PuTTY / Tera Term / MPLAB Data Visualizer) on that port, or replug the DEBUG
  USB to reset the VCOM, then pass `--console COMx` if auto-detect picks wrong.
- **Build fails for one demo** — open its `results\logs\<demo>_<speed>_build.log`.
  Some samples need peripherals/devicetree this board lacks (`arduino_serial`,
  I2S, `zephyr,camera`); those legitimately fail to build and are listed so you
  can triage.
- **Nothing enumerates but flash succeeded** — confirm the board's USB *device*
  port (USB-C for HS, Micro-B for FS — not the debugger port) is plugged into
  this PC, and increase `--enum-timeout`.
- **FS runs all fail to enumerate** — usually the Micro-B (FS) device port isn't
  connected. HS uses USB-C, FS uses Micro-B; both must reach this PC.
- **Board `.dts` looks modified after a crash** (fallback FS-patch branches only)
  — run `python run_usb_validation.py --restore-dts`.
- **`mass` transaction fails / "your organization blocks this device"** — the
  machine's USB device-control / DLP (SentinelOne, Microsoft Purview, Ivanti…) is
  blocking USB mass storage; the device shows `Status: Error` /
  `CM_PROB_FAILED_START` and no disk appears. Not a firmware bug. Check with:
  ```
  powershell -Command "Get-PnpDevice | ? {$_.InstanceId -like '*VID_2FE3*' -and $_.Status -eq 'Error'} | Select FriendlyName,ConfigManagerErrorCode"
  ```
  Ask IT to allow-list the test device, or run the `mass` transaction on an
  unmanaged PC. Enumeration and CDC transactions are unaffected.
