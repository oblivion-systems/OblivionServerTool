@echo off
setlocal

echo [1/3] Installing dependencies...
pip install -r requirements.txt --quiet
pip install pyinstaller --quiet

echo [1.5/3] Clearing previous build artefacts...
if exist build\OblivionServerTool rmdir /s /q build\OblivionServerTool
if exist dist\OblivionServerTool.exe del /f /q dist\OblivionServerTool.exe

echo [2/3] Building executable...
pyinstaller ^
  --onefile ^
  --windowed ^
  --noconfirm ^
  --name "OblivionServerTool" ^
  --icon "emblem.ico" ^
  --collect-all flask ^
  --collect-all jinja2 ^
  --collect-all webview ^
  --hidden-import werkzeug ^
  --hidden-import werkzeug.serving ^
  --hidden-import werkzeug.routing ^
  --hidden-import werkzeug.exceptions ^
  --hidden-import keyring ^
  --hidden-import keyring.backends ^
  --hidden-import keyring.backends.Windows ^
  --hidden-import cs2servergui.config ^
  --hidden-import cs2servergui.rcon ^
  --hidden-import cs2servergui.core ^
  --hidden-import cs2servergui.web ^
  --hidden-import cs2servergui._netutils ^
  --add-data "emblem.ico;." ^
  --add-data "cs2servergui/plugins;cs2servergui/plugins" ^
  --add-data "cs2servergui/templates;cs2servergui/templates" ^
  --add-data "cs2servergui/static;cs2servergui/static" ^
  main.py

echo [3/3] Done.
echo.
echo  Executable : dist\OblivionServerTool.exe
echo.
echo  To build the installer:
echo    1. Install Inno Setup: https://jrsoftware.org/isinfo.php
echo    2. Run: ISCC installer.iss
echo    3. Output: dist\OblivionServerToolSetup-v*.exe
echo.
pause
