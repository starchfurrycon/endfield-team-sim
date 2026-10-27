"""真实操作录屏 + 中文解说 + 原创合成 BGM（哈基米风）。

前置：python run.py serve --port 8797 --no-browser（数据已构建）
产物：docs/media/suixinpei-demo.mp4
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import shutil
import subprocess
import tempfile
import threading
import time
import wave
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

SCENES = [
    ('title', '这是随心配，一个帧级的终末地配队工具。', 0),
    ('reco', '先不选人，直接看全局推荐。', 1),
    ('constraints', '只设约束：必选洛茜，排除狼卫。', 1),
    ('axis', '轴是帧级的：主控轨、换人、可取消窗口。', 1),
    ('guide', '下面直接告诉你每一步：什么时候战技、连携、终结技。', 1),
    ('compare', '同一队两套配置，直接看差多少。', 1),
    ('loadout', '装备和武器，按人给三把、两套。', 1),
    ('llm', '接自己的 API，自检和复核都有证据。', 1),
    ('diversity', '不设约束时，Top3 会换核心，不是同一套换一个人。', 1),
    ('end', '免费、本地运行，GitHub 搜随心配。', 0),
]


# ------------------------- 解说 -------------------------
def tts(text: str, path: Path) -> float:
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.SelectVoice('Microsoft Huihui Desktop');$s.Rate=1;"
        f"$s.SetOutputToWaveFile('{path}');$s.Speak('{text}');$s.Dispose()"
    )
    subprocess.run(['powershell', '-NoProfile', '-Command', script],
                   check=True, capture_output=True)
    with wave.open(str(path), 'rb') as w:
        return w.getnframes() / w.getframerate()


# ------------------------- 截图捕获 -------------------------
class Grabber:
    def __init__(self, page):
        self.page = page
        self.frames: list[tuple[float, bytes]] = []
        self.marks: list[tuple[str, float]] = []
        self.running = True

    async def run(self):
        while self.running:
            try:
                data = await self.page.screenshot(type='jpeg', quality=82)
                self.frames.append((time.monotonic(), data))
            except Exception:
                pass
            await asyncio.sleep(0.07)

    def mark(self, name: str):
        self.marks.append((name, time.monotonic()))


# ------------------------- 录音流程 -------------------------
async def record(page, work: Path) -> dict:
    g = Grabber(page)
    task = asyncio.create_task(g.run())
    async def wait_loaded():
        await page.wait_for_function(
            "() => document.querySelectorAll('#result .card .axis-scroll').length > 0",
            timeout=30000)
        await page.wait_for_timeout(600)

    await page.goto(URL)
    await page.wait_for_selector('#result .card', timeout=30000)
    await wait_loaded()

    async def op(name: str):
        await page.locator('.op', has_text=name).first.click()

    async def scene(name: str, fn):
        g.mark(name + ':start')
        try:
            await fn()
        except Exception as e:  # noqa: BLE001
            print('scene failed', name, e)
        g.mark(name + ':end')

    async def s_reco():
        await page.mouse.move(700, 520)
        await page.wait_for_timeout(600)
        await page.locator('#result .card').first.hover()
        await page.wait_for_timeout(2200)

    async def s_constraints():
        await op('洛茜')          # 必选
        await page.wait_for_timeout(400)
        await op('狼卫')          # 必选 -> 排除
        await page.wait_for_timeout(400)
        await op('狼卫')          # 排除
        await page.wait_for_timeout(400)
        await wait_loaded()
        await page.wait_for_timeout(2200)

    async def s_axis():
        await page.locator('#main').evaluate('(e)=>e.scrollTop=0')
        await page.locator('#result .card').first.locator('button', has_text='热').click()
        await page.wait_for_timeout(400)
        await wait_loaded()
        await page.wait_for_timeout(1200)
        await page.locator('#result .card').first.hover()
        await page.wait_for_timeout(1200)

    async def s_guide():
        await page.locator('.guide').first.scroll_into_view_if_needed()
        await page.wait_for_timeout(2200)

    async def s_compare():
        await page.locator('#main').evaluate('(e)=>e.scrollTop=0')
        await page.locator('#result .card').first.locator('button', has_text='对比').click()
        await page.wait_for_selector('#cmp.open', timeout=10000)
        await page.click('#cmp-run')
        await page.wait_for_timeout(3200)
        await page.click('#cmp-close')

    async def s_loadout():
        await page.locator('#result .card').first.locator('button', has_text='装备').click()
        await page.locator('#main').evaluate('(e)=>e.scrollTop=e.scrollHeight')
        await page.wait_for_timeout(2800)

    async def s_llm():
        await page.locator('#main').evaluate('(e)=>e.scrollTop=0')
        await page.click('#llm-btn')
        await page.wait_for_timeout(900)
        await page.click('#llm-selftest')
        await page.wait_for_timeout(3000)
        await page.keyboard.press('Escape')
        await page.evaluate("document.querySelector('#llm').classList.remove('open')")

    async def s_diversity():
        await page.evaluate("document.querySelectorAll('.loadout-box').forEach(e=>e.remove())")
        await page.locator('#main').evaluate('(e)=>e.scrollTop=0')
        await page.evaluate("document.querySelector('#llm').classList.remove('open')")
        await page.evaluate("document.querySelector('#clr').click()")
        await page.wait_for_function(
            "() => document.querySelectorAll('.cchip').length === 0", timeout=10000)
        await wait_loaded()
        await page.locator('#main').evaluate('(e)=>e.scrollTop=560')
        await page.wait_for_timeout(1800)

    for name, _txt, kind in SCENES:
        if kind == 0:
            continue
        fn = {'reco': s_reco, 'constraints': s_constraints, 'axis': s_axis,
              'guide': s_guide, 'compare': s_compare, 'loadout': s_loadout,
              'llm': s_llm, 'diversity': s_diversity}[name]
        await scene(name, fn)

    g.running = False
    await task
    return {'frames': g.frames, 'marks': g.marks}


# ------------------------- 静态卡片 -------------------------
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
    f = ImageFont.truetype(FONT_B, 82)
    d.text((116, 316), '随心配', font=f, fill=INK)
    d.text((96, 470), '配队 · 输出轴 · 自更新', font=ImageFont.truetype(FONT_B, 34), fill=INK)
    d.text((98, 540), 'SUIXINPEI / SQUAD SIMULATOR',
           font=ImageFont.truetype(FONT_M, 18), fill=(110, 114, 110))
    return img


def end_card() -> Image.Image:
    img = paper_card()
    d = ImageDraw.Draw(img)
    d.rectangle([92, 250, 522, 380], fill=YELLOW, outline=INK, width=3)
    d.text((116, 266), '随心配', font=ImageFont.truetype(FONT_B, 82), fill=INK)
    d.text((96, 420), '免费 · 本地运行 · 自带 API', font=ImageFont.truetype(FONT_B, 30), fill=INK)
    d.text((96, 500), 'github.com/starchfurrycon/suixinpei',
           font=ImageFont.truetype(FONT_M, 22), fill=(90, 94, 90))
    return img


LABELS = {'title': '开场', 'reco': '推荐', 'constraints': '约束',
          'axis': '时间轴', 'guide': '怎么打', 'compare': '对比',
          'loadout': '装备', 'llm': '自检', 'diversity': '多样', 'end': '下载'}


def chapter(img: Image.Image, text: str) -> Image.Image:
    img = img.convert('RGB')
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT_B, 15)
    d.rectangle([24, 22, 24 + 8 * len(text) + 24, 48], fill=(5, 6, 6))
    d.text((36, 27), text, font=f, fill=YELLOW)
    return img


# ------------------------- 音乐 -------------------------
def music(path: Path, seconds: float = 8.0) -> None:
    sr = 44100
    n = int(sr * seconds)
    buf = [0.0] * n
    melody = [523.25, 659.25, 783.99, 1046.5, 783.99, 659.25,
              587.33, 698.46, 880.0, 1174.66, 880.0, 698.46]
    step = 0.2
    for i, hz in enumerate(melody * 4):
        start = int(i * step * sr)
        ln = int(step * sr * 0.9)
        for k in range(ln):
            if start + k >= n:
                break
            t = k / sr
            env = math.exp(-5 * t) * min(1, k / 200)
            v = (1 if math.sin(2 * math.pi * hz * t) > 0 else -1) * 0.16
            buf[start + k] += v * env
    for beat in range(int(seconds / 0.4)):
        start = int(beat * 0.4 * sr)
        for k in range(int(0.05 * sr)):
            if start + k >= n:
                break
            buf[start + k] += (random.random() * 2 - 1) * 0.05 * math.exp(-40 * k / sr)
        if beat % 4 == 0:
            for k in range(int(0.12 * sr)):
                if start + k >= n:
                    break
                t = k / sr
                buf[start + k] += math.sin(2 * math.pi * 70 * t) * 0.18 * math.exp(-18 * t)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b''.join(
            int(max(-1, min(1, x)) * 32767).to_bytes(2, 'little', signed=True)
            for x in buf))


# ------------------------- 合成 -------------------------
def read_wav(path: Path) -> tuple[bytes, int, int, int]:
    with wave.open(str(path), 'rb') as w:
        return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels(), w.getsampwidth()


def silence(seconds: float, sr: int, ch: int, sw: int) -> bytes:
    return b'\x00' * int(seconds * sr) * ch * sw


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix='demo-'))
    stub = None
    try:
        # 本地 stub，供 LLM 自检演示（不联网）
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
        stub = threading.Thread(target=srv.serve_forever, daemon=True)
        stub.start()
        Path('data/llm.json').write_text(json.dumps({
            'enabled': True, 'base': f'http://127.0.0.1:{PORT}/v1',
            'model': 'stub-model', 'key': 'sk-stub-000000000000',
            'temperature': 0, 'max_tokens': 2048, 'timeout_s': 30,
            'price_in': 0, 'price_out': 0}, ensure_ascii=False), encoding='utf-8')

        # TTS
        narr = {}
        for name, text, _kind in SCENES:
            p = work / f'{name}.wav'
            narr[name] = (p, tts(text, p))
            print('tts', name, round(narr[name][1], 2))

        # 录屏
        async def run():
            async with async_playwright() as pw:
                b = await pw.chromium.launch(channel='msedge', headless=True)
                page = await b.new_page(viewport={'width': 1440, 'height': 900})
                data = await record(page, work)
                await b.close()
                return data

        data = asyncio.run(run())
        frames, marks = data['frames'], data['marks']
        print('frames', len(frames), 'marks', len(marks))
        paths = [Path(p) for p, _ in marks]

        # 每个场景的截图
        scene_frames = {}
        for i, (name, _t, kind) in enumerate(SCENES):
            if kind == 0:
                scene_frames[name] = []
                continue
            a = paths.index(Path(name + ':start'))
            b = paths.index(Path(name + ':end'))
            t0 = marks[a][1]
            t1 = marks[b][1]
            sel = [d for ts, d in frames if t0 <= ts <= t1]
            if not sel:
                sel = [d for ts, d in frames if ts >= t0][:1] or [frames[-1][1]]
            scene_frames[name] = sel
            print(name, 'captured', len(sel), 'dur', round(t1 - t0, 2))

        # 场景时长 = max(录制, 解说 + 1.1)
        durs = {}
        for name, _t, kind in SCENES:
            rec = 0.0
            if kind == 1:
                a = paths.index(Path(name + ':start'))
                b = paths.index(Path(name + ':end'))
                rec = marks[b][1] - marks[a][1]
            durs[name] = max(3.0, rec, narr[name][1] + 1.1)

        # 视频帧（12fps）
        frames_dir = work / 'frames'
        frames_dir.mkdir()
        idx = 0
        for si, (name, _t, kind) in enumerate(SCENES, 1):
            dur = durs[name]
            n = int(dur * FPS)
            src = ([fit(title_card())] if name == 'title' else
                   [fit(end_card())] if name == 'end' else
                   [fit(Image.open(__import__('io').BytesIO(d))) for d in scene_frames[name]])
            for k in range(n):
                img = src[min(len(src) - 1, int(k / max(1, n - 1) * (len(src) - 1)))]
                img = chapter(img, f'{si:02d} / {LABELS.get(name, name)}')
                img.save(frames_dir / f'{idx:05d}.jpg', quality=88)
                idx += 1

        # 解说轨
        narration = work / 'narration.wav'
        with wave.open(str(narration), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(22050)
            for name, _t, _k in SCENES:
                txt = narr[name][0]
                raw, sr, ch, sw = read_wav(txt)
                if sr != 22050:
                    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(txt),
                                    '-ar', '22050', '-ac', '1', str(work / 'x.wav')], check=True)
                    raw, sr, ch, sw = read_wav(work / 'x.wav')
                out.writeframes(silence(0.35, 22050, 1, 2))
                out.writeframes(raw)
                pad = durs[name] - narr[name][1] - 0.35
                out.writeframes(silence(max(0.2, pad), 22050, 1, 2))

        # 音乐
        mus = work / 'music.wav'
        music(mus, 8.0)

        video = work / 'video.mp4'
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(FPS),
                        '-i', str(frames_dir / '%05d.jpg'), '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', '-crf', '20', str(video)], check=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        total = sum(durs.values())
        subprocess.run([
            'ffmpeg', '-y', '-loglevel', 'error', '-i', str(video),
            '-i', str(narration), '-stream_loop', '-1', '-i', str(mus),
            '-filter_complex',
            f'[2:a]volume=0.14,atrim=0:{total:.2f}[m];'
            f'[1:a][m]amix=inputs=2:duration=first:dropout_transition=0,'
            f'loudnorm=I=-16:TP=-1.5:LRA=11[a]',
            '-map', '0:v', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac',
            '-b:a', '128k', '-shortest', str(OUT)], check=True)
        print('video', OUT, round(OUT.stat().st_size / 1048576, 1), 'MB', round(total, 1), 's')
    finally:
        try:
            Path('data/llm.json').unlink(missing_ok=True)
        except Exception:
            pass
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
