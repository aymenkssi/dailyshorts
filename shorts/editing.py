"""Sous-titres mot à mot, plans (zoom/pan) et montage final, en vertical (Short) ou horizontal (vidéo longue)."""
import json
import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FPS = 30
ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"


@dataclass(frozen=True)
class Layout:
    w: int
    h: int
    cap_size: int        # taille de police des sous-titres
    cap_box_h: int       # hauteur de la bande transparente des sous-titres
    cap_center_y: int    # centre vertical des sous-titres à l'écran
    per_chunk: int       # mots affichés ensemble
    stacked: bool        # True : un mot par ligne (vertical) ; False : une seule ligne (horizontal)
    maxrate: str


# Short vertical : 2 mots empilés au milieu de l'écran (calé sur la vidéo d'exemple)
SHORT = Layout(1080, 1920, 100, 600, int(1920 * 0.47), 2, True, "5M")
# Vidéo longue horizontale : une ligne de 4 mots en bas de l'écran
LONG = Layout(1920, 1080, 68, 180, int(1080 * 0.86), 4, False, "6M")

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


def render_caption(words_txt, active, font, L):
    img = Image.new("RGBA", (L.w, L.cap_box_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if L.stacked:  # un mot par ligne, centré
        line_h = int(L.cap_size * 1.12)
        top = L.cap_box_h // 2 - line_h * len(words_txt) // 2 + line_h // 2
        spots = [(L.w // 2, top + k * line_h) for k in range(len(words_txt))]
    else:          # une seule ligne, mots côte à côte
        space = int(L.cap_size * 0.42)
        widths = [d.textlength(t, font=font) for t in words_txt]
        x = (L.w - sum(widths) - space * (len(words_txt) - 1)) / 2
        spots = []
        for wd in widths:
            spots.append((x + wd / 2, L.cap_box_h // 2))
            x += wd + space
    pad_x, pad_y, radius = L.cap_size * 0.24, L.cap_size * 0.14, int(L.cap_size * 0.22)
    for k, (txt, (cx, cy)) in enumerate(zip(words_txt, spots)):
        if k == active:
            x0, y0, x1, y1 = d.textbbox((cx, cy), txt, font=font, anchor="mm")
            d.rounded_rectangle((x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y), radius=radius, fill=ACCENT)
        d.text((cx, cy), txt, font=font, anchor="mm", fill="white",
               stroke_width=4 if k != active else 2, stroke_fill=STROKE)
    return img


def build_captions(words, scene_ranges, total_dur, out_dir, L):
    """Crée une image par mot + un fichier concat ffmpeg avec les durées."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONT), L.cap_size)
    blank = out_dir / "blank.png"
    Image.new("RGBA", (L.w, L.cap_box_h), (0, 0, 0, 0)).save(blank)

    events = []  # (start, png)
    chunks = caption_chunks(words, scene_ranges, L.per_chunk)
    for c, chunk in enumerate(chunks):
        txts = [_clean(words[i]["word"]) for i in chunk]
        for k, i in enumerate(chunk):
            png = out_dir / f"c{c:03d}_{k}.png"
            render_caption(txts, k, font, L).save(png)
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
XFADE = 0.5      # durée du fondu enchaîné entre deux plans (s)
ZOOM = 1.12      # zoom maximal d'un plan
GRADE = "eq=contrast=1.04:saturation=1.08,vignette=angle=PI/5"   # étalonnage léger + vignettage


def prepare(img_path, L):
    """Image agrandie une seule fois (Lanczos + accentuation) pour que chaque image du plan soit une
    réduction : plus nette qu'un agrandissement refait à chaque image."""
    from PIL import ImageFilter
    src = Image.open(img_path).convert("RGB")
    sw, sh = src.size
    bw, bh = (sh * L.w / L.h, sh) if sw / sh > L.w / L.h else (sw, sw * L.h / L.w)  # cadre au format vidéo
    need = L.w * ZOOM / bw  # facteur pour que le cadre le plus serré couvre encore toute la largeur
    if need > 1.01:
        src = src.resize((round(sw * need), round(sh * need)), Image.LANCZOS)
        src = src.filter(ImageFilter.UnsharpMask(radius=1.6, percent=55, threshold=2))
    return src


def kb_frame(src, motion, p, L):
    """Une image du plan : zoom/pan sous-pixel, p = avancement de 0 à 1 (au-delà pendant les fondus)."""
    W, H = L.w, L.h
    p = min(max(p, -0.1), 1.1)
    sw, sh = src.size
    bw, bh = (sh * W / H, sh) if sw / sh > W / H else (sw, sw * H / W)
    z0, z1, dx, dy = 1.0, 1.0, 0.0, 0.0
    if motion == "zoom_in":
        z0, z1 = 1.0, ZOOM
    elif motion == "zoom_out":
        z0, z1 = ZOOM, 1.0
    else:
        z0 = z1 = ZOOM
        amp = 0.045
        dx = {"pan_left": amp, "pan_right": -amp}.get(motion, 0.0)
        dy = {"pan_up": amp, "pan_down": -amp}.get(motion, 0.0)
    z = max(1.0, z0 + (z1 - z0) * p)
    cw, ch = bw / z, bh / z
    cx = min(max(sw / 2 + (p - 0.5) * 2 * dx * sw, cw / 2), sw - cw / 2)
    cy = min(max(sh / 2 + (p - 0.5) * 2 * dy * sh, ch / 2), sh - ch / 2)
    return src.resize((W, H), Image.BICUBIC, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))


class Particles:
    """Poussière de lumière qui flotte lentement devant l'image (ambiance « film d'animation »)."""

    def __init__(self, L, seed, n=None):
        from PIL import ImageFilter
        rnd = random.Random(seed)
        self.W, self.H = L.w, L.h
        n = n or int(L.w * L.h / 60000)
        self.items = [(rnd.uniform(0, L.w), rnd.uniform(0, L.h), rnd.uniform(-6, 6), rnd.uniform(-22, -6),
                       rnd.choice((0, 0, 1, 1, 2)), rnd.uniform(0, 6.28), rnd.uniform(0.4, 1.0)) for _ in range(n)]
        self.sprites = []
        for size in (10, 16, 26):
            m = Image.new("L", (size, size), 0)
            ImageDraw.Draw(m).ellipse((size * .3, size * .3, size * .7, size * .7), fill=255)
            m = m.filter(ImageFilter.GaussianBlur(size / 7))
            levels = [m.point(lambda v, a=a: int(v * a)) for a in (0.25, 0.45, 0.65, 0.85)]
            self.sprites.append((Image.new("RGB", (size, size), (255, 236, 196)), levels))

    def draw(self, frame, t):
        import math
        for x0, y0, vx, vy, k, ph, amp in self.items:
            rgb, levels = self.sprites[k]
            x = (x0 + vx * t + 12 * math.sin(t * 0.5 + ph)) % self.W
            y = (y0 + vy * t) % self.H
            a = amp * (0.6 + 0.4 * math.sin(t * 1.3 + ph))
            frame.paste(rgb, (int(x), int(y)), levels[min(3, int(a * 4))])


def render_shots(images, timings, motions, out, L, particles=True, seed=0):
    """Tous les plans en un seul flux, avec un fondu enchaîné à chaque changement de plan."""
    total = timings[-1][1]
    n_frames = max(1, round(total * FPS))
    starts = [s for s, _ in timings]
    cache = {}

    def src(i):
        if i not in cache:
            for k in [k for k in cache if k < i - 1]:  # deux plans en mémoire au plus
                del cache[k]
            cache[i] = prepare(images[i], L)
        return cache[i]

    def shot_frame(i, t):
        s, e = timings[i]
        return kb_frame(src(i), motions[i], (t - s) / max(0.1, e - s), L)

    dust = Particles(L, seed) if particles else None
    proc = subprocess.Popen([
        "ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{L.w}x{L.h}",
        "-r", str(FPS), "-i", "-", "-vf", GRADE, "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
        "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    i = 0
    try:
        for f in range(n_frames):
            t = f / FPS
            while i + 1 < len(starts) and t >= starts[i + 1]:
                i += 1
            frame = shot_frame(i, t)
            # fondu centré sur la coupe : la fin du plan précédent ou le début du suivant
            if i > 0 and t - starts[i] < XFADE / 2:
                a = 0.5 + (t - starts[i]) / XFADE
                frame = Image.blend(shot_frame(i - 1, t), frame, a)
            elif i + 1 < len(starts) and starts[i + 1] - t < XFADE / 2:
                a = 0.5 - (starts[i + 1] - t) / XFADE
                frame = Image.blend(frame, shot_frame(i + 1, t), a)
            if dust:
                dust.draw(frame, t)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    if proc.wait():
        raise RuntimeError(f"ffmpeg a échoué pendant le rendu des plans : {proc.stderr.read().decode()[-300:]}")


def animated_clip(video_path, dur, out, L):
    """Clip animé recadré au format ; légèrement ralenti puis figé s'il est trop court."""
    W, H = L.w, L.h
    src_dur = media_duration(video_path)
    slow = min(1.5, max(1.0, dur / src_dur))
    run([
        "ffmpeg", "-y", "-i", str(video_path), "-an", "-vf",
        f"setpts={slow:.4f}*PTS,scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={FPS},tpad=stop_mode=clone:stop_duration={dur:.3f}",
        "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(out)])


# ---------- Montage ----------
def assemble(scenes, images, animated, voice, words, out_dir, music_dir=None, music_volume=0.1, layout=SHORT,
             particles=True):
    """animated : conservé pour compatibilité (plus utilisé) ; particles : poussière de lumière."""
    L = layout
    out_dir = Path(out_dir)
    audio_dur = media_duration(voice)
    timings, ranges = scene_timings(scenes, words, audio_dur)
    total = timings[-1][1]

    rnd = random.Random(str(out_dir))
    motions, last = [], None
    for _ in timings:  # jamais deux fois de suite le même mouvement
        m = rnd.choice([x for x in MOTIONS if x != last])
        motions.append(m)
        last = m
    video = out_dir / "video_noaudio.mp4"
    if not video.exists():
        tmp = out_dir / "video_noaudio.tmp.mp4"
        render_shots(images, timings, motions, tmp, L, particles=particles, seed=str(out_dir))
        tmp.replace(video)

    captions = build_captions(words, ranges, total, out_dir / "captions", L)

    music = None
    if music_dir and music_volume > 0:
        tracks = sorted(Path(music_dir).glob("*.mp3"))
        if tracks:
            music = rnd.choice(tracks)

    final = out_dir / "final.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(video), "-i", str(voice),
           "-f", "concat", "-safe", "0", "-i", str(captions)]
    fc = (f"[2:v]fps={FPS},format=rgba[cap];"
          f"[0:v][cap]overlay=0:{L.cap_center_y - L.cap_box_h // 2}:eof_action=pass,format=yuv420p[v];")
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fc += (f"[3:a]volume={music_volume},afade=t=out:st={max(0, total - 2):.2f}:d=2[m];"
               f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    else:
        fc += "[1:a]anull[a]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", L.maxrate, "-bufsize", "12M",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(final)]
    run(cmd)
    (out_dir / "timings.json").write_text(json.dumps({"scenes": timings, "total": total}, indent=1))
    return final
