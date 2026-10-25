@echo off
REM 启动桌面版。
REM 注意：若环境里设了 ELECTRON_RUN_AS_NODE，Electron 会退化成 node 而报
REM "Cannot read properties of undefined (reading 'whenReady')"，这里强制清掉。
set ELECTRON_RUN_AS_NODE=
cd /d "%~dp0"
if not exist node_modules\electron\dist\electron.exe (
  echo 首次运行需要安装 Electron...
  call npm install --no-audit --no-fund || goto :err
)
call node_modules\electron\dist\electron.exe .
goto :eof
:err
echo.
echo 安装失败。可尝试镜像：set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
pause
