@echo off
setlocal

REM Use `python -m pip` / `python -m PyInstaller` everywhere so the build
REM uses the SAME Python interpreter that `python` resolves to on this
REM machine.  Otherwise — on machines with multiple Python installs —
REM `pip` and `pyinstaller` can resolve to a different Python whose
REM site-packages doesn't have segno (or any other dep), and PyInstaller
REM silently drops the missing module from the bundle without warning.
REM Symptom: frozen .exe runs but throws "No module named 'X'" at runtime
REM the first time the missing import fires.  v0.10.0 and v0.10.0.1 first
REM build both shipped without segno because of this exact env mismatch.
set "PY=python"

echo [1/3] Installing dependencies...
%PY% -m pip install -r requirements.txt --quiet
%PY% -m pip install pyinstaller --quiet
echo Python    : %PY%
%PY% -c "import sys; print('Python ver:', sys.version.split()[0])"
%PY% -c "import segno; print('segno ver :', segno.__version__, '(', segno.__file__, ')')"

echo [1.5/3] Clearing previous build artefacts...
if exist build\OblivionServerTool rmdir /s /q build\OblivionServerTool
if exist dist\OblivionServerTool.exe del /f /q dist\OblivionServerTool.exe

echo [2/3] Building executable...
%PY% -m PyInstaller ^
  --onefile ^
  --windowed ^
  --noconfirm ^
  --name "OblivionServerTool" ^
  --icon "emblem.ico" ^
  --collect-all flask ^
  --collect-all jinja2 ^
  --collect-all webview ^
  --collect-all segno ^
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
  --hidden-import cs2servergui.veto ^
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
