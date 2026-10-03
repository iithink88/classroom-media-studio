@echo off
title Classroom Media Studio - AIGC HomeTown
cd /d "%~dp0"
set "NODE_EXE="
where node >nul 2>nul
if %errorlevel%==0 set "NODE_EXE=node"
if "%NODE_EXE%"=="" (
  for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do (
    if exist "%%D\node.exe" if "%NODE_EXE%"=="" set "NODE_EXE=%%D\node.exe"
  )
)
rem only check existence when we got a real path; the PATH name "node" must not be checked
if /i not "%NODE_EXE%"=="node" if not exist "%NODE_EXE%" goto NODEMISS
if not exist "server.js" goto SRVMISS
echo Starting Classroom Media Studio, please wait ...
"%NODE_EXE%" server.js
echo.
echo [closed] the window will close. Read the lines above if it never came up.
pause
goto :EOF
:NODEMISS
echo.
echo   [ERROR] Node.js not found.
echo   Looked for: node in PATH, and %USERPROFILE%\.workbuddy\binaries\node\versions\*
echo   Please install Node.js 18 or above, then run this file again.
echo.
pause
exit /b 1
:SRVMISS
echo.
echo   [ERROR] server.js not found in this folder.
echo   Please keep this .bat and server.js in the SAME folder.
echo.
pause
exit /b 1
