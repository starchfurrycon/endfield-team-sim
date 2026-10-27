"""组装 Windows 便携版桌面应用（Electron 壳 + 随包 server）。

产物：release/随心配-3.0.0-win64/
  随心配.exe            ← 改名后的 electron.exe（rcedit 写图标/版本信息）
  resources/app/             ← main.js / index.html / package.json / assets
  resources/server/          ← endfield-server.exe
  README.txt / LICENSE.txt
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path('.').resolve()
DESKTOP = ROOT / 'desktop'
ELECTRON = DESKTOP / 'node_modules' / 'electron' / 'dist'
RCEDIT = DESKTOP / 'node_modules' / 'rcedit' / 'bin' / 'rcedit-x64.exe'
APP_VERSION = '3.7.0'
OUT = ROOT / 'release' / f'随心配-{APP_VERSION}-win64'

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

# 1) Electron 运行时
print('[pkg] copy electron runtime…')
shutil.copytree(ELECTRON, OUT, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('default_app.asar', '*.pdb'))

# 2) 应用资源
app_dir = OUT / 'resources' / 'app'
app_dir.mkdir(parents=True, exist_ok=True)
for name in ('main.js', 'index.html', 'package.json'):
    shutil.copy2(DESKTOP / name, app_dir / name)
assets = app_dir / 'assets'
assets.mkdir(exist_ok=True)
for name in ('icon.png', 'icon.ico'):
    shutil.copy2(DESKTOP / 'assets' / name, assets / name)
print('[pkg] app resources ->', app_dir)

# 3) 随包后端
srv_dir = OUT / 'resources' / 'server'
srv_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / 'dist' / 'endfield-server.exe', srv_dir / 'endfield-server.exe')
print('[pkg] server ->', srv_dir / 'endfield-server.exe')

# 4) 瘦身：只保留中英文 locale
locales = OUT / 'locales'
keep = {'zh-CN.pak', 'en-US.pak', 'en-GB.pak'}
if locales.exists():
    for f in locales.iterdir():
        if f.name not in keep:
            f.unlink()
print('[pkg] locales:', sorted(p.name for p in locales.iterdir()) if locales.exists() else [])

# 5) 改名 + 写图标/版本
exe = OUT / '随心配.exe'
(OUT / 'electron.exe').rename(exe)
if RCEDIT.exists():
    subprocess.run([
        str(RCEDIT), str(exe),
        '--set-icon', str(DESKTOP / 'assets' / 'icon.ico'),
        '--set-file-version', APP_VERSION,
        '--set-product-version', f'{APP_VERSION}.0',
        '--set-version-string', 'FileDescription', '随心配',
        '--set-version-string', 'ProductName', '随心配',
        '--set-version-string', 'CompanyName', 'starchfurrycon',
        '--set-version-string', 'LegalCopyright', 'MIT / 非官方粉丝作品',
        '--set-version-string', 'OriginalFilename', '随心配.exe',
    ], check=True)
    print('[pkg] rcedit ok')
else:
    print('[pkg] rcedit missing, skip icon/version', file=sys.stderr)

# 6) 说明文件
shutil.copy2(ROOT / 'LICENSE', OUT / 'LICENSE.txt')
readme_src = ROOT / 'release' / 'DESKTOP-README.txt'
if readme_src.exists():
    shutil.copy2(readme_src, OUT / 'README.txt')
print('[pkg] assembled ->', OUT)

size = sum(f.stat().st_size for f in OUT.rglob('*') if f.is_file())
print(f'[pkg] files={sum(1 for f in OUT.rglob("*") if f.is_file())} '
      f'size={size / 1024 / 1024:.1f} MB')
