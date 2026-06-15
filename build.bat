@echo off
REM ============================================================================
REM  build.bat — produces dist\OblivionServerTool.exe via PyInstaller.
REM
REM  v0.11.10 changes: every run now captures full output to build_log.txt
REM  (rotating the previous to build_log.prev.txt), shows the last 50 lines
REM  of output in the console regardless of success/failure, prints a clear
REM  OK/FAIL banner, and always pauses so the window never closes silently.
REM
REM  Why this matters: silent close on early error = "what just happened?"
REM  vs the operator-friendly "BUILD FAILED — error was X, full log at Y".
REM ============================================================================
setlocal EnableExtensions EnableDelayedExpansion

set "LOG=build_log.txt"
set "PREV=build_log.prev.txt"

REM Rotate previous log so we never silently lose the last failed build's output.
if exist "%LOG%" (
  copy /Y "%LOG%" "%PREV%" > nul 2>&1
  del /Q "%LOG%" > nul 2>&1
)

REM ── Run the whole pipeline with all output going to LOG ──────────────────
call :do_build > "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM ── Show last 50 lines so the operator sees the important bits ───────────
echo.
echo === BUILD OUTPUT (last 50 lines from %LOG%) ===
powershell -NoProfile -Command "Get-Content -Path '%LOG%' -Tail 50" 2>nul
if errorlevel 1 (
  REM Fallback if PowerShell isn't available (won't happen on Windows >= 10).
  type "%LOG%"
)
echo.
echo =============================================================
if "%RC%"=="0" (
  echo  BUILD SUCCESS  ^|  v%cd:~-30%
  echo  exe: dist\OblivionServerTool.exe
  if exist dist\OblivionServerTool.exe (
    for %%I in (dist\OblivionServerTool.exe) do echo  size: %%~zI bytes ^| mtime: %%~tI
  )
) else (
  echo  BUILD FAILED  ^|  exit code %RC%
  echo  Read full log: %LOG%
)
echo  Full log saved: %LOG%
echo  Previous log:   %PREV% ^(if any^)
echo =============================================================
echo.
pause
exit /b %RC%

REM ============================================================================
REM  :do_build — actual build pipeline.  All output here goes to LOG via the
REM  redirect on the call above.
REM ============================================================================
:do_build
echo === Build started %DATE% %TIME% ===
echo CWD: %CD%
echo.

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
if errorlevel 1 ( echo ERROR: pip install requirements failed & exit /b 11 )
%PY% -m pip install pyinstaller --quiet
if errorlevel 1 ( echo ERROR: pip install pyinstaller failed & exit /b 12 )
echo Python    : %PY%
%PY% -c "import sys; print('Python ver:', sys.version.split()[0])"
%PY% -c "import segno; print('segno ver :', segno.__version__, '(', segno.__file__, ')')"
if errorlevel 1 ( echo ERROR: segno import test failed — wrong Python interpreter? & exit /b 13 )
%PY% -c "import discord; print('discord ver:', discord.__version__)" 2>nul
if errorlevel 1 ( echo WARN: discord.py not importable — Discord bot features will be inactive in the build )
echo.

echo [1.5/3] Clearing previous build artefacts...
if exist build\OblivionServerTool rmdir /s /q build\OblivionServerTool
if exist dist\OblivionServerTool.exe del /f /q dist\OblivionServerTool.exe
if exist dist\OblivionServerTool.exe.bak del /f /q dist\OblivionServerTool.exe.bak
echo.

REM v0.16.8 (review fix #2) — fetch the WebView2 Evergreen bootstrapper.
REM installer.iss conditionally bundles it via #if FileExists; without
REM this step the obvious "build.bat → ISCC" flow ships an installer that
REM skips WebView2 entirely, defeating v0.16.5's item A on clean Windows
REM 10 machines.  Idempotent — fetch_webview2.ps1 exits early if present.
echo [1.6/3] Fetching WebView2 bootstrapper for installer bundling...
powershell -ExecutionPolicy Bypass -NoProfile -File "tools\fetch_webview2.ps1"
if errorlevel 1 (
  echo WARN: WebView2 bootstrapper fetch failed - installer will skip the
  echo       WebView2 bundle.  Friends on Win10 may see a blank window.
  echo       Re-run "tools\fetch_webview2.ps1" manually or ship without it.
)
echo.

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
  --collect-all discord ^
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
  --hidden-import cs2servergui.discord_bot ^
  --add-data "emblem.ico;." ^
  --add-data "cs2servergui/plugins;cs2servergui/plugins" ^
  --add-data "cs2servergui/registry;cs2servergui/registry" ^
  --add-data "cs2servergui/templates;cs2servergui/templates" ^
  --add-data "cs2servergui/static;cs2servergui/static" ^
  main.py
if errorlevel 1 ( echo ERROR: PyInstaller failed & exit /b 20 )

echo.
echo [3/3] Verifying output...
if not exist dist\OblivionServerTool.exe (
  echo ERROR: dist\OblivionServerTool.exe missing after build
  exit /b 21
)
for %%I in (dist\OblivionServerTool.exe) do echo  output: %%~fI ^(%%~zI bytes^)

echo.
echo === Build finished %DATE% %TIME% ===
exit /b 0
