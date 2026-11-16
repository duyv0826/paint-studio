@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "APP=%HERE%paint_studio_widget.py"
set "EXE=%HERE%打包\绘画自习室.exe"
set "PYW="

rem 1) 已经打包好 exe 就直接跑 exe，不需要 Python
if exist "%EXE%" (
  start "" "%EXE%" %*
  exit /b 0
)

rem 2) 没有 exe：找一个带 tkinter 的 Python，用 pythonw 拉起脚本（不弹黑窗）
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PYW (
    py -%%V -c "import tkinter" >nul 2>nul
    if not errorlevel 1 (
      for /f "delims=" %%P in ('py -%%V -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%P"
    )
  )
)

if not defined PYW (
  python -c "import tkinter" >nul 2>nul
  if not errorlevel 1 (
    for /f "delims=" %%P in ('python -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%P"
  )
)

if not defined PYW (
  echo 没找到带 tkinter 的 Python，也没有打包好的 exe。
  echo 装 Python 3.12+ 并勾选 tcl/tk，或者直接用 打包\绘画自习室.exe。
  pause
  exit /b 1
)

start "" "%PYW%" "%APP%" %*
