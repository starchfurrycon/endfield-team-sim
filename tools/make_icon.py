"""生成「随心配」图标：内置 Q 版大头美术资产（不依赖运行时抓取）。

输入：desktop/assets/icon_raw.jpg —— 生图 AI 出的一张 Q 版大头（见 ICON.md）
处理：去背景 → 圆形裁切 → 纸底色圆盘 + 1px 墨环
输出：desktop/assets/icon.png（512）、icon.ico（16~256）

用法：python tools/make_icon.py [--src desktop/assets/icon_raw.jpg]
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

SIZE = 512
PAPER = (226, 230, 230, 255)
INK = (16, 17, 16, 255)


def cutout(img: Image.Image) -> Image.Image:
    """从四角洪水填充去掉浅灰背景。"""
    im = img.convert('RGB')
    w, h = im.size
    px = im.load()
    mask = Image.new('L', (w, h), 0)
    mpx = mask.load()
    seeds = [(3, 3), (w - 4, 3), (3, h - 4), (w - 4, h - 4), (w // 2, 3)]
    base = px[3, 3]
    tol = 26
    seen = bytearray(w * h)
    dq = deque(seeds)
    while dq:
        x, y = dq.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or seen[y * w + x]:
            continue
        r, g, b = px[x, y]
        if abs(r - base[0]) + abs(g - base[1]) + abs(b - base[2]) > tol * 3:
            continue
        seen[y * w + x] = 1
        mpx[x, y] = 255
        dq.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    fg = ImageOps.invert(mask).filter(ImageFilter.GaussianBlur(1.2))
    out = img.convert('RGBA')
    out.putalpha(fg)
    bbox = fg.getbbox()
    return out.crop(bbox) if bbox else out


def build(src: Path, out_dir: Path) -> Image.Image:
    # 裁掉原图右下角的生成服务水印
    raw = Image.open(src)
    w, h = raw.size
    head = cutout(raw.crop((int(w * .05), int(h * .025), int(w * .94), int(h * .91))))

    target = int(SIZE * 0.92)
    scale = min(target / head.width, target / head.height)
    head = head.resize((int(head.width * scale), int(head.height * scale)),
                       Image.LANCZOS)

    icon = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.ellipse([2, 2, SIZE - 3, SIZE - 3], fill=PAPER)
    icon.alpha_composite(head, ((SIZE - head.width) // 2,
                                (SIZE - head.height) // 2))
    d.ellipse([2, 2, SIZE - 3, SIZE - 3], outline=INK, width=4)

    out_dir.mkdir(parents=True, exist_ok=True)
    icon.save(out_dir / 'icon.png')
    icon.resize((256, 256), Image.LANCZOS).save(
        out_dir / 'icon.ico',
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
               (128, 128), (256, 256)])
    return icon


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='desktop/assets/icon_raw.jpg')
    ap.add_argument('--out', default='desktop/assets')
    args = ap.parse_args()
    icon = build(Path(args.src), Path(args.out))
    print('icon written:', Path(args.out) / 'icon.png', icon.size)


if __name__ == '__main__':
    main()
