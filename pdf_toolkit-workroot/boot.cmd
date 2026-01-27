@echo off
setlocal
set "script="
set "args="
for /f "tokens=1,*" %%A in ("%*") do (
  set "script=%%~A"
  set "args=%%~B"
)
if "%script%"=="" (
  echo Usage: %~nx0 init.ps1 [args...]
  exit /b 1
)
if /I "%script%"=="bootstrap.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -NoExit -Command "& { . '%~dp0%script%' %args% }"
) else (
  if /I "%script%"=="init.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -NoExit -Command "& { . '%~dp0%script%' %args%; if ($?) { . '%~dp0bootstrap.ps1' } }"
  ) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0%script%" %args%
  )
)
endlocal

rem usage
rem .\boot.cmd init.ps1 (initialize)
rem .\boot.cmd bootstrap.ps1 (per use)
