"""生成应用图标：埃特拉 Q 版大头 + 终末地官网风格（纸底/墨黑/信号黄/全直角）。

用法：python tools/make_icon.py [--avatar data/avatars/chr_0021_whiten.png]
输出：desktop/assets/icon.png（512）、icon.ico（16/24/32/48/64/128/256）
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

PAPER = (232, 232, 226, 255)      # #e8e8e2
INK = (16, 17, 16, 255)           # #101110
YELLOW = (255, 245, 0, 255)       # #fff500
LINE = (182, 184, 179, 255)       # #b6b8b3


def hazard(size: tuple[int, int], gap: int = 26) -> Image.Image:
    """黄黑斜纹条。"""
    w, h = size
    img = Image.new("RGBA", size, YELLOW)
    d = ImageDraw.Draw(img)
    for x in range(-h, w + h, gap):
        d.polygon([(x, h), (x + gap // 2, h), (x + gap // 2 + h, 0), (x + h, 0)],
                  fill=INK)
    return img


def build(avatar: Path, out_dir: Path, size: int = 512) -> Image.Image:
    s = size
    icon = Image.new("RGBA", (s, s), PAPER)
    d = ImageDraw.Draw(icon)

    # 外框：2px 墨线，内缩 18px
    m = 18
    d.rectangle([m, m, s - m, s - m], outline=INK, width=3)

    # 头像：Q 版大头，圆形裁切，1px 墨线
    head_size = int(s * 0.70)
    head = Image.open(avatar).convert("RGBA").resize((head_size, head_size),
                                                     Image.LANCZOS)
    # 圆形遮罩
    mask = Image.new("L", (head_size, head_size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, head_size - 1, head_size - 1], fill=255)
    head.putalpha(mask)
    hx = (s - head_size) // 2
    hy = int(s * 0.085)
    # 头像底：信号黄方块（只露边，作为"信号"）
    d.rectangle([hx - 6, hy - 6, hx + head_size + 5, hy + head_size + 5],
                fill=YELLOW, outline=INK, width=3)
    icon.alpha_composite(head, (hx, hy))
    d.ellipse([hx, hy, hx + head_size - 1, hy + head_size - 1],
              outline=INK, width=3)

    # 底部黄黑斜纹 + 名称
    band_h = int(s * 0.165)
    band_y = s - m - band_h
    band = hazard((s - 2 * m, band_h), gap=max(18, s // 18))
    icon.alpha_composite(band, (m, band_y))
    d.line([m, band_y, s - m, band_y], fill=INK, width=3)

    # 名称（墨黑压黄底，放在斜纹上方的一小段纯黄上）
    d.rectangle([m + 3, band_y + 3, m + int(s * 0.48), band_y + band_h - 3],
                fill=YELLOW, outline=INK, width=2)
    try:
        from PIL import ImageFont
        font = None
        for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf"):
            try:
                font = ImageFont.truetype(name, int(band_h * 0.62))
                break
            except OSError:
                continue
        if font:
            d.text((m + 12, band_y + band_h // 2), "随心配", font=font,
                   fill=INK, anchor="lm")
    except Exception:  # noqa: BLE001
        pass

    out_dir.mkdir(parents=True, exist_ok=True)
    icon.save(out_dir / "icon.png")
    ico = icon.resize((256, 256), Image.LANCZOS)
    ico.save(out_dir / "icon.ico",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                    (128, 128), (256, 256)])
    return icon


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--avatar", default="data/avatars/chr_0021_whiten.png")
    ap.add_argument("--out", default="desktop/assets")
    args = ap.parse_args()
    icon = build(Path(args.avatar), Path(args.out))
    print("icon written:", Path(args.out) / "icon.png", icon.size)


if __name__ == "__main__":
    main()
