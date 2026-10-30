"""Tests for gifshrink. Needs ffmpeg on PATH.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gifshrink  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg"))


BUSY = "testsrc2=size=320x180:rate=15:duration=2"
# a slow, smooth colour swirl: the hard case for GIF. Deterministic.
SMOOTH = ("nullsrc=size=320x180:rate=15:duration=2,format=rgb24,"
          "geq=r='110+60*sin(X/70+T*0.5)':g='110+60*sin(Y/50-T*0.4)':"
          "b='130+60*sin((X+Y)/90+T*0.6)'")


def make_gif(path, lavfi, loop):
    """A small GIF made the usual way: a palette built from its own frames."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", lavfi,
           "-filter_complex", "[0:v]split[a][b];[a]palettegen[p];[b][p]paletteuse",
           "-loop", str(loop), "-f", "gif", path]
    subprocess.run(cmd, check=True)


def run_cli(*args):
    """Run main() in-process, return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = gifshrink.main(list(args))
        except SystemExit as exc:  # argparse errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class ParseTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(gifshrink.parse_size("500K"), 500 * 1024)
        self.assertEqual(gifshrink.parse_size("2M"), 2 * 1024 * 1024)
        self.assertEqual(gifshrink.parse_size("1.5MB"), int(1.5 * 1024 * 1024))
        self.assertEqual(gifshrink.parse_size("2000"), 2000)

    def test_bad_size(self):
        for bad in ("big", "", "-3M", "0"):
            with self.assertRaises(argparse.ArgumentTypeError):
                gifshrink.parse_size(bad)

    def test_filter_has_two_pass_palette(self):
        f = gifshrink.build_filter(160, None, None, 64, "sierra2_4a", 2, "full")
        self.assertIn("split", f)
        self.assertIn("palettegen=max_colors=64", f)
        self.assertIn("paletteuse=dither=sierra2_4a", f)
        self.assertIn("scale=160", f)
        self.assertNotIn("fps=", f)

    def test_filter_every_and_bayer(self):
        f = gifshrink.build_filter(None, None, 3, 256, "bayer", 4, "diff")
        self.assertIn("not(mod(n", f)
        self.assertIn("bayer_scale=4", f)
        self.assertIn("stats_mode=diff", f)
        self.assertNotIn("lanczos", f)

    def test_ladder_only_gets_smaller(self):
        info = {"width": 320, "height": 180, "frames": 30, "duration": 2.0}
        s = {"width": None, "fps": None, "every": None, "colors": 256,
             "dither": "sierra2_4a", "bayer_scale": 2, "stats_mode": "full"}
        ladder = gifshrink.size_ladder(s, info)
        self.assertEqual(ladder[0][0], "as requested")
        prev = (320, 15, 256)
        for _, st in ladder[1:]:
            now = (st["width"] or 320, st["fps"] or 15, st["colors"])
            self.assertTrue(all(a <= b for a, b in zip(now, prev)), (now, prev))
            self.assertNotEqual(now, prev)
            prev = now
        self.assertGreater(len(ladder), 8)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class GifTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="gifshrink-test-")
        cls.busy = os.path.join(cls.dir, "busy.gif")          # forever
        cls.smooth = os.path.join(cls.dir, "smooth.gif")      # 3 repeats
        cls.once = os.path.join(cls.dir, "once.gif")          # no loop block
        make_gif(cls.busy, BUSY, 0)
        make_gif(cls.smooth, SMOOTH, 3)
        make_gif(cls.once, BUSY, -1)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.out = os.path.join(self.dir, "out-%s.gif" % self.id().split(".")[-1])

    def tearDown(self):
        for name in os.listdir(self.dir):
            if name.startswith("out-") or name.startswith(".out-"):
                os.remove(os.path.join(self.dir, name))

    def no_leftovers(self):
        return [n for n in os.listdir(self.dir) if n.endswith(".part")]

    # reading the GIF ------------------------------------------------------

    def test_gif_info_matches_ffprobe(self):
        info = gifshrink.gif_info(self.busy)
        self.assertEqual((info["width"], info["height"]), (320, 180))
        self.assertEqual(info["frames"], 30)
        self.assertAlmostEqual(info["duration"], 2.0, delta=0.05)
        self.assertEqual(len(info["delays"]), 30)
        if shutil.which("ffprobe"):
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                 "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", self.busy],
                stdout=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(int(res.stdout.strip()), info["frames"])

    def test_gif_info_loop_values(self):
        self.assertEqual(gifshrink.gif_info(self.busy)["loop"], 0)
        self.assertEqual(gifshrink.gif_info(self.smooth)["loop"], 3)
        self.assertIsNone(gifshrink.gif_info(self.once)["loop"])

    def test_truncated_gif_still_reads(self):
        cut = os.path.join(self.dir, "out-cut.gif")
        with open(self.busy, "rb") as fh:
            data = fh.read()
        with open(cut, "wb") as fh:
            fh.write(data[:len(data) // 2])
        info = gifshrink.gif_info(cut)
        self.assertTrue(0 < info["frames"] < 30)

    def test_add_to_last_delay(self):
        shutil.copyfile(self.busy, self.out)
        before = gifshrink.gif_info(self.out)
        gifshrink.add_to_last_delay(self.out, 50)
        after = gifshrink.gif_info(self.out)
        self.assertEqual(after["delays"][-1], before["delays"][-1] + 50)
        self.assertEqual(after["delays"][:-1], before["delays"][:-1])
        self.assertEqual(after["frames"], before["frames"])

    # shrinking ------------------------------------------------------------

    def test_default_is_valid_and_prints_before_after(self):
        code, out, err = run_cli(self.smooth, "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertIn("--colors 128", out)
        self.assertIn("before", out)
        self.assertIn("dimensions", out)
        self.assertLess(os.path.getsize(self.out), os.path.getsize(self.smooth))

    def test_default_output_name(self):
        local = os.path.join(self.dir, "out-name.gif")
        shutil.copyfile(self.busy, local)
        code, out, err = run_cli(local, "--colors", "64")
        self.assertEqual(code, 0, err)
        expect = os.path.join(self.dir, "out-name.small.gif")
        self.assertTrue(os.path.exists(expect))
        self.assertEqual(gifshrink.gif_info(local)["frames"], 30)  # input untouched

    def test_width_makes_it_smaller(self):
        code, _, err = run_cli(self.busy, "--width", "160", "-o", self.out)
        self.assertEqual(code, 0, err)
        info = gifshrink.gif_info(self.out)
        self.assertEqual((info["width"], info["height"]), (160, 90))
        self.assertLess(os.path.getsize(self.out), os.path.getsize(self.busy))
        self.assertEqual(info["frames"], 30)

    def test_width_never_scales_up(self):
        code, out, err = run_cli(self.busy, "--width", "640", "--colors", "128",
                                 "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertIn("only 320 px wide", out)
        self.assertEqual(gifshrink.gif_info(self.out)["width"], 320)

    def test_fewer_colors_makes_it_smaller(self):
        code, _, err = run_cli(self.smooth, "--colors", "32", "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertLess(os.path.getsize(self.out), os.path.getsize(self.smooth))

    def test_fps_drops_frames_and_keeps_duration(self):
        code, _, err = run_cli(self.busy, "--fps", "5", "-o", self.out)
        self.assertEqual(code, 0, err)
        before, after = gifshrink.gif_info(self.busy), gifshrink.gif_info(self.out)
        self.assertAlmostEqual(after["frames"], 10, delta=1)
        self.assertAlmostEqual(after["duration"], before["duration"], delta=0.1)
        self.assertLess(os.path.getsize(self.out), os.path.getsize(self.busy))

    def test_every_keeps_every_nth_and_the_duration(self):
        code, _, err = run_cli(self.busy, "--every", "3", "-o", self.out)
        self.assertEqual(code, 0, err)
        before, after = gifshrink.gif_info(self.busy), gifshrink.gif_info(self.out)
        self.assertEqual(after["frames"], 10)
        self.assertAlmostEqual(after["duration"], before["duration"], delta=0.03)
        self.assertLess(os.path.getsize(self.out), os.path.getsize(self.busy))

    def test_fps_above_source_is_left_alone(self):
        code, out, err = run_cli(self.busy, "--fps", "60", "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertIn("keeping that", out)
        self.assertEqual(gifshrink.gif_info(self.out)["frames"], 30)

    def test_every_one_is_rejected_and_too_big_every_errors(self):
        self.assertEqual(run_cli(self.busy, "--every", "1", "-o", self.out)[0], 2)
        code, _, err = run_cli(self.busy, "--every", "99", "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("frames", err)

    def test_every_dither_runs(self):
        for dither in gifshrink.DITHERS:
            with self.subTest(dither=dither):
                code, _, err = run_cli(self.smooth, "--colors", "64", "--dither", dither,
                                       "--overwrite", "-o", self.out)
                self.assertEqual(code, 0, err)
                self.assertGreater(os.path.getsize(self.out), 0)

    def test_dither_changes_the_file(self):
        sizes = set()
        for dither in ("sierra2_4a", "none"):
            run_cli(self.busy, "--colors", "64", "--dither", dither, "--overwrite",
                    "-o", self.out)
            sizes.add(os.path.getsize(self.out))
        self.assertEqual(len(sizes), 2)

    def test_warns_when_not_smaller(self):
        # a tiny already-optimised GIF at full palette cannot shrink by re-palettising
        tiny = os.path.join(self.dir, "out-tiny.gif")
        make_gif(tiny, "color=c=red:size=32x32:rate=5:duration=1", 0)
        code, out, _ = run_cli(tiny, "--colors", "256", "--overwrite", "-o", self.out)
        self.assertEqual(code, 0)
        if os.path.getsize(self.out) >= os.path.getsize(tiny):
            self.assertIn("not smaller", out)

    # target size ----------------------------------------------------------

    def test_target_size_really_fits(self):
        for src in (self.busy, self.smooth):
            for target in (100 * 1024, 40 * 1024):
                with self.subTest(src=os.path.basename(src), target=target):
                    code, out, err = run_cli(src, "--target-size", str(target),
                                             "--overwrite", "-o", self.out)
                    self.assertEqual(code, 0, err)
                    self.assertLessEqual(os.path.getsize(self.out), target)
                    self.assertIn("try  1/", out)
                    self.assertIn("fits", out)
                    self.assertEqual(self.no_leftovers(), [])

    def test_target_size_prints_every_try_until_fit(self):
        code, out, _ = run_cli(self.busy, "--target-size", "60K", "-o", self.out)
        self.assertEqual(code, 0)
        tries = [l for l in out.splitlines() if l.startswith("try")]
        self.assertGreater(len(tries), 2)
        self.assertTrue(tries[-1].endswith("fits"))
        self.assertTrue(all(t.endswith("too big") for t in tries[:-1]))

    def test_target_size_cannot_fit_exits_3_and_writes_nothing(self):
        code, out, err = run_cli(self.busy, "--target-size", "1K", "-o", self.out)
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.no_leftovers(), [])
        self.assertIn("could not fit", err)
        self.assertIn("No file was written", err)
        self.assertGreater(len([l for l in out.splitlines() if l.startswith("try")]), 5)

    def test_target_size_failure_keeps_existing_output(self):
        with open(self.out, "wb") as fh:
            fh.write(b"keep me")
        code, _, _ = run_cli(self.busy, "--target-size", "1K", "--overwrite", "-o", self.out)
        self.assertEqual(code, 3)
        with open(self.out, "rb") as fh:
            self.assertEqual(fh.read(), b"keep me")

    def test_target_size_already_fits_copies_unchanged(self):
        code, out, _ = run_cli(self.busy, "--target-size", "10M", "-o", self.out)
        self.assertEqual(code, 0)
        self.assertIn("already", out)
        with open(self.busy, "rb") as a, open(self.out, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_target_size_with_every_first_try_then_steps(self):
        code, out, err = run_cli(self.busy, "--every", "2", "--target-size", "40K",
                                 "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertIn("every 2", out)
        self.assertLessEqual(os.path.getsize(self.out), 40 * 1024)

    # loop count -----------------------------------------------------------

    def test_loop_is_kept(self):
        for src, expect in ((self.busy, 0), (self.smooth, 3), (self.once, None)):
            with self.subTest(src=os.path.basename(src)):
                code, _, err = run_cli(src, "--colors", "64", "--overwrite", "-o", self.out)
                self.assertEqual(code, 0, err)
                self.assertEqual(gifshrink.gif_info(self.out)["loop"], expect)

    def test_loop_is_kept_with_target_size(self):
        code, _, err = run_cli(self.smooth, "--target-size", "30K", "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertEqual(gifshrink.gif_info(self.out)["loop"], 3)

    def test_loop_flag_overrides(self):
        run_cli(self.smooth, "--colors", "64", "--loop", "7", "-o", self.out)
        self.assertEqual(gifshrink.gif_info(self.out)["loop"], 7)
        run_cli(self.smooth, "--colors", "64", "--loop", "0", "--overwrite", "-o", self.out)
        self.assertEqual(gifshrink.gif_info(self.out)["loop"], 0)
        run_cli(self.busy, "--colors", "64", "--once", "--overwrite", "-o", self.out)
        self.assertIsNone(gifshrink.gif_info(self.out)["loop"])
        run_cli(self.once, "--colors", "64", "--loop", "2", "--overwrite", "-o", self.out)
        self.assertEqual(gifshrink.gif_info(self.out)["loop"], 2)

    def test_transparency_is_kept(self):
        tr = os.path.join(self.dir, "out-transparent.gif")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "color=c=black@0.0:s=96x96:r=10:d=1,format=rgba",
             "-f", "lavfi", "-i", "color=c=blue:s=40x40:r=10:d=1,format=rgba",
             "-filter_complex",
             "[0][1]overlay=10:10:format=auto,format=rgba,split[a][b];"
             "[a]palettegen=reserve_transparent=1[p];[b][p]paletteuse=alpha_threshold=128",
             "-loop", "0", "-f", "gif", tr], check=True)
        code, _, err = run_cli(tr, "--colors", "16", "-o", self.out)
        self.assertEqual(code, 0, err)
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", self.out,
             "-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
            stdout=subprocess.PIPE, check=True)
        px = res.stdout
        corner = px[3]
        centre = px[(30 * 96 + 30) * 4 + 3]
        self.assertEqual(corner, 0)      # background still transparent
        self.assertEqual(centre, 255)    # the square still opaque

    # error paths ----------------------------------------------------------

    def test_missing_input(self):
        code, _, err = run_cli(os.path.join(self.dir, "nope.gif"), "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_input_is_not_a_gif(self):
        txt = os.path.join(self.dir, "out-text.gif")
        with open(txt, "w") as fh:
            fh.write("this is not a gif at all")
        code, _, err = run_cli(txt, "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("not a GIF", err)
        self.assertFalse(os.path.exists(self.out))

    def test_output_exists_needs_overwrite(self):
        with open(self.out, "wb") as fh:
            fh.write(b"old")
        code, _, err = run_cli(self.busy, "--colors", "64", "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("--overwrite", err)
        with open(self.out, "rb") as fh:
            self.assertEqual(fh.read(), b"old")
        code, _, _ = run_cli(self.busy, "--colors", "64", "--overwrite", "-o", self.out)
        self.assertEqual(code, 0)

    def test_output_cannot_be_the_input(self):
        local = os.path.join(self.dir, "out-same.gif")
        shutil.copyfile(self.busy, local)
        code, _, err = run_cli(local, "-o", local, "--overwrite")
        self.assertEqual(code, 1)
        self.assertIn("overwrite the input", err)

    def test_output_folder_missing_or_is_a_folder(self):
        code, _, err = run_cli(self.busy, "-o", os.path.join(self.dir, "nodir", "x.gif"))
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)
        code, _, err = run_cli(self.busy, "-o", self.dir)
        self.assertEqual(code, 1)
        self.assertIn("folder", err)

    def test_bad_options_exit_2(self):
        for args in (["--target-size", "huge"], ["--target-size", "0"],
                     ["--colors", "1"], ["--colors", "300"], ["--width", "0"],
                     ["--fps", "0"], ["--fps", "5", "--every", "2"],
                     ["--loop", "2", "--once"], ["--dither", "wavy"]):
            with self.subTest(args=args):
                code, _, _ = run_cli(self.busy, *args)
                self.assertEqual(code, 2)

    def test_no_ffmpeg(self):
        with mock.patch("gifshrink.shutil.which", return_value=None):
            code, _, err = run_cli(self.busy, "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("ffmpeg not found", err)

    def test_ffmpeg_failure_is_reported_and_cleaned_up(self):
        bad = {"width": 320, "fps": None, "every": None, "colors": 256,
               "dither": "sierra2_4a", "bayer_scale": 2, "stats_mode": "full"}
        with mock.patch("gifshrink.build_filter", return_value="[0:v]nonsense[x]"):
            with self.assertRaises(gifshrink.GifShrinkError):
                gifshrink.shrink(self.busy, self.out, bad, None, False, None, False,
                                 say=lambda *_: None)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.no_leftovers(), [])


if __name__ == "__main__":
    unittest.main()
