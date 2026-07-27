# -*- coding: utf-8 -*-
"""
Blinky sanity check for PIC32CK SG01 Curiosity Ultra.

A minimal end-to-end confidence test that reuses the exact same toolchain
resolution, build, flash and reporting code as run_usb_validation.py, but with
everything fixed:

  * board  = pic32ck_sg01_cult
  * sample = samples/basic/blinky

It BUILDS blinky, FLASHES it with west/ipecmd, and reports PASS/FAIL. There is
no USB enumeration step because blinky does not use USB - a successful build +
flash (and LED0 blinking on the board) proves the whole build/flash pipeline
works, isolating any USB-only problems seen in the USB validation run.

Usage (venv need not be activated - west/ipecmd are auto-located):

    python run_blinky_check.py
    python run_blinky_check.py --build-only          # just compile
    python run_blinky_check.py --ipecmd "C:\\...\\ipecmd.exe"
"""

import argparse
import datetime
import os
import sys

import run_usb_validation as R

BOARD = "pic32ck_sg01_cult"
BLINKY = dict(key="blinky", path="basic/blinky", scenario="sample.basic.blinky",
              note="Basic LED blink (LED0)")


def parse_args():
    ap = argparse.ArgumentParser(description="Blinky sanity check for PIC32CK SG01")
    ap.add_argument("--zephyr-base", default="",
                    help="Zephyr base or workspace root (default: auto-detect / prompt)")
    ap.add_argument("--west", default="", help="path to west.exe (default: auto-detect venv)")
    ap.add_argument("--venv", default="", help="path to the Zephyr venv")
    ap.add_argument("--ipecmd", default="",
                    help="path to ipecmd.exe or its folder (default: auto-detect MPLAB IPE)")
    ap.add_argument("--flasher", choices=["west"], default="west")
    ap.add_argument("--build-only", action="store_true", help="build only; do not flash")
    ap.add_argument("--pristine", choices=["auto", "always", "never"], default="auto",
                    help="west build pristine mode (default: auto = fast incremental)")
    ap.add_argument("--build-cache", default="",
                    help="dir for cached build artifacts (default: <outdir>\\.build_cache)")
    ap.add_argument("--no-cache", action="store_true", help="disable the build cache")
    ap.add_argument("--build-timeout", type=int, default=1800)
    ap.add_argument("--flash-timeout", type=int, default=600)
    ap.add_argument("--outdir", default=os.path.join(R.HERE, "results_blinky"))
    return ap.parse_args()


def main():
    args = parse_args()

    args.zephyr_base = R.resolve_zephyr_base(args)
    if not args.zephyr_base:
        R.log_line("ERROR: could not locate a valid Zephyr base. "
                   "Pass --zephyr-base <path to ...\\zephyr>.")
        return 2

    run_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_outdir = args.outdir
    args.outdir = os.path.join(args.outdir, "run_" + run_stamp)
    os.makedirs(os.path.join(args.outdir, "logs"), exist_ok=True)

    if args.no_cache:
        cache_dir = None
    else:
        cache_dir = args.build_cache or os.path.join(base_outdir, ".build_cache")
        os.makedirs(cache_dir, exist_ok=True)

    env, _ = R.resolve_toolchain(args)
    ok, fatal, warn = R.preflight(env, need_flash=not args.build_only)
    for w in warn:
        R.log_line("WARNING: " + w)
    if not ok:
        R.log_line("PREFLIGHT FAILED - not starting build:")
        for f in fatal:
            for ln in f.splitlines():
                print("    " + ln)
        return 2

    commit_info = R.git_commit_info(args.zephyr_base)
    R.log_line("Zephyr base : {}".format(args.zephyr_base))
    if commit_info:
        R.log_line("Git commit  : {}  [{}]".format(commit_info["short"], commit_info["branch"]))
        if commit_info.get("subject"):
            R.log_line("             {}".format(commit_info["subject"]))
    R.log_line("Output dir  : {}".format(args.outdir))
    R.log_line("west        : {}".format(R.WEST))
    if not args.build_only:
        R.log_line("ipecmd      : {}".format(R.IPECMD or "(not found - flashing will fail)"))
    R.log_line("Board       : {}".format(BOARD))
    R.log_line("Sample      : samples/{}".format(BLINKY["path"]))

    rec = dict(demo=BLINKY["key"], speed="na", note=BLINKY["note"],
               build=R.NA, flash=R.NA, enum=R.NA, reason="", device="")

    # ---- build ----
    R.log_line("BUILD  blinky ...")
    bstatus, build_dir, blog, breason = R.build_with_west(
        BLINKY, "na", BOARD, args.zephyr_base, args.outdir, env, args.build_timeout,
        pristine=args.pristine, cache_dir=cache_dir)
    rec["build"] = bstatus
    rec["build_log"] = os.path.relpath(blog, args.outdir)
    if breason:
        rec["reason"] = breason
    R.log_line("BUILD  blinky -> {} {}".format(bstatus, breason))

    if bstatus == R.PASS and not args.build_only:
        # ---- flash ----
        R.log_line("FLASH  blinky ...")
        fstatus, flog, freason = R.flash_demo(
            build_dir, BLINKY, "na", args.outdir, env, args.flash_timeout)
        rec["flash"] = fstatus
        rec["flash_log"] = os.path.relpath(flog, args.outdir)
        if freason:
            rec["reason"] = freason
        R.log_line("FLASH  blinky -> {} {}".format(fstatus, freason))
        if fstatus == R.PASS:
            rec["device"] = "flashed OK - confirm LED0 is blinking on the board"

    results = [rec]
    meta = dict(board=BOARD, flasher=args.flasher, vid="n/a",
                generated=R.ts(), stamp=run_stamp, zephyr_base=args.zephyr_base,
                build_only=args.build_only,
                commit=commit_info.get("commit", ""), short=commit_info.get("short", ""),
                branch=commit_info.get("branch", ""), commit_subject=commit_info.get("subject", ""))
    html_path, json_path = R.write_reports(results, meta, args.outdir)

    # ---- summary ----
    print("\n" + "=" * 60)
    print("BLINKY SANITY CHECK - {}".format(BOARD))
    print("=" * 60)
    print("  build : {}".format(rec["build"]))
    print("  flash : {}".format(rec["flash"]))
    if rec.get("reason"):
        print("  note  : {}".format(rec["reason"]))
    print("-" * 60)
    if rec["build"] == R.PASS and (args.build_only or rec["flash"] == R.PASS):
        print("RESULT: PASS  ->  LED0 should be blinking on the board.")
    else:
        print("RESULT: FAIL  ->  see logs in {}".format(os.path.join(args.outdir, "logs")))
    print("\nHTML report: {}".format(html_path))
    print("JSON report: {}".format(json_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
