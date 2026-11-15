"""Étapes 4-5 : sous-titres mot à mot, plans (zoom/pan ou clip animé) et montage final."""
import json
import random
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"

# Style des sous-titres (calé sur la vidéo d'exemple)
CAP_SIZE = 100
CAP_BOX_H = 600                    # hauteur de la bande transparente des sous-titres
CAP_CENTER_Y = int(H * 0.47)       # centre vertical des sous-titres à l'écran
ACCENT = (124, 58, 237, 240)       # violet du mot actif
STROKE = (17, 17, 17, 255)


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def media_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out)


# ---------- Découpage temporel ----------
def scene_timings(scenes, words, audio_dur):
    """Associe chaque scène à ses mots et calcule (début, fin) de chaque plan."""
    counts = [len(s["narration"].split()) for s in scenes]
    if sum(counts) != len(words):  # sécurité : répartition proportionnelle
        scale = len(words) / sum(counts)
        counts = [max(1, round(c * scale)) for c in counts]
        counts[-1] = max(1, len(words) - sum(counts[:-1]))
    firsts, idx = [], 0
    for c in counts:
        firsts.append(min(idx, len(words) - 1))
        idx += c
    starts = [0.0] + [words[f]["start"] for f in firsts[1:]]
    ends = starts[1:] + [audio_dur + 0.4]
    ranges = [(f, f + c) for f, c in zip(firsts, counts)]
    return [(round(s, 3), round(e, 3)) for s, e in zip(starts, ends)], ranges


# ---------- Sous-titres ----------
def _clean(word):
    return re.sub(r"[^\w'’\-%$]", "", word).upper()


def caption_chunks(words, scene_ranges, per_chunk=2):
    """Groupe les mots par 2 sans traverser une fin de phrase ni un changement de plan."""
    boundaries = {r[0] for r in scene_ranges}
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur and (len(cur) == per_chunk or i in boundaries):
            chunks.append(cur)
            cur = []
        if _clean(w["word"]):
            cur.append(i)
        if re.search(r"[.!?;:]$", w["word"]) and cur:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def render_caption(words_txt, active, font):
    img = Image.new("RGBA", (W, CAP_BOX_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    line_h = int(CAP_SIZE * 1.12)
    top = CAP_BOX_H // 2 - line_h * len(words_txt) // 2 + line_h // 2
    for k, txt in enumerate(words_txt):
        cy = top + k * line_h
        if k == active:
            x0, y0, x1, y1 = d.textbbox((W // 2, cy), txt, font=font, anchor="mm")
            d.rounded_rectangle((x0 - 24, y0 - 14, x1 + 24, y1 + 14), radius=22, fill=ACCENT)
        d.text((W // 2, cy), txt, font=font, anchor="mm", fill="white",
               stroke_width=4 if k != active else 2, stroke_fill=STROKE)
    return img


def build_captions(words, scene_ranges, total_dur, out_dir):
    """Crée une image par mot + un fichier concat ffmpeg avec les durées."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONT), CAP_SIZE)
    blank = out_dir / "blank.png"
    Image.new("RGBA", (W, CAP_BOX_H), (0, 0, 0, 0)).save(blank)

    events = []  # (start, png)
    chunks = caption_chunks(words, scene_ranges)
    for c, chunk in enumerate(chunks):
        txts = [_clean(words[i]["word"]) for i in chunk]
        for k, i in enumerate(chunk):
            png = out_dir / f"c{c:03d}_{k}.png"
            render_caption(txts, k, font).save(png)
            events.append((words[i]["start"], png))
        # pause dans la voix -> on efface après le dernier mot du groupe
        clear_at = words[chunk[-1]]["end"] + 0.35
        next_start = words[chunks[c + 1][0]]["start"] if c + 1 < len(chunks) else total_dur
        if clear_at < next_start:
            events.append((clear_at, blank))
    timeline = [(0.0, blank)] + events
    lines = ["ffconcat version 1.0"]
    for n, (start, png) in enumerate(timeline):
        nxt = timeline[n + 1][0] if n + 1 < len(timeline) else total_dur
        dur = nxt - start
        if dur <= 0.001:
            continue
        lines += [f"file '{png.name}'", f"duration {dur:.3f}"]
    lines.append(f"file '{blank.name}'")
    concat = out_dir / "captions.ffconcat"
    concat.write_text("\n".join(lines) + "\n")
    return concat


# ---------- Plans ----------
MOTIONS = ["zoom_in", "zoom_out", "pan_up", "pan_down", "zoom_in", "pan_left", "pan_right"]


def still_clip(img_path, dur, motion, out):
    """Zoom/pan fluide (sous-pixel) sur une image fixe, rendu via Pillow -> ffmpeg."""
    src = Image.open(img_path).convert("RGB")
    sw, sh = src.size
    # cadre 9:16 maximal dans l'image
    if sw / sh > W / H:
        bw, bh = sh * W / H, sh
    else:
        bw, bh = sw, sw * H / W
    n = max(1, round(dur * FPS))
    z0, z1, dx, dy = 1.0, 1.0, 0.0, 0.0
    if motion == "zoom_in":
        z0, z1 = 1.0, 1.12
    elif motion == "zoom_out":
        z0, z1 = 1.12, 1.0
    else:
        z0 = z1 = 1.12
        amp = 0.045
        dx = {"pan_left": amp, "pan_right": -amp}.get(motion, 0.0)
        dy = {"pan_up": amp, "pan_down": -amp}.get(motion, 0.0)

    proc = subprocess.Popen([
        "ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    for f in range(n):
        p = f / max(1, n - 1)
        z = z0 + (z1 - z0) * p
        cw, ch = bw / z, bh / z
        cx = sw / 2 + (p - 0.5) * 2 * dx * sw
        cy = sh / 2 + (p - 0.5) * 2 * dy * sh
        cx = min(max(cx, cw / 2), sw - cw / 2)
        cy = min(max(cy, ch / 2), sh - ch / 2)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        proc.stdin.write(src.resize((W, H), Image.BICUBIC, box=box).tobytes())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError(f"ffmpeg a échoué pour {img_path}")


def animated_clip(video_path, dur, out):
    """Clip IA recadré en 9:16 ; légèrement ralenti puis figé s'il est trop court."""
    src_dur = media_duration(video_path)
    slow = min(1.5, max(1.0, dur / src_dur))
    run([
        "ffmpeg", "-y", "-i", str(video_path), "-an", "-vf",
        f"setpts={slow:.4f}*PTS,scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={FPS},tpad=stop_mode=clone:stop_duration={dur:.3f}",
        "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(out)])


# ---------- Montage ----------
def assemble(scenes, images, animated, voice, words, out_dir, music_dir=None, music_volume=0.1):
    out_dir = Path(out_dir)
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    audio_dur = media_duration(voice)
    timings, ranges = scene_timings(scenes, words, audio_dur)
    total = timings[-1][1]

    rnd = random.Random(str(out_dir))
    clip_list = []
    for i, (s, e) in enumerate(timings):
        out = clips_dir / f"clip_{i:02d}.mp4"
        if not out.exists():
            if i in animated:
                animated_clip(animated[i], e - s, out)
            else:
                still_clip(images[i], e - s, rnd.choice(MOTIONS), out)
        clip_list.append(out)

    concat = clips_dir / "clips.txt"
    concat.write_text("".join(f"file '{c.name}'\n" for c in clip_list))
    video = out_dir / "video_noaudio.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(video)])

    captions = build_captions(words, ranges, total, out_dir / "captions")

    music = None
    if music_dir and music_volume > 0:
        tracks = sorted(Path(music_dir).glob("*.mp3"))
        if tracks:
            music = rnd.choice(tracks)

    final = out_dir / "final.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(video), "-i", str(voice),
           "-f", "concat", "-safe", "0", "-i", str(captions)]
    fc = (f"[2:v]fps={FPS},format=rgba[cap];"
          f"[0:v][cap]overlay=0:{CAP_CENTER_Y - CAP_BOX_H // 2}:eof_action=pass,format=yuv420p[v];")
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fc += (f"[3:a]volume={music_volume},afade=t=out:st={max(0, total - 2):.2f}:d=2[m];"
               f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    else:
        fc += "[1:a]anull[a]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "5M", "-bufsize", "10M",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(final)]
    run(cmd)
    (out_dir / "timings.json").write_text(json.dumps({"scenes": timings, "total": total}, indent=1))
    return final
