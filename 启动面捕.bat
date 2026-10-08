@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto path_error

if exist ".venv\Scripts\python.exe" goto check_dependencies
echo 正在创建面捕工具的 Python 环境...
py -3.11 -m venv .venv
if errorlevel 1 goto python_error

:check_dependencies
".venv\Scripts\python.exe" -c "import tkinter, websockets; assert websockets.__version__ == '16.0'" >nul 2>&1
if not errorlevel 1 goto launch
echo 正在安装面捕工具依赖，首次运行需要联网...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto dependency_error

:launch
echo Live Link Face 到 VTube Studio
echo 请在弹出的窗口中点击开始连接。关闭工具窗口后，此窗口会自动退出。
".venv\Scripts\python.exe" app.py %*
set "LLF_EXIT_CODE=%errorlevel%"
if not "%LLF_EXIT_CODE%"=="0" goto app_error
popd
exit /b 0

:python_error
echo 无法创建环境。请先安装 Python 3.11，并包含 tkinter 和 Python Launcher。
goto failure

:dependency_error
echo 依赖安装失败，请检查网络后重新双击运行。
goto failure

:app_error
echo 程序退出，错误代码 %LLF_EXIT_CODE%。请查看上面的报错信息。
goto failure

:failure
if not "%LLF_NONINTERACTIVE%"=="1" pause
popd
exit /b 1

:path_error
echo 无法进入工具目录，请检查文件夹是否存在。
if not "%LLF_NONINTERACTIVE%"=="1" pause
exit /b 1
