"""真实操作录屏 + 短字幕 + 原创"哈基米风"BGM（无配音）。

前置：python run.py serve --port 8797 --no-browser（数据已构建）
产物：docs/media/suixinpei-demo.mp4
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

URL = 'http://127.0.0.1:8797/?shell=1'
PORT = 8899
OUT = Path('docs/media/suixinpei-demo.mp4')
W, H = 1280, 720
FPS = 12
FONT_B = 'C:/Windows/Fonts/msyhbd.ttc'
FONT_M = 'C:/Windows/Fonts/consola.ttf'
INK = (16, 17, 16)
PAPER = (244, 244, 240)
YELLOW = (255, 245, 0)

# name, caption, kind(0=card, 1=record)
SCENES = [
    ('title', '', 0),
    ('reco', '不选人，先看全局最优', 1),
    ('constraints', '必选洛茜 · 排除狼卫', 1),
    ('axis', '主控 / 换人 / 可取消', 1),
    ('guide', '每一步：时间 · 技能 · 条件', 1),
    ('compare', '同队两套配置，看差值', 1),
    ('loadout', '装备 / 武器，按人推荐', 1),
    ('llm', '接自己的 API，自检有证据', 1),
    ('diversity', 'Top3 会换核心', 1),
    ('end', '', 0),
]


async def wait_loaded(page):
    await page.wait_for_function(
        "() => document.querySelectorAll('#result .card .axis-scroll').length > 0",
        timeout=30000)
    await page.wait_for_timeout(500)


async def capture(page, seconds: float = 2.4) -> list[bytes]:
    frames = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        frames.append(await page.screenshot(type='jpeg', quality=82))
        await asyncio.sleep(0.08)
    return frames


async def record(browser) -> dict:
    async def op(page, name):
        await page.locator('.op', has_text=name).first.click()

    async def constraints(page):
        await op(page, '洛茜')
        await page.wait_for_timeout(250)
        await op(page, '狼卫')
        await page.wait_for_timeout(250)
        await op(page, '狼卫')
        await page.wait_for_timeout(900)
        await page.wait_for_function(
            "() => { const h=document.querySelector('#hero-meta');"
            "return !h || !h.textContent.includes('计算中'); }",
            timeout=30000)
        await wait_loaded(page)
        await page.wait_for_timeout(500)

    async def one(setup, seconds=2.4):
        print('scene start')
        ctx = await browser.new_context(viewport={'width': 1440, 'height': 900})
        page = await ctx.new_page()
        await page.goto(URL)
        await page.wait_for_selector('#result .card', timeout=30000)
        await wait_loaded(page)
        await setup(page)
        print('scene capture')
        frames = await capture(page, seconds)
        await ctx.close()
        return frames

    async def s_reco(page):
        await page.locator('#result .card').first.hover()

    async def s_constraints(page):
        await constraints(page)

    async def s_axis(page):
        await constraints(page)
        await page.locator('#main').evaluate('(e)=>e.scrollTop=0')

    async def s_guide(page):
        await constraints(page)
        await page.locator('.guide').first.scroll_into_view_if_needed()

    async def s_compare(page):
        await constraints(page)
        await page.locator('#main').evaluate('(e)=>e.scrollTop=0')
        await page.locator('#result .card').first.locator('button', has_text='对比').click()
        await page.wait_for_selector('#cmp.open', timeout=10000)
        await page.click('#cmp-run')
        await page.wait_for_timeout(1600)

    async def s_loadout(page):
        await constraints(page)
        await page.locator('#result .card').first.locator('button', has_text='装备').click()
        await page.locator('#main').evaluate('(e)=>e.scrollTop=e.scrollHeight')
        await page.wait_for_timeout(900)

    async def s_llm(page):
        await page.click('#llm-btn')
        await page.wait_for_timeout(600)
        await page.click('#llm-selftest')
        await page.wait_for_timeout(1800)

    async def s_diversity(page):
        await page.locator('#main').evaluate('(e)=>e.scrollTop=560')

    return {
        'reco': await one(s_reco),
        'constraints': await one(s_constraints),
        'axis': await one(s_axis),
        'guide': await one(s_guide, 2.2),
        'compare': await one(s_compare, 2.6),
        'loadout': await one(s_loadout),
        'llm': await one(s_llm, 2.6),
        'diversity': await one(s_diversity, 2.6),
    }


# ------------------------- 画面 -------------------------
def fit(img: Image.Image) -> Image.Image:
    img = img.convert('RGB')
    s = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
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


def title_card() -> Image.Image:
    img = paper_card()
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1440, 14], fill=INK)
    d.rectangle([92, 300, 522, 430], fill=YELLOW, outline=INK, width=3)
    d.text((116, 316), '随心配', font=ImageFont.truetype(FONT_B, 82), fill=INK)
    d.text((96, 470), '配队 · 输出轴 · 可执行', font=ImageFont.truetype(FONT_B, 34), fill=INK)
    d.text((98, 540), 'SUIXINPEI / SQUAD SIMULATOR',
           font=ImageFont.truetype(FONT_M, 18), fill=(110, 114, 110))
    return img


def end_card() -> Image.Image:
    img = paper_card()
    d = ImageDraw.Draw(img)
    d.rectangle([92, 250, 522, 380], fill=YELLOW, outline=INK, width=3)
    d.text((116, 266), '随心配', font=ImageFont.truetype(FONT_B, 82), fill=INK)
    d.text((96, 420), '免费 · 本地运行 · 接自己的 API',
           font=ImageFont.truetype(FONT_B, 28), fill=INK)
    d.text((96, 500), 'github.com/starchfurrycon/suixinpei',
           font=ImageFont.truetype(FONT_M, 22), fill=(90, 94, 90))
    return img


def caption(img: Image.Image, text: str) -> Image.Image:
    if not text:
        return img
    img = img.convert('RGB')
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(H - 150, H):
        a = int(205 * (y - (H - 150)) / 150)
        d.rectangle([0, y, W, y + 1], fill=(5, 6, 6, a))
    f = ImageFont.truetype(FONT_B, 30)
    tw = d.textlength(text, font=f)
    d.text(((W - tw) / 2, H - 64), text, font=f, fill=(248, 248, 242))
    return Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')


# ------------------------- 合成 -------------------------
def main() -> None:
    work = Path(tempfile.mkdtemp(prefix='demo-'))
    try:
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n).decode('utf-8'))
                text, slot = '', 'battle_skill'
                for m in body.get('messages', []):
                    if m.get('role') == 'user':
                        try:
                            p = json.loads(m.get('content') or '{}')
                            text = p.get('text') or ''
                            slot = p.get('slot') or slot
                        except Exception:
                            pass
                out = {'slot': slot, 'trigger': 'on_status',
                       'trigger_params': {'hp_below_pct': None, 'requires_status': None,
                                          'window_s': None},
                       'element': '物理', 'abnormal': None, 'target': 'enemy',
                       'role': 'damage', 'duration_s': None, 'is_dot': False,
                       'needs_enemy_hit': False, 'gives_sp': None, 'gives_ult': None,
                       'heals': False, 'shields': False, 'tags': [],
                       'evidence': [text[:8]] if text else [],
                       'confidence': 0.9, 'method': 'llm', 'notes': []}
                raw = json.dumps({'choices': [{'message': {'content': json.dumps(out, ensure_ascii=False)}}],
                                  'usage': {'prompt_tokens': 100, 'completion_tokens': 40}},
                                 ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        Path('data/llm.json').write_text(json.dumps({
            'enabled': True, 'base': f'http://127.0.0.1:{PORT}/v1',
            'model': 'stub-model', 'key': 'local-dummy-key',
            'temperature': 0, 'max_tokens': 2048, 'timeout_s': 30,
            'price_in': 0, 'price_out': 0}, ensure_ascii=False), encoding='utf-8')

        async def run():
            async with async_playwright() as pw:
                b = await pw.chromium.launch(channel='msedge', headless=True)
                data = await record(b)
                await b.close()
                return data

        data = asyncio.run(run())
        durs = {name: (3.0 if kind == 0 else 2.8)
                for name, _cap, kind in SCENES}

        frames_dir = work / 'frames'
        frames_dir.mkdir()
        idx = 0
        for name, cap, kind in SCENES:
            dur = durs[name]
            n = int(dur * FPS)
            if name == 'title':
                src = [fit(title_card())]
            elif name == 'end':
                src = [fit(end_card())]
            else:
                src = [fit(Image.open(__import__('io').BytesIO(d)))
                       for d in data[name]]
            for k in range(n):
                img = src[min(len(src) - 1, int(k / max(1, n - 1) * (len(src) - 1)))]
                img = caption(img, cap)
                img.save(frames_dir / f'{idx:05d}.jpg', quality=88)
                idx += 1

        total = sum(durs.values())
        OUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(FPS),
                        '-i', str(frames_dir / '%05d.jpg'), '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', '-crf', '20', str(OUT)], check=True)
        print('video', OUT, round(OUT.stat().st_size / 1048576, 1), 'MB', round(total, 1), 's')
    finally:
        Path('data/llm.json').unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
