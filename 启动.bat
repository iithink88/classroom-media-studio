@echo off
title Classroom Media Studio - AIGC Home Town
cd /d "%~dp0"
set "NODE_EXE="

where node >nul 2>nul
if %errorlevel%==0 set "NODE_EXE=node"

if "%NODE_EXE%"=="" (
  for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do (
    if exist "%%D\node.exe" if "%NODE_EXE%"=="" set "NODE_EXE=%%D\node.exe"
  )
)

rem 最后兜底：托管的 node 目录（版本号每台机器不一样，这里不强写，扫到了就用）
if "%NODE_EXE%"=="" (
  for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do (
    if exist "%%D\node.exe" if "%NODE_EXE%"=="" set "NODE_EXE=%%D\node.exe"
  )
)

rem 只有在拿到的是真实路径时才做存在性检查；"node" 这种 PATH 名字不能查
if /i not "%NODE_EXE%"=="node" if not exist "%NODE_EXE%" goto NODEMISS

echo Starting Classroom Media Studio, please wait...
"%NODE_EXE%" server.js
goto :EOF

:NODEMISS
echo.
echo   [ERROR] Node.js not found.
echo   Looked for: node in PATH, and %%USERPROFILE%%\.workbuddy\binaries\node\versions\*
echo   Please install Node.js 18 or above, then run this file again.
echo.
pause
exit /b 1
