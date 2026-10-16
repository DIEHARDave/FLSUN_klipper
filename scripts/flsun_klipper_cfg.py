#!/usr/bin/env python3
"""Config files for upstream Klipper on the FLSun T1 Pro.

    flsun_klipper_cfg.py create    Write printer_upstream.cfg, if it doesn't exist,
                                   from FLSun's printer.cfg: its [probe] section and
                                   its calibration (the SAVE_CONFIG block).
    flsun_klipper_cfg.py includes  Write flsun_klipper_includes.cfg: the [include]
                                   lines of printer.cfg, where plugins add theirs,
                                   with stock-only files swapped for upstream ones.
                                   Also links the modules (as extras does). Runs
                                   before every start of upstream Klipper.
    flsun_klipper_cfg.py extras    Link this repo's extras/*.py into upstream
                                   Klipper's klippy/extras, hidden from git.

Standard library only, and Python 3.7, so FLSun's system Python can run it.
"""

import glob
import os
import re
import sys

CONFIG_DIR = os.environ.get("FLSUN_KLIPPER_CONFIG_DIR",
                            os.path.expanduser("~pi/printer_data/config"))
STOCK_CFG = os.path.join(CONFIG_DIR, "printer.cfg")
UPSTREAM_CFG = os.path.join(CONFIG_DIR, "printer_upstream.cfg")
INCLUDES_CFG = os.path.join(CONFIG_DIR, "flsun_klipper_includes.cfg")
SAVE_MARK = "#*# <---------------------- SAVE_CONFIG ---------------------->"

# Files included by printer.cfg that only work with FLSun's Klipper, and what
# upstream Klipper includes instead (None: nothing). Wildcard includes are
# expanded file by file, so the rest of a folder is still included.
REPLACE = {
    # FLSUN_Macros defines SET_PRINT_STATS_INFO, which upstream has built in.
    "flsun_macros/pause_at_layer.cfg": "flsun_klipper/compat/flsun_macros.cfg",
}

REPO_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
EXTRAS_DIR = os.path.join(REPO_DIR, "extras")
KLIPPER_DIR = os.environ.get("FLSUN_KLIPPER_KLIPPER_DIR",
                             os.path.expanduser("~pi/klipper-upstream"))

INCLUDE_RE = re.compile(r"^\[include\s+([^\]]+?)\s*\]")

HEADER = """\
# printer.cfg for UPSTREAM Klipper on the FLSun T1 Pro, used while FLSUN_klipper
# runs upstream Klipper. FLSun's printer.cfg stays as it was for FLSun's Klipper.
#
# The hardware and FLSun's macros come from flsun_klipper/t1_pro.cfg, which is
# updated with FLSUN_klipper. Add your own changes below it. The [include] lines
# that plugins add to printer.cfg are copied into flsun_klipper_includes.cfg
# each time Klipper starts, so you don't need to repeat them here.
#
# Made from printer.cfg on {date}: [probe] and the calibration below.

[include flsun_klipper/t1_pro.cfg]
[include flsun_klipper_includes.cfg]

"""


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def split_saved(text):
    """(config, SAVE_CONFIG block or "")."""
    i = text.find(SAVE_MARK)
    return (text, "") if i < 0 else (text[:i], text[i:])


def section(text, name):
    """The lines of section [name], up to the next section header."""
    out, inside = [], False
    for line in text.split("\n"):
        if re.match(r"^\[", line):
            if inside:
                break
            inside = line.split("]")[0].strip("[ ").lower() == name
        if inside:
            out.append(line.rstrip())
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def cmd_create():
    if os.path.exists(UPSTREAM_CFG):
        print("%s exists; left as it is" % UPSTREAM_CFG)
        return
    config, saved = split_saved(read(STOCK_CFG))
    probe = section(config, "probe")
    if not probe:
        sys.exit("No [probe] section in %s" % STOCK_CFG)
    import time
    text = HEADER.format(date=time.strftime("%Y-%m-%d")) + probe + "\n\n"
    if saved:
        text += saved.rstrip("\n") + "\n"
    write(UPSTREAM_CFG, text)
    print("Wrote %s with [probe] and %s" % (
        UPSTREAM_CFG, "the calibration" if saved else "no calibration"))


def cmd_includes():
    lines = []
    if os.path.exists(STOCK_CFG):
        config, _saved = split_saved(read(STOCK_CFG))
        for line in config.split("\n"):
            m = INCLUDE_RE.match(line)
            if not m:
                continue
            name = m.group(1)
            if not any(c in name for c in "*?["):
                names = [name]
            else:
                # Klipper expands wildcards from the including file's folder.
                names = sorted(os.path.relpath(f, CONFIG_DIR).replace(os.sep, "/")
                               for f in glob.glob(os.path.join(CONFIG_DIR, name)))
                if not any(n in REPLACE for n in names):
                    lines.append("[include %s]" % name)
                    continue
            for n in names:
                if n in REPLACE:
                    if REPLACE[n]:
                        lines.append("[include %s]  # instead of %s" % (REPLACE[n], n))
                else:
                    lines.append("[include %s]" % n)
    text = ("# Written by FLSUN_klipper each time upstream Klipper starts: the [include]\n"
            "# lines of printer.cfg. Don't edit; changes are overwritten.\n\n"
            + "\n".join(lines) + "\n")
    try:
        if read(INCLUDES_CFG) == text:
            return
    except OSError:
        pass
    write(INCLUDES_CFG, text)


def cmd_extras():
    target = os.path.join(KLIPPER_DIR, "klippy", "extras")
    if not os.path.isdir(target):
        return
    exclude = os.path.join(KLIPPER_DIR, ".git", "info", "exclude")
    try:
        ignored = set(read(exclude).splitlines())
    except OSError:
        ignored = set()
    new_ignored = []
    for name in sorted(os.listdir(EXTRAS_DIR)):
        if not name.endswith(".py"):
            continue
        src = os.path.join(EXTRAS_DIR, name)
        link = os.path.join(target, name)
        if os.path.islink(link) and os.readlink(link) == src:
            pass
        elif os.path.lexists(link) and not os.path.islink(link):
            print("%s is Klipper's own file; not replaced" % link)
            continue
        else:
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(src, link)
            print("Linked %s" % link)
        rel = "klippy/extras/" + name
        if rel not in ignored:
            new_ignored.append(rel)
    # Links to modules this repo no longer has.
    for name in os.listdir(target):
        link = os.path.join(target, name)
        if (os.path.islink(link)
                and os.readlink(link).startswith(EXTRAS_DIR + os.sep)
                and not os.path.exists(link)):
            os.remove(link)
            print("Removed %s" % link)
    if new_ignored and os.path.isdir(os.path.dirname(exclude)):
        with open(exclude, "a", encoding="utf-8") as f:
            f.writelines(n + os.linesep for n in new_ignored)


if __name__ == "__main__":
    commands = {"create": cmd_create, "includes": cmd_includes,
                "extras": cmd_extras}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
    if sys.argv[1] == "includes":
        try:
            cmd_extras()
        except OSError as e:
            print("Couldn't link the modules: %s" % e)
