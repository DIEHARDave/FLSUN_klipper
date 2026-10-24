#!/usr/bin/env python3
"""Config files for upstream Klipper on the FLSun T1 Pro.

    flsun_klipper_cfg.py create    Write printer_upstream.cfg, if it doesn't exist,
                                   from FLSun's printer.cfg: its [probe] section and
                                   its calibration (the SAVE_CONFIG block), and
                                   the heater control and delta calibration from
                                   its main sections if that lacks them.
    flsun_klipper_cfg.py includes  Write flsun_klipper_includes.cfg: the [include]
                                   lines of printer.cfg, where plugins add theirs,
                                   with stock-only files swapped for upstream ones.
                                   Also links the modules (as extras does) and adds
                                   what create would have to a printer_upstream.cfg
                                   that lacks it. Runs before every start of
                                   upstream Klipper.
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

# Settings each printer calibrates, which t1_pro.cfg can't set: upstream's
# SAVE_CONFIG refuses to save an option that an included file sets, which would
# break PID_CALIBRATE and DELTA_CALIBRATE. So printer_upstream.cfg itself needs
# them, unless its calibration has them. FLSun's printer.cfg has them in the
# SAVE_CONFIG block once calibrated (Klipper then comments out the main ones),
# else in the main sections.
# (name, the option that shows it's there, {section: options}, factory values)
HEATER_OPTIONS = r"control|pid_k[pid]|max_delta"
DELTA_OPTIONS = r"position_endstop|arm_length|angle"
CALIBRATION = [
    ("extruder heater control", ("extruder", "control"),
     {"extruder": HEATER_OPTIONS},
     {"extruder": ["control: pid", "pid_Kp: 14.155", "pid_Ki: 0.303",
                   "pid_Kd: 165.086"]}),
    ("bed heater control", ("heater_bed", "control"),
     {"heater_bed": HEATER_OPTIONS},
     {"heater_bed": ["control: pid", "pid_Kp: 18.240", "pid_Ki: 0.558",
                     "pid_Kd: 149.109"]}),
    # No factory values: a wrong position_endstop drives the nozzle into the bed.
    ("delta calibration", ("stepper_a", "position_endstop"),
     {"printer": r"delta_radius", "stepper_a": DELTA_OPTIONS,
      "stepper_b": DELTA_OPTIONS, "stepper_c": DELTA_OPTIONS},
     None),
]

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


def unsaved(saved):
    """A SAVE_CONFIG block as plain config, without the #*# prefixes."""
    return "\n".join(re.sub(r"^#\*# ?", "", l) for l in saved.split("\n"))


def option_lines(text, name, options):
    """The lines of section [name] that set one of options (a regex)."""
    return [l.strip() for l in section(text, name).split("\n")
            if re.match(r"^\s*(%s)\s*[:=]" % options, l, re.I)]


def missing_calibration(upstream_text, stock_text):
    """Sections with the CALIBRATION printer_upstream.cfg lacks, from
    printer.cfg's SAVE_CONFIG block, its main sections or the factory values.
    Returns (text, names of what was added, names of what wasn't found)."""
    config, saved = split_saved(upstream_text)
    stock_config, stock_saved = split_saved(stock_text)
    out, added, lost = [], [], []
    for name, (key_section, key), sections, factory in CALIBRATION:
        if any(option_lines(t, key_section, key) for t in (config, unsaved(saved))):
            continue
        for source, text in (("SAVE_CONFIG block", unsaved(stock_saved)),
                             ("settings", stock_config)):
            if option_lines(text, key_section, key):
                found = {s: option_lines(text, s, o) for s, o in sections.items()}
                break
        else:
            if not factory:
                lost.append(name)
                continue
            source, found = "factory settings", factory
        out.append("# The %s from printer.cfg's %s. Calibrating and SAVE_CONFIG\n"
                   "# override it.\n" % (name, source)
                   + "".join("[%s]\n%s\n" % (s, "\n".join(l))
                             for s, l in found.items() if l))
        added.append(name)
    return "\n".join(out), added, lost


# What missing_calibration wrote (and, worded a little differently, its first
# version): two comment lines, then the sections up to a blank line.
ADDED_RE = re.compile(r"^# The [\w ]+ from printer\.cfg's [a-z ]+\. .*\n"
                      r"# .*override it\.\n(?:.*\S.*\n)*", re.M)


def saved_options(saved):
    """{section: options} of a SAVE_CONFIG block."""
    out, name = {}, None
    for line in unsaved(saved).split("\n"):
        m = re.match(r"^\[([^\]]+)\]", line)
        if m:
            name = m.group(1).strip().lower()
        elif name:
            m = re.match(r"^(\w+)\s*[:=]", line)
            if m:
                out.setdefault(name, set()).add(m.group(1).lower())
    return out


def unshadow(config, saved):
    """Comment out the options in what missing_calibration added that the
    SAVE_CONFIG block also sets. Upstream Klipper takes the main file's value
    and drops the saved one, so they would hide a later calibration."""
    options = saved_options(saved)

    def strip(m):
        lines, name = m.group(0).split("\n"), None
        for i, line in enumerate(lines):
            h = re.match(r"^\[([^\]]+)\]", line)
            o = re.match(r"^(\w+)\s*[:=]", line)
            if h:
                name = h.group(1).strip().lower()
            elif o and o.group(1).lower() in options.get(name, ()):
                lines[i] = "#" + line
        return "\n".join(lines)
    return ADDED_RE.sub(strip, config)


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
    extra, added, lost = missing_calibration(saved, config + saved)
    if extra:
        text += extra + "\n"
    if saved:
        text += saved.rstrip("\n") + "\n"
    write(UPSTREAM_CFG, text)
    print("Wrote %s with [probe] and %s" % (
        UPSTREAM_CFG, "the calibration" if saved else "no calibration"))
    if added:
        print("Took the %s from printer.cfg" % ", ".join(added))
    if lost:
        print("printer.cfg has no %s" % ", ".join(lost))


def fix_calibration():
    """Give a printer_upstream.cfg made without them printer.cfg's SAVE_CONFIG
    block and the CALIBRATION upstream Klipper won't start without."""
    if not (os.path.exists(UPSTREAM_CFG) and os.path.exists(STOCK_CFG)):
        return
    text, stock_text = read(UPSTREAM_CFG), read(STOCK_CFG)
    config, saved = split_saved(text)
    notes = []
    if not saved:
        saved = split_saved(stock_text)[1]
        if saved:
            notes.append("printer.cfg's SAVE_CONFIG block")
    extra, added, lost = missing_calibration(config + saved, stock_text)
    notes += ["the %s" % n for n in added]
    for n in lost:
        print("%s has no %s, and printer.cfg neither" % (UPSTREAM_CFG, n))
    unshadowed = unshadow(config, saved)
    if not notes and unshadowed == config:
        return
    write(UPSTREAM_CFG, unshadowed.rstrip("\n") + "\n\n"
          + (extra + "\n" if extra else "") + saved.rstrip("\n") + "\n")
    if notes:
        print("Added %s to %s" % (", ".join(notes), UPSTREAM_CFG))
    if unshadowed != config:
        print("Commented out the added settings the SAVE_CONFIG block sets")


def cmd_includes():
    fix_calibration()
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
