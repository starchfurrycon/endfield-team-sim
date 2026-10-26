# 图标资产（icon_raw.jpg）

这个图标是**生图 AI 出的新美术资产**，不是从游戏里抠的、也不依赖运行时抓取。

- 生成方式：Pollinations（`model=turbo`），2026-10-26
- 提示词：
  > super chibi big head sticker, cute girl, short white hair, huge blue eyes,
  > white construction helmet with a small yellow triangle on the front,
  > thick clean outline, flat anime colors, centered head, plain light gray
  > background, no text
- 种子：`202`（`helmet_2.jpg`）
- 后处理：`tools/make_icon.py` —— 去浅灰背景、圆形裁切、纸底色圆盘、1px 墨环，
  输出 512 PNG 与多尺寸 ICO。原图右下角带生成服务水印，裁切时已排除。

想换风格/换人：重新生成一张 768×768 的 Q 版大头（浅色纯背景、头部居中），
覆盖 `icon_raw.jpg` 后运行 `python tools/make_icon.py` 即可。

注意：生图模型的输出许可与商用条款请以所用服务为准；本仓库仅把它当作
粉丝工具的图标使用。
