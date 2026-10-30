#!/usr/bin/env python3
"""gifshrink: make an existing GIF smaller with ffmpeg.

Rebuilds the palette (palettegen + paletteuse), can drop frames and scale the
picture down, and can step through settings until the file fits a target size.
Needs ffmpeg only. The GIF is read in pure Python, so no ffprobe either.
Standard library only.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

__version__ = "1.0.0"

DITHERS = ("sierra2_4a", "bayer", "floyd_steinberg", "none")
STATS_MODES = ("full", "diff")
DEFAULT_COLORS = 128  # used only when no shrink option at all is given
MIN_WIDTH = 64


class GifShrinkError(Exception):
    """A problem the user can act on. Printed without a traceback."""


class SizeTargetError(GifShrinkError):
    """--target-size could not be met."""


# ----------------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------------

def parse_size(text):
    """'2M', '500K', '1.5MB', '2000000' -> bytes (KB and MB are 1024-based)."""
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([kKmMgG]?)[bB]?\s*", str(text))
    if not m:
        raise argparse.ArgumentTypeError(
            "bad size %r, use something like 2M, 500K or 2000000" % text)
    value = float(m.group(1))
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]
    size = int(value * mult)
    if size <= 0:
        raise argparse.ArgumentTypeError("size must be above zero")
    return size


def human_size(n):
    if n >= 1024 ** 2:
        return "%.2f MB" % (n / 1024 ** 2)
    if n >= 1024:
        return "%.1f KB" % (n / 1024)
    return "%d B" % n


def fmt_num(x):
    return "%g" % x


def _int_range(lo, hi, name):
    def conv(text):
        try:
            v = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError("%s must be a whole number" % name)
        if not lo <= v <= hi:
            raise argparse.ArgumentTypeError(
                "%s must be between %d and %d" % (name, lo, hi))
        return v
    return conv


def _positive_float(name):
    def conv(text):
        try:
            v = float(text)
        except ValueError:
            raise argparse.ArgumentTypeError("%s must be a number" % name)
        if v <= 0:
            raise argparse.ArgumentTypeError("%s must be above zero" % name)
        return v
    return conv


# ----------------------------------------------------------------------------
# GIF reader (pure Python)
# ----------------------------------------------------------------------------

def gif_info(path):
    """Parse a GIF. Returns dict(width, height, frames, duration, loop, delays).

    duration is in seconds. delays is the per-frame delay in centiseconds.
    loop is None when there is no NETSCAPE loop block (plays once), 0 for
    forever, or N for N extra plays.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise GifShrinkError("could not read %s: %s" % (path, exc.strerror or exc))
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 13:
        raise GifShrinkError("%s is not a GIF" % path)
    width = data[6] | (data[7] << 8)
    height = data[8] | (data[9] << 8)
    flags = data[10]
    pos = 13
    if flags & 0x80:
        pos += 3 * (2 ** ((flags & 7) + 1))
    delays = []
    pending = 0
    loop = None
    n = len(data)

    def skip_sub_blocks(p):
        while p < n:
            size = data[p]
            p += 1
            if size == 0:
                return p
            p += size
        return p

    try:
        while pos < n:
            b = data[pos]
            if b == 0x3B:
                break
            if b == 0x21:
                label = data[pos + 1]
                if label == 0xF9 and data[pos + 2] >= 4:
                    pending = data[pos + 4] | (data[pos + 5] << 8)
                elif label == 0xFF and data[pos + 2] == 11 \
                        and data[pos + 3:pos + 14] in (b"NETSCAPE2.0", b"ANIMEXTS1.0") \
                        and data[pos + 14] >= 3 and data[pos + 15] == 1:
                    loop = data[pos + 16] | (data[pos + 17] << 8)
                pos = skip_sub_blocks(pos + 2)
            elif b == 0x2C:
                delays.append(pending)
                pending = 0
                lflags = data[pos + 9]
                pos += 10
                if lflags & 0x80:
                    pos += 3 * (2 ** ((lflags & 7) + 1))
                pos += 1  # LZW minimum code size
                pos = skip_sub_blocks(pos)
            else:
                break
    except IndexError:
        pass  # truncated file: keep the frames read so far
    if not delays:
        raise GifShrinkError("%s has no readable frames" % path)
    return {"width": width, "height": height, "frames": len(delays),
            "duration": sum(delays) / 100.0, "loop": loop, "delays": delays}


def add_to_last_delay(path, extra_cs):
    """Lengthen the last frame of a GIF file by extra_cs centiseconds, in place."""
    with open(path, "rb") as fh:
        data = bytearray(fh.read())
    last = None
    pos = 13
    flags = data[10]
    if flags & 0x80:
        pos += 3 * (2 ** ((flags & 7) + 1))
    n = len(data)

    def skip_sub_blocks(p):
        while p < n:
            size = data[p]
            p += 1
            if size == 0:
                return p
            p += size
        return p

    while pos < n:
        b = data[pos]
        if b == 0x3B:
            break
        if b == 0x21:
            if data[pos + 1] == 0xF9 and data[pos + 2] >= 4:
                last = pos + 4
            pos = skip_sub_blocks(pos + 2)
        elif b == 0x2C:
            lflags = data[pos + 9]
            pos += 10
            if lflags & 0x80:
                pos += 3 * (2 ** ((lflags & 7) + 1))
            pos += 1
            pos = skip_sub_blocks(pos)
        else:
            break
    if last is None:
        return
    value = min(65535, (data[last] | (data[last + 1] << 8)) + int(extra_cs))
    data[last] = value & 0xFF
    data[last + 1] = value >> 8
    with open(path, "wb") as fh:
        fh.write(data)


# ----------------------------------------------------------------------------
# ffmpeg
# ----------------------------------------------------------------------------

def find_ffmpeg():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise GifShrinkError(
            "ffmpeg not found on your PATH. Install ffmpeg first:\n"
            "  macOS:          brew install ffmpeg\n"
            "  Debian/Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows:        winget install ffmpeg")
    return ffmpeg


def build_filter(width, fps, every, colors, dither, bayer_scale, stats_mode):
    """width/fps/every can be None (keep). Returns a -filter_complex string."""
    chain = []
    if every and every > 1:
        chain.append("select='not(mod(n\\,%d))'" % every)
    elif fps:
        chain.append("fps=%s" % fmt_num(fps))
    if width:
        chain.append("scale=%d:-2:flags=lanczos" % width)
    chain.append("split[a][b]")
    pal = "palettegen=max_colors=%d:stats_mode=%s" % (colors, stats_mode)
    use = "paletteuse=dither=%s" % dither
    if dither == "bayer":
        use += ":bayer_scale=%d" % bayer_scale
    use += ":diff_mode=rectangle"
    return "[0:v]%s;[a]%s[p];[b][p]%s" % (",".join(chain), pal, use)


def run_ffmpeg(ffmpeg, src, out, settings, loop):
    """One ffmpeg call. Writes to `out` (a temp path), raises on failure."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
           "-i", src, "-an", "-sn", "-dn",
           "-filter_complex",
           build_filter(settings["width"], settings["fps"], settings["every"],
                        settings["colors"], settings["dither"],
                        settings["bayer_scale"], settings["stats_mode"]),
           "-loop", str(loop), "-f", "gif", out]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True)
    except OSError as exc:
        raise GifShrinkError("could not run ffmpeg: %s" % exc)
    if res.returncode != 0:
        lines = [l for l in res.stderr.strip().splitlines() if l.strip()]
        tail = "\n  ".join(lines[-6:]) if lines else "(no message)"
        raise GifShrinkError("ffmpeg failed:\n  " + tail)
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise GifShrinkError("ffmpeg produced no output")


# ----------------------------------------------------------------------------
# Shrinking, with an optional size target
# ----------------------------------------------------------------------------

def even(x):
    return max(2, int(round(x / 2.0)) * 2)


def size_ladder(settings, src_info):
    """Return [(label, settings)] from best quality to smallest.

    Takes one step at a time from each of fps, width and colors in turn. Each
    step keeps the earlier ones, so the settings only ever get smaller.
    """
    src_w = src_info["width"]
    src_fps = None
    if src_info["duration"] > 0:
        src_fps = src_info["frames"] / src_info["duration"]
    if settings["every"] and src_fps:
        eff_fps = src_fps / settings["every"]
    elif settings["fps"]:
        eff_fps = min(settings["fps"], src_fps) if src_fps else settings["fps"]
    else:
        eff_fps = src_fps

    # each lever is a list of (label, change); the ladder takes one step from
    # each lever in turn, so no single setting is pushed to its limit first
    fps_steps = []
    for f in (20, 15, 12, 10, 8, 6, 5):
        if eff_fps is None or f < eff_fps - 0.01:
            fps_steps.append(("fps %s" % fmt_num(f), {"fps": f, "every": None}))
            eff_fps = f

    width_steps = []
    base_w = min(settings["width"] or src_w, src_w)
    seen = {base_w}
    for factor in (0.85, 0.7, 0.55, 0.4, 0.3):
        w = even(base_w * factor)
        if w < MIN_WIDTH or w in seen:
            continue
        seen.add(w)
        width_steps.append(("width %d" % w, {"width": w}))

    color_steps = [("%d colors" % c, {"colors": c})
                   for c in (128, 64, 32, 16) if c < settings["colors"]]

    cur = dict(settings)
    steps = [("as requested", dict(cur))]
    levers = [fps_steps, width_steps, color_steps]
    while any(levers):
        for lever in levers:
            if lever:
                label, change = lever.pop(0)
                cur = dict(cur, **change)
                steps.append((label, dict(cur)))
    return steps


def shrink(src, out, settings, loop_arg, once, target, overwrite, say=print):
    """Shrink `src` into `out`. Returns (src_info, out_info, chosen, label).

    loop_arg is None to keep the input's loop count, otherwise an int.
    """
    ffmpeg = find_ffmpeg()
    if not os.path.isfile(src):
        raise GifShrinkError("input file not found: %s" % src)
    if os.path.isdir(out):
        raise GifShrinkError("output is a folder, give a file name: %s" % out)
    out_dir = os.path.dirname(os.path.abspath(out))
    if not os.path.isdir(out_dir):
        raise GifShrinkError("output folder does not exist: %s" % out_dir)
    if os.path.exists(out) and os.path.samefile(out, src):
        raise GifShrinkError("output would overwrite the input file")
    if os.path.exists(out) and not overwrite:
        raise GifShrinkError("%s already exists. Use --overwrite to replace it." % out)

    info = gif_info(src)
    if once:
        loop = -1
    elif loop_arg is not None:
        loop = loop_arg
    else:
        loop = -1 if info["loop"] is None else info["loop"]

    settings = dict(settings)
    if settings["width"] and settings["width"] >= info["width"]:
        if settings["width"] > info["width"]:
            say("note: the GIF is only %d px wide, keeping that instead of %d" % (
                info["width"], settings["width"]))
        settings["width"] = None
    src_fps = info["frames"] / info["duration"] if info["duration"] > 0 else None
    if settings["fps"] and src_fps and settings["fps"] >= src_fps - 0.01:
        if settings["fps"] > src_fps + 0.01:
            say("note: the GIF runs at about %s fps, keeping that instead of %s" % (
                fmt_num(round(src_fps, 2)), fmt_num(settings["fps"])))
        settings["fps"] = None
    if settings["every"] and settings["every"] > info["frames"]:
        raise GifShrinkError("--every %d is more than the %d frames in the GIF" % (
            settings["every"], info["frames"]))

    tmp = os.path.join(out_dir, ".%s.part" % os.path.basename(out))
    chosen, label = None, None
    try:
        if target is not None and os.path.getsize(src) <= target:
            say("the GIF is already %s, under the %s target. Copying it unchanged." % (
                human_size(os.path.getsize(src)), human_size(target)))
            shutil.copyfile(src, tmp)
            chosen, label = settings, "already fits"
        elif target is None:
            run_ffmpeg(ffmpeg, src, tmp, settings, loop)
            chosen = settings
            _fix_duration(tmp, info, settings)
        else:
            ladder = size_ladder(settings, info)
            smallest, last = None, None
            for i, (lab, trial) in enumerate(ladder, 1):
                run_ffmpeg(ffmpeg, src, tmp, trial, loop)
                _fix_duration(tmp, info, trial)
                size = os.path.getsize(tmp)
                smallest = size if smallest is None else min(smallest, size)
                last = trial
                fits = size <= target
                say("try %2d/%-2d %-13s %4s px  %4s  %3d colors  ->  %s  %s" % (
                    i, len(ladder), lab, trial["width"] or info["width"],
                    _fps_text(trial, info), trial["colors"], human_size(size),
                    "fits" if fits else "too big"))
                if fits:
                    chosen, label = trial, lab
                    break
            if chosen is None:
                raise SizeTargetError(
                    "could not fit %s. The smallest try was %s (%s px wide, %s, %d colors). "
                    "No file was written. Ask for a larger --target-size, or cut the GIF "
                    "shorter first." % (
                        human_size(target), human_size(smallest),
                        last["width"] or info["width"], _fps_text(last, info),
                        last["colors"]))
        os.replace(tmp, out)
    except BaseException:
        _cleanup(tmp)
        raise
    return info, gif_info(out), chosen, label


def _fps_text(s, info):
    if s["every"]:
        return "every %d" % s["every"]
    if s["fps"]:
        return "%s fps" % fmt_num(s["fps"])
    return "keep fps"


def _fix_duration(path, src_info, settings):
    """Dropping frames with select shortens the last frame. Give the lost time
    back so the GIF keeps its total duration."""
    if not settings.get("every"):
        return
    got = gif_info(path)
    short = round(src_info["duration"] * 100) - round(got["duration"] * 100)
    if short > 0:
        add_to_last_delay(path, short)


def _cleanup(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def loop_text(loop):
    if loop is None:
        return "plays once"
    if loop == 0:
        return "loops forever"
    return "repeats %d times" % loop


def print_report(src, out, before, after):
    sb, sa = os.path.getsize(src), os.path.getsize(out)
    change = (sa - sb) * 100.0 / sb if sb else 0.0
    rows = [
        ("size", "%s (%d bytes)" % (human_size(sb), sb),
         "%s (%d bytes)  %+.0f%%" % (human_size(sa), sa, change)),
        ("dimensions", "%d x %d" % (before["width"], before["height"]),
         "%d x %d" % (after["width"], after["height"])),
        ("frames", "%d" % before["frames"], "%d" % after["frames"]),
        ("duration", "%.2f s" % before["duration"], "%.2f s" % after["duration"]),
        ("loop", loop_text(before["loop"]), loop_text(after["loop"])),
    ]
    w = max(len(r[1]) for r in rows)
    print("%-11s %-*s  %s" % ("", w, "before", "after"))
    for name, b, a in rows:
        print("%-11s %-*s  %s" % (name, w, b, a))
    if sa >= sb:
        print("warning: the new GIF is not smaller. Try a smaller --width, a lower "
              "--fps or fewer --colors, or use --target-size.")


def build_parser():
    p = argparse.ArgumentParser(
        prog="gifshrink",
        description="Make an existing GIF smaller with ffmpeg. Rebuilds the "
                    "palette, and can drop frames and scale down.",
        epilog="examples:\n"
               "  gifshrink big.gif\n"
               "  gifshrink big.gif --width 320 --colors 64\n"
               "  gifshrink big.gif --fps 10 -o small.gif\n"
               "  gifshrink big.gif --target-size 500K",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="the GIF to shrink")
    p.add_argument("-o", "--output",
                   help="output file (default: input name with .small.gif)")
    p.add_argument("--overwrite", action="store_true",
                   help="replace the output file if it exists")
    p.add_argument("--colors", type=_int_range(2, 256, "colors"), metavar="N",
                   help="palette size, 2 to 256 (default %d when no other shrink option is given, "
                        "otherwise 256)" % DEFAULT_COLORS)
    p.add_argument("--width", type=_int_range(16, 7680, "width"), metavar="PX",
                   help="scale down to this width, height follows (never scales up)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--fps", type=_positive_float("fps"), metavar="N",
                   help="drop frames to this rate, the total duration stays the same")
    g.add_argument("--every", type=_int_range(2, 1000, "every"), metavar="N",
                   help="keep every Nth frame, the total duration stays the same")
    p.add_argument("--dither", choices=DITHERS, default="sierra2_4a",
                   help="dither method (default sierra2_4a)")
    p.add_argument("--bayer-scale", type=_int_range(0, 5, "bayer scale"), default=2,
                   metavar="0-5", help="bayer pattern size, only with --dither bayer (default 2)")
    p.add_argument("--stats-mode", choices=STATS_MODES, default="full",
                   help="palette stats: full = whole frame, diff = moving parts (default full)")
    p.add_argument("--target-size", type=parse_size, metavar="SIZE",
                   help="file size to fit, like 2M or 500K. Steps through lower fps, "
                        "smaller width and fewer colors until it fits. Exit code 3 and "
                        "no file if it cannot")
    lg = p.add_mutually_exclusive_group()
    lg.add_argument("--loop", type=_int_range(0, 65535, "loop"), metavar="N",
                    help="extra repeats, 0 = forever. Default: keep the input's loop count")
    lg.add_argument("--once", action="store_true", help="play once and stop")
    p.add_argument("--version", action="version", version="gifshrink " + __version__)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    out = args.output
    if not out:
        base, _ = os.path.splitext(args.input)
        out = base + ".small.gif"

    colors = args.colors
    plain = not (args.colors or args.width or args.fps or args.every or args.target_size)
    if colors is None:
        colors = DEFAULT_COLORS if plain else 256
        if plain:
            print("note: no shrink option given, using --colors %d" % DEFAULT_COLORS)
    settings = {"width": args.width, "fps": args.fps, "every": args.every,
                "colors": colors, "dither": args.dither,
                "bayer_scale": args.bayer_scale, "stats_mode": args.stats_mode}
    if args.bayer_scale != 2 and args.dither != "bayer":
        print("note: --bayer-scale only applies with --dither bayer")

    try:
        before, after, chosen, label = shrink(
            args.input, out, settings, args.loop, args.once, args.target_size,
            args.overwrite)
    except SizeTargetError as exc:
        print("gifshrink: error: %s" % exc, file=sys.stderr)
        return 3
    except GifShrinkError as exc:
        print("gifshrink: error: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ngifshrink: cancelled", file=sys.stderr)
        return 130

    if args.target_size is not None and label != "already fits":
        print("chose: %s px wide, %s, %d colors (%s)" % (
            chosen["width"] or before["width"], _fps_text(chosen, before),
            chosen["colors"],
            "first try fit" if label == "as requested" else "reduced to " + label))
    print("wrote %s" % out)
    print_report(args.input, out, before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
