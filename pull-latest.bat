@echo off
REM ==========================================================================
REM  pull-latest.bat - fetch the latest OblivionServerTool.exe release
REM
REM  v3: rewritten with `if X goto :label` style instead of multi-line
REM  `if X ( ... )` blocks.  cmd.exe's parser is notoriously fragile with
REM  multi-line if-blocks when their bodies contain colons (e.g. URLs like
REM  `https://...`), which caused the earlier "then was unexpected at
REM  this time" parser error.  Single-line if + explicit label jumps
REM  sidestep that entire class of bug.
REM
REM  ASCII-only.  Forced CRLF via .gitattributes (*.bat text eol=crlf).
REM ==========================================================================
setlocal
pushd "%~dp0"

set "REPO=oblivion-systems/OblivionServerTool"
set "EXE_NAME=OblivionServerTool.exe"
set "DEST_DIR=dist"
set "DEST=%DEST_DIR%\%EXE_NAME%"
set "BACKUP=%DEST%.bak"

echo.
echo === Oblivion Server Tool - pull latest release ===
echo Repo : %REPO%
echo Dest : %DEST%
echo CWD  : %CD%
echo.

REM --- 1. gh CLI present? -----------------------------------------------
where gh >nul 2>&1
if errorlevel 1 goto :no_gh

REM --- 2. gh authenticated? ---------------------------------------------
gh auth status >nul 2>&1
if errorlevel 1 goto :no_auth

REM --- 3. App not running? ----------------------------------------------
tasklist /FI "IMAGENAME eq %EXE_NAME%" 2>nul | findstr /I "%EXE_NAME%" >nul
if not errorlevel 1 goto :running

REM --- 4. Ensure dest dir exists ----------------------------------------
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%"

REM --- 5. Query latest release tag --------------------------------------
echo [i] Querying latest release...
set "TAG="
for /f "usebackq delims=" %%v in (`gh release view --repo %REPO% --json tagName -q ".tagName" 2^>nul`) do set "TAG=%%v"
if "%TAG%"=="" goto :no_release
echo [i] Latest release tag: %TAG%

REM --- 6. Back up existing exe ------------------------------------------
if not exist "%DEST%" goto :skip_backup
echo [i] Backing up existing exe to %BACKUP%
copy /Y "%DEST%" "%BACKUP%" >nul
if errorlevel 1 goto :backup_failed
:skip_backup

REM --- 7. Download via gh -----------------------------------------------
echo [i] Downloading %EXE_NAME% from release %TAG%...
gh release download %TAG% --repo %REPO% --pattern %EXE_NAME% --dir %DEST_DIR% --clobber
if errorlevel 1 goto :download_failed

REM --- 8. Report what we got --------------------------------------------
for %%I in ("%DEST%") do echo [+] Downloaded %%~zI bytes - %%~tI

REM --- 9. Offer to launch -----------------------------------------------
echo.
choice /M "Launch the new version now"
if errorlevel 2 goto :done
echo [i] Launching %DEST%...
start "" "%DEST%"
goto :done


REM ====================================================================
REM   Error handlers
REM ====================================================================

:no_gh
echo [X] gh CLI not found on PATH.
echo     Install: winget install --id GitHub.cli
goto :fail

:no_auth
echo [X] gh CLI not authenticated.
echo     Run: gh auth login
echo     Pick GitHub.com, HTTPS, web browser.
goto :fail

:running
echo [!] %EXE_NAME% is currently running.
echo     Close it from the system tray or app window first
echo     so it can save state cleanly, then re-run this script.
goto :fail

:no_release
echo [X] Could not read latest release.  Possible causes:
echo     - no releases published yet
echo     - your gh token lacks read access to %REPO%
goto :fail

:backup_failed
echo [X] Backup failed.  Aborting before overwriting %DEST%.
goto :fail

:download_failed
echo [X] Download failed.  If you backed up your old exe it is at:
echo         %BACKUP%
goto :fail


REM ====================================================================
REM   Exit paths
REM ====================================================================

:done
echo.
echo Done.  Previous version (if any) is at:
echo     %BACKUP%
echo.
popd
endlocal
pause
exit /b 0

:fail
echo.
popd
endlocal
pause
exit /b 1
