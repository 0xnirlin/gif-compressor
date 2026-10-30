# gifshrink

Make an existing GIF smaller with ffmpeg. One Python file, no dependencies, and an optional target file size.

```
gifshrink big.gif
gifshrink big.gif --width 320 --colors 64
gifshrink big.gif --target-size 500K
```

If you searched for "compress gif", "reduce gif size" or "gif too big", this is the command you were trying to remember. It needs ffmpeg and nothing else. No gifsicle, no ImageMagick.

## What it does

- Rebuilds the palette from the GIF's own frames (palettegen and paletteuse, in one ffmpeg call), with as many colors as you allow.
- Drops frames with `--fps 8` or `--every 2` (keep every 2nd frame). The total duration stays the same, so the GIF plays at the same speed with fewer, longer frames.
- Scales down with `--width`. It never scales up.
- Lets you pick the dither.
- With `--target-size 500K` it steps through lower fps, smaller width and fewer colors until the file fits, prints every try, and exits with code 3 and **no file** if nothing fits.
- Keeps the input's loop count (forever, N times, or play once) unless you pass `--loop` or `--once`.
- Prints size, dimensions, frames, duration and loop for the GIF before and after.
- Never touches the original. The result goes to `name.small.gif` unless you pick `-o`.
- Runs on your machine. Nothing is uploaded.

## Install

You need Python 3.8 or newer and ffmpeg on your PATH.

```
# ffmpeg
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Debian, Ubuntu
winget install ffmpeg          # Windows

# gifshrink
pipx install git+https://github.com/0xnirlin/gif-compressor
```

Or skip the install. The tool is the single file `gifshrink.py`. Clone the repo and run it directly:

```
git clone https://github.com/0xnirlin/gif-compressor
cd gif-compressor
./gifshrink in.gif
python3 gifshrink in.gif
```

## Examples

```
# No options: rebuilds the palette with 128 colors
gifshrink in.gif

# Narrower and fewer colors
gifshrink in.gif --width 320 --colors 64

# Half the frames, same duration
gifshrink in.gif --every 2

# A frame rate instead
gifshrink in.gif --fps 10

# No dithering (flat colors, good for logos and screen recordings)
gifshrink in.gif --colors 128 --dither none

# Fit under 2 MB, replacing an old result
gifshrink in.gif --target-size 2M -o small.gif --overwrite

# Make it play once
gifshrink in.gif --once
```

Example output with `--target-size` (the `testsrc2` test GIF from the table below):

```
try  1/15 as requested   320 px  keep fps  256 colors  ->  264.1 KB  too big
try  2/15 fps 12         320 px  12 fps  256 colors  ->  212.2 KB  too big
try  3/15 width 272      272 px  12 fps  256 colors  ->  245.3 KB  too big
try  4/15 128 colors     272 px  12 fps  128 colors  ->  220.5 KB  too big
try  5/15 fps 10         272 px  10 fps  128 colors  ->  185.1 KB  too big
try  6/15 width 224      224 px  10 fps  128 colors  ->  143.1 KB  too big
try  7/15 64 colors      224 px  10 fps   64 colors  ->  123.3 KB  too big
try  8/15 fps 8          224 px  8 fps   64 colors  ->  102.2 KB  too big
try  9/15 width 176      176 px  8 fps   64 colors  ->  73.9 KB  fits
chose: 176 px wide, 8 fps, 64 colors (reduced to width 176)
wrote ex.gif
            before                   after
size        264.1 KB (270460 bytes)  73.9 KB (75635 bytes)  -72%
dimensions  320 x 180                176 x 100
frames      45                       24
duration    3.00 s                   3.01 s
loop        loops forever            loops forever
```

If nothing fits you get this, and no file is written:

```
gifshrink: error: could not fit 1.0 KB. The smallest try was 14.6 KB (96 px wide, 5 fps, 16 colors). No file was written. Ask for a larger --target-size, or cut the GIF shorter first.
```

### All options

| Option | Meaning |
| --- | --- |
| `-o, --output FILE` | Output file. Default is the input name with `.small.gif`. |
| `--overwrite` | Replace the output file if it exists. |
| `--colors N` | Palette size, 2 to 256. With no shrink option at all the default is 128, otherwise 256. |
| `--width PX` | Scale down to this width. Height follows. A width above the GIF's own is ignored. |
| `--fps N` | Drop frames to this rate. A rate above the GIF's own is ignored. |
| `--every N` | Keep every Nth frame. Cannot be used with `--fps`. |
| `--dither` | `sierra2_4a` (default), `bayer`, `floyd_steinberg` or `none`. |
| `--bayer-scale 0-5` | Pattern size, only with `--dither bayer`. Default 2. |
| `--stats-mode` | `full` (default) or `diff`. Diff builds the palette from the moving parts. |
| `--target-size SIZE` | Size to fit, such as `2M` or `500K`. Sizes use 1024 per K. |
| `--loop N` | Extra repeats. 0 means forever. Default: keep the input's loop count. |
| `--once` | Play once and stop. |

Exit codes: 0 on success, 1 for an error (missing file, not a GIF, output exists, no ffmpeg, ffmpeg failed), 2 for bad options, 3 when `--target-size` cannot be met.

How `--target-size` steps: the first try uses your settings as they are. After that it takes one step at a time from three levers in turn (fewer frames per second, a smaller width, fewer colors), each step on top of the last, and stops at the first file that fits. So you get the gentlest settings on its list that meet the size, and no single setting is pushed to its limit before the others move. Smaller settings are not always a smaller file (see the table), so a step can come out bigger than the one before it. The fps steps are 20, 15, 12, 10, 8, 6 and 5. The widths are 85%, 70%, 55%, 40% and 30% of the start, down to 64 px. The colors are 128, 64, 32 and 16. If the GIF is already under the target, it is copied unchanged.

## Real sizes

These numbers are measured, not estimated. Three synthetic 3 second GIFs, 320x180, 15 fps, 45 frames, made with ffmpeg 8.0.1 and a palette built from their own frames, which is how a well-made GIF is usually exported:

```
ffmpeg -f lavfi -i "testsrc2=size=320x180:rate=15:duration=3" -filter_complex "[0:v]split[a][b];[a]palettegen[p];[b][p]paletteuse" -loop 0 testsrc2.gif

SWIRL="nullsrc=size=320x180:rate=15:duration=3,format=rgb24,geq=r='110+60*sin(X/70+T*0.5)':g='110+60*sin(Y/50-T*0.4)':b='130+60*sin((X+Y)/90+T*0.6)'"
ffmpeg -f lavfi -i "$SWIRL" -filter_complex "[0:v]split[a][b];[a]palettegen[p];[b][p]paletteuse" -loop 0 smooth.gif
ffmpeg -f lavfi -i "$SWIRL,noise=alls=14:allf=t:all_seed=1" -filter_complex "[0:v]split[a][b];[a]palettegen[p];[b][p]paletteuse" -loop 0 grain.gif
```

`testsrc2` is a busy test pattern with sharp text and many colors. `smooth` is a slow color swirl, the hard case for GIF because every pixel changes a little in every frame. `grain` is the same swirl with film grain added. Sizes are in KB (1024 bytes). **SSIM** compares the new GIF with the original at 320x180 and 15 fps: 1.000 is identical, lower is worse. It is shown for `testsrc2` and `smooth` only, because on `grain` it mostly measures the grain, which is different in every frame.

| Command | testsrc2 | smooth | grain |
| --- | --- | --- | --- |
| original GIF | 264.1 KB | 1191.4 KB | 1942.3 KB |
| `gifshrink in.gif` (128 colors) | 249.2 KB, -6% (SSIM 0.985) | 1212.6 KB, +2% (SSIM 0.828) | 1622.3 KB, -16% |
| `--colors 64` | 216.2 KB, -18% (SSIM 0.968) | 1064.2 KB, -11% (SSIM 0.701) | 1318.8 KB, -32% |
| `--colors 128 --dither none` | 223.0 KB, -16% (SSIM 0.987) | 992.2 KB, -17% (SSIM 0.915) | 1581.3 KB, -19% |
| `--width 240` | 249.7 KB, -5% (SSIM 0.862) | 769.6 KB, -35% (SSIM 0.762) | 1052.3 KB, -46% |
| `--fps 8` | 143.4 KB, -46% (SSIM 0.939) | 658.5 KB, -45% (SSIM 0.827) | 1036.3 KB, -47% |
| `--every 2` | 137.7 KB, -48% (SSIM 0.936) | 632.9 KB, -47% (SSIM 0.819) | 991.9 KB, -49% |
| `--width 240 --fps 10 --colors 64` | 132.9 KB, -50% (SSIM 0.808) | 389.1 KB, -67% (SSIM 0.623) | 464.3 KB, -76% |
| `--target-size 250K` | 212.2 KB, -20% (320 px, 12 fps) | 179.9 KB, -85% (176 px, 8 fps, 64 colors) | 202.6 KB, -90% (176 px, 8 fps, 64 colors) |
| `--target-size 100K` | 73.9 KB, -72% (176 px, 8 fps, 64 colors) | 60.6 KB, -95% (128 px, 6 fps, 32 colors) | 64.5 KB, -97% (128 px, 6 fps, 32 colors) |

The SSIM for the narrower GIFs is measured after stretching them back to 320 px, so it counts the lost sharpness as well. Every output had the same loop count as its input, and a duration of 3.00 s (3.01 s at 8 fps, from rounding the frame delays to hundredths of a second).

What to read from this:

- **Dropping frames is the reliable saving.** `--fps 8` and `--every 2` took 45% to 49% off all three GIFs, whatever the picture.
- **Fewer colors can help a lot or not at all.** 64 colors took 32% off the grainy GIF and 18% off the busy one. On `smooth`, plain 128 colors made the file 2% bigger, and the tool warned that it was not smaller. The swirl needs many colors to look smooth, so with fewer, the dither probably adds more detail, not less. `--dither none` on the same GIF took 17% off, but it drops the dither that hides banding.
- **A narrower GIF is not always much smaller.** On `testsrc2`, 240 px took only 5% off. Scaling softens the sharp edges, and the dither needed to draw soft edges may be what costs the bytes. On the two swirls the same step took 35% and 46%.
- **`--target-size` picks the settings for you.** For `testsrc2` at 250K it only needed 12 fps. For the swirls it needed a smaller width, fewer frames and fewer colors together.
- **Grain is expensive.** The same swirl with grain is 63% bigger (1191.4 KB to 1942.3 KB).
- Your GIFs will behave differently. Run yours with `--target-size` and look at the result.

## Why is my GIF so big?

A GIF stores every frame as its own small picture, with at most 256 colors each. There is no motion prediction like in MP4. So the size is roughly:

> pixels per frame  x  number of frames  x  how hard the picture is to store

1. **Width and height.** Halving the width cuts the pixels in every frame to a quarter. This is usually the biggest lever, but see the table: on sharp text and lines, scaling can add dither that eats part of the gain.
2. **Frame count.** A 15 fps GIF holds 15 pictures a second. Going to 8 fps holds about half as many, and the clip still plays for the same time.
3. **Length.** Every extra second adds every frame again. Cutting the clip shorter beats every other setting. This tool does not trim, so cut first.
4. **Noise and grain.** Film grain, a camera's sensor noise and fine gradients change a little in every frame, so every frame has to be stored in full. In the test GIFs below, the same swirl is 1191.4 KB clean and 1942.3 KB with grain added.
5. **Dither.** To fake colors it does not have, a GIF mixes dots of nearby colors. Dots are detail, and detail costs bytes. `--dither none` leaves flat colors and can be smaller, but smooth gradients show steps.
6. **Colors.** Fewer colors help a little, mostly on pictures that were using all 256.
7. **A generic palette.** Some tools write a GIF with a fixed palette instead of one built from the frames. Re-making the palette from the real frames is what `gifshrink` does first.

If the place you post to takes MP4 or WebM, use that instead. On the three test GIFs, a plain `ffmpeg -i in.gif -pix_fmt yuv420p -c:v libx264 -crf 23 out.mp4` gave 63.0 KB (from 264.1 KB), 78.9 KB (from 1191.4 KB) and 266.9 KB (from 1942.3 KB).

## FAQ

**Does it need gifsicle?**
No, only ffmpeg. There is no lossy gifsicle mode. The size comes from the palette, the frames and the width, and `--target-size` picks them for you.

**Will it look worse?**
A smaller file has less to show. A narrower GIF looks softer, fewer frames look less smooth, and fewer colors can show steps in gradients. Change one setting at a time and look at the result, or use `--target-size` and let it take the smallest steps that fit.

**Does the GIF keep its speed when frames are dropped?**
Yes. Each kept frame is shown for longer, so the total duration is the same. With `--every N` the last frame is lengthened a little so the total does not shrink either.

**What is the difference between `--fps` and `--every`?**
`--fps 8` sets a frame rate and picks the nearest frames to hit it. `--every 3` keeps frames 1, 4, 7 and so on, exactly. For a GIF at a steady frame rate they give nearly the same result.

**Does it keep the loop count?**
Yes. A GIF that loops forever still loops forever, one that repeats 3 times still repeats 3 times, and one with no loop block still plays once. Pass `--loop N` or `--once` to change it.

**What if `--target-size` cannot be met?**
It exits with code 3, prints the smallest size it reached, and writes nothing. An old output file with the same name is left alone.

**Can the result be bigger than the original?**
Yes, if the GIF was already well made. The tool tells you when the new file is not smaller. Use `--width`, `--fps` or `--target-size` to make it smaller.

**Does it keep transparency?**
Yes. A transparent background stays transparent. The test suite checks it on a small GIF with a transparent background and an opaque square. GIF has no partial transparency, so a pixel is either clear or solid.

**Does it keep the sound?**
GIFs have no sound.

**What does `--stats-mode diff` do?**
It builds the palette from the parts of the picture that move. It can help a small moving subject on a still background. For full-frame video the default is usually better.

**Will it upscale?**
No. It never goes above the GIF's own width or frame rate.

**It says ffmpeg is not found.**
Install ffmpeg (see Install) and open a new terminal so the PATH updates. Check with `ffmpeg -version`.

**Which Python?**
3.8 or newer. There are no third-party packages.

## Prefer a browser?

If you do not want to install anything, there is [a free browser version](https://getziztu.com/tools/gif-compressor?utm_source=github&utm_medium=readme&utm_campaign=gif-compressor). It is free, needs no account, takes GIFs up to 50 MB and allows 10 GIFs an hour. You pick a width, a frame rate and a number of colors.

## Development

```
python3 -m unittest discover -s tests -v
```

The tests build small GIFs with ffmpeg and check that the output is smaller, that `--target-size` really fits, that the loop count is kept and overridden, and the error paths. They need ffmpeg on your PATH and take about 25 seconds.

## License

MIT. Copyright (c) 2026 Ziztu.
