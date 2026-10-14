@echo off
rem ============================================================
rem  Svet -- game launcher. Just double-click this file.
rem  Extra keys are passed through, e.g.:  run_game.bat --seed 42
rem
rem  NOTE: this file is deliberately ASCII-only. Cyrillic text in a
rem  .bat is read with the console codepage, which differs between a
rem  double-click and a terminal, so it garbles or even breaks parsing.
rem  The game's own interface is Russian.
rem ============================================================

cd /d "%~dp0"

rem On this machine `python` in PATH is the Microsoft Store stub,
rem so try `py` first.
set "PY=py"
where py >nul 2>nul || set "PY=python"

rem First run: install the dependencies if they are missing.
%PY% -c "import pygame, numpy" >nul 2>nul
if errorlevel 1 (
    echo First run: installing pygame-ce and numpy...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)

echo Starting Svet... Press Esc in the game to quit.
%PY% main.py %*
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo Could not start the game.
echo Make sure Python 3.11+ is installed and that there is internet
echo access to install the dependencies:
echo     py -m pip install -r requirements.txt
echo.
pause
exit /b 1
