"""生成「随心配」图标：埃特拉 Q 版大头，圆形裁切，无文字、无几何拼贴。

用法：
    python tools/make_icon.py                       # 默认 data/avatars/chr_0021_whiten.png
    python tools/make_icon.py --avatar <path>       # 换任意干员头像

输出：desktop/assets/icon.png（512，透明圆）、icon.ico（16~256）
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 512


def build(avatar: Path, out_dir: Path) -> Image.Image:
    im = Image.open(avatar).convert('RGBA')

    # 方形取头部（头像本身是 Q 版大头，居中略偏上）
    w, h = im.size
    side = min(w, h)
    im = im.crop(((w - side) // 2, 0, (w + side) // 2, side))

    # 放大 + 轻锐化（官方 120px → 512）
    im = im.resize((SIZE, SIZE), Image.LANCZOS)
    im = im.filter(ImageFilter.UnsharpMask(radius=1.6, percent=120, threshold=2))

    # 圆形遮罩 + 细墨线外环
    mask = Image.new('L', (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).ellipse([2, 2, SIZE - 3, SIZE - 3], fill=255)
    icon = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(im, (0, 0), mask)
    ImageDraw.Draw(icon).ellipse([2, 2, SIZE - 3, SIZE - 3],
                                 outline=(16, 17, 16, 255), width=4)

    out_dir.mkdir(parents=True, exist_ok=True)
    icon.save(out_dir / 'icon.png')
    icon.resize((256, 256), Image.LANCZOS).save(
        out_dir / 'icon.ico',
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
               (128, 128), (256, 256)])
    return icon


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--avatar', default='data/avatars/chr_0021_whiten.png')
    ap.add_argument('--out', default='desktop/assets')
    args = ap.parse_args()
    icon = build(Path(args.avatar), Path(args.out))
    print('icon written:', Path(args.out) / 'icon.png', icon.size)


if __name__ == '__main__':
    main()
