"""生成宣传片（无声 MP4）：截图 + 局部推拉 + 极短字幕，避免文字堆砌。

用法：
    python tools/make_promo.py [--out docs/media/suixinpei-promo.mp4]

输入：docs/media/src/{reco,cons,compare,llm}.png（1440x900 截图）
输出：1280x720 / 24fps / H.264 MP4
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
FPS = 24
SRC = Path('docs/media/src')
OUT = Path('docs/media/suixinpei-promo.mp4')

FONT_B = 'C:/Windows/Fonts/msyhbd.ttc'
FONT_R = 'C:/Windows/Fonts/msyh.ttc'
FONT_M = 'C:/Windows/Fonts/consola.ttf'

INK = (16, 17, 16)
PAPER = (244, 244, 240)
YELLOW = (255, 245, 0)
WHITE = (245, 245, 240)
MUTED = (150, 154, 150)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def fit(img: Image.Image) -> Image.Image:
    """cover 到 1280x720。"""
    img = img.convert('RGB')
    scale = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * scale), int(img.height * scale)),
                     Image.LANCZOS)
    x = (img.width - W) // 2
    y = (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def paper_card() -> Image.Image:
    img = Image.new('RGB', (1440, 900), PAPER)
    d = ImageDraw.Draw(img)
    for y in range(0, 900, 7):
        for x in range(0, 1440, 7):
            d.point((x, y), fill=(214, 216, 210))
    return img


def caption(img: Image.Image, kicker: str, text: str) -> Image.Image:
    img = img.convert('RGB')
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(H - 190, H):
        a = int(210 * (y - (H - 190)) / 190)
        d.rectangle([0, y, W, y + 1], fill=(5, 6, 6, a))
    d.text((64, H - 132), kicker, font=font(FONT_M, 17), fill=YELLOW)
    d.text((64, H - 100), text, font=font(FONT_B, 40), fill=WHITE)
    return Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')


def title_card() -> Image.Image:
    img = paper_card()
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1440, 14], fill=INK)
    d.rectangle([92, 300, 92 + 430, 300 + 130], fill=YELLOW, outline=INK, width=3)
    d.text((116, 316), '随心配', font=font(FONT_B, 82), fill=INK)
    d.text((96, 470), '配队 · 输出轴 · 自更新', font=font(FONT_B, 34), fill=INK)
    d.text((98, 540), 'SUIXINPEI / SQUAD SIMULATOR',
           font=font(FONT_M, 18), fill=(110, 114, 110))
    return img


def adv_card() -> Image.Image:
    img = Image.new('RGB', (1440, 900), (8, 9, 9))
    d = ImageDraw.Draw(img)
    d.text((96, 210), '帧级轴', font=font(FONT_B, 96), fill=YELLOW)
    d.text((96, 340), '换人', font=font(FONT_B, 96), fill=YELLOW)
    d.text((96, 470), '自更新', font=font(FONT_B, 96), fill=YELLOW)
    d.text((96, 640), '别人给循环表，我们给可执行轴。',
           font=font(FONT_R, 30), fill=WHITE)
    return img


def end_card() -> Image.Image:
    img = paper_card()
    d = ImageDraw.Draw(img)
    d.rectangle([92, 250, 92 + 430, 250 + 130], fill=YELLOW, outline=INK, width=3)
    d.text((116, 266), '随心配', font=font(FONT_B, 82), fill=INK)
    d.text((96, 420), '免费 · 本地运行 · 自带 API',
           font=font(FONT_R, 30), fill=INK)
    d.text((96, 500), 'github.com/starchfurrycon/suixinpei',
           font=font(FONT_M, 22), fill=(90, 94, 90))
    return img


def zoom_frames(img: Image.Image, dur: float, focus: tuple[int, int, int, int],
                z0: float, z1: float, kicker: str, text: str):
    fps = FPS
    n = max(1, int(dur * fps))
    x0, y0, x1, y1 = focus
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    frames = []
    for i in range(n):
        t = i / max(1, n - 1)
        z = z0 + (z1 - z0) * t
        cw, ch = W / z, H / z
        cx_i = cx + (x0 + x1) / 2 * 0  # keep center
        left = max(0, min(img.width - cw, cx_i - cw / 2))
        top = max(0, min(img.height - ch, cy - ch / 2))
        crop = img.crop((int(left), int(top), int(left + cw), int(top + ch)))
        frame = fit(crop)
        frames.append(caption(frame, kicker, text))
    return frames


def card_frames(card: Image.Image, dur: float, text: str = '') -> list[Image.Image]:
    n = max(1, int(dur * FPS))
    base = fit(card)
    out = []
    for _ in range(n):
        out.append(caption(base, '', text) if text else base)
    return out


def blend(a: Image.Image, b: Image.Image, k: float) -> Image.Image:
    return Image.blend(a, b, k)


def assemble(scenes: list[list[Image.Image]], fade: int = 10) -> list[Image.Image]:
    out: list[Image.Image] = []
    for idx, frames in enumerate(scenes):
        if idx == 0:
            out.extend(frames)
            continue
        prev = out[-fade:]
        out = out[:-fade]
        nxt = frames[:fade]
        for k in range(fade):
            out.append(blend(prev[k], nxt[k], (k + 1) / (fade + 1)))
        out.extend(frames[fade:])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(OUT))
    args = ap.parse_args()

    reco = Image.open(SRC / 'reco.png')
    cons = Image.open(SRC / 'cons.png')
    cmp_ = Image.open(SRC / 'compare.png')
    llm = Image.open(SRC / 'llm_checks.png')
    loadout = Image.open(SRC / 'loadout.png')

    scenes = [
        card_frames(title_card(), 3.2),
        zoom_frames(reco, 8.0, (0, 0, 1440, 900), 1.02, 1.08,
                    '01 / 推荐', '不选人，先给全局最优'),
        zoom_frames(cons, 7.5, (280, 60, 1240, 560), 1.05, 1.12,
                    '02 / 约束', '只设必选与排除'),
        zoom_frames(cons, 11.0, (280, 230, 1240, 640), 1.12, 1.18,
                    '03 / 时间轴', '主控 · 换人 · 可取消'),
        zoom_frames(cons, 7.0, (280, 610, 1240, 870), 1.12, 1.2,
                    '04 / 执行', '每一步：时间 · 技能 · 条件'),
        zoom_frames(cmp_, 8.0, (220, 20, 1240, 830), 1.0, 1.05,
                    '05 / 对比', '同队两套配置，直接看差值'),
        zoom_frames(loadout, 6.5, (280, 60, 1240, 620), 1.05, 1.12,
                    '06 / 装备', '每人 3 把武器，按轴配'),
        zoom_frames(llm, 8.0, (350, 40, 1110, 470), 1.04, 1.1,
                    '07 / 自适应', '自检 8 项：证据 · 数值 · 提示词'),
        card_frames(adv_card(), 7.0),
        card_frames(end_card(), 6.0),
    ]

    frames = assemble(scenes)
    tmp = Path(tempfile.mkdtemp(prefix='promo-'))
    try:
        for i, f in enumerate(frames):
            f.save(tmp / f'{i:05d}.jpg', quality=90)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            'ffmpeg', '-y', '-loglevel', 'error',
            '-framerate', str(FPS), '-i', str(tmp / '%05d.jpg'),
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20',
            '-movflags', '+faststart', str(out),
        ], check=True)
        print(f'video: {out} ({out.stat().st_size / 1024 / 1024:.1f} MB, '
              f'{len(frames) / FPS:.1f}s)')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
