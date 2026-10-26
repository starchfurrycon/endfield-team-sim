"""生成「随心配」图标：埃特拉 Q 版大头（内置资产，圆形，无文字）。

输入：desktop/assets/icon_raw.png —— 游戏内官方 Q 版头像（120px，见 ICON.md）
处理：居中裁切 → 放大 + 轻锐化 → 纸底圆盘 + 1px 墨环
输出：desktop/assets/icon.png（512）、icon.ico（16~256）

用法：python tools/make_icon.py [--src desktop/assets/icon_raw.png]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 512
PAPER = (238, 240, 236, 255)
INK = (16, 17, 16, 255)


def build(src: Path, out_dir: Path) -> Image.Image:
    im = Image.open(src).convert('RGBA')
    side = min(im.size)
    im = im.crop((0, 0, side, side))

    inner = int(SIZE * 0.90)
    im = im.resize((inner, inner), Image.LANCZOS)
    im = im.filter(ImageFilter.UnsharpMask(radius=2.0, percent=150,
                                           threshold=1))

    disc = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(disc)
    d.ellipse([2, 2, SIZE - 3, SIZE - 3], fill=PAPER)

    head = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    head.paste(im, ((SIZE - inner) // 2, (SIZE - inner) // 2), im)
    mask = Image.new('L', (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).ellipse([2, 2, SIZE - 3, SIZE - 3], fill=255)
    disc.paste(head, (0, 0), mask)
    ImageDraw.Draw(disc).ellipse([2, 2, SIZE - 3, SIZE - 3],
                                 outline=INK, width=5)

    out_dir.mkdir(parents=True, exist_ok=True)
    disc.save(out_dir / 'icon.png')
    disc.resize((256, 256), Image.LANCZOS).save(
        out_dir / 'icon.ico',
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
               (128, 128), (256, 256)])
    return disc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='desktop/assets/icon_raw.png')
    ap.add_argument('--out', default='desktop/assets')
    args = ap.parse_args()
    icon = build(Path(args.src), Path(args.out))
    print('icon written:', Path(args.out) / 'icon.png', icon.size)


if __name__ == '__main__':
    main()
