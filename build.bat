@echo off
setlocal

echo [1/3] Installing dependencies...
pip install -r requirements.txt --quiet
pip install pyinstaller --quiet

echo [2/3] Building executable...
pyinstaller ^
  --onefile ^
  --windowed ^
  --name "OblivionServerTool" ^
  --collect-all customtkinter ^
  --collect-all flask ^
  --collect-all PIL ^
  --collect-all jinja2 ^
  --hidden-import werkzeug ^
  --hidden-import werkzeug.serving ^
  --hidden-import werkzeug.routing ^
  --hidden-import werkzeug.exceptions ^
  --hidden-import cs2servergui.config ^
  --hidden-import cs2servergui.rcon ^
  --hidden-import cs2servergui.core ^
  --hidden-import cs2servergui.web ^
  --hidden-import cs2servergui.gui ^
  main.py

echo [3/3] Done.
echo.
echo  Executable : dist\OblivionServerTool.exe
echo  Config file: oblivion_config.json  (created next to the .exe on first run)
echo.
echo  To share with others:
echo    1. Copy dist\OblivionServerTool.exe to any machine
echo    2. Run it — a setup dialog will ask for the CS2 server directory
echo    3. That's it. No installer needed.
echo.
pause
