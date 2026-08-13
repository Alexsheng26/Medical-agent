@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
title 科研中间体 MRA

set "PYEXE="
set "VENV=.venv\Scripts\python.exe"
set "WORK=%~dp0workspace"

echo.
echo ============================================================
echo   科研中间体 MRA
echo ============================================================
echo.

REM ---------------------------------------------------------------- Python
REM `where python` returns 0 for the Microsoft Store alias too, so ask Python
REM to actually print something. The stub prints nothing at all, which is the
REM failure that looks exactly like success.

for /f "delims=" %%v in ('py -3 -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2^>nul') do set "PYVER=%%v"
if defined PYVER (
    set "PYEXE=py -3"
) else (
    for /f "delims=" %%v in ('python -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2^>nul') do set "PYVER=%%v"
    if defined PYVER set "PYEXE=python"
)

if not defined PYEXE (
    echo   [X] 没有找到可用的 Python。
    echo.
    echo   注意：如果你刚才敲 python 时"什么都没显示、也没报错"，
    echo   那不是装好了 —— Windows 自带一个指向应用商店的假 python，
    echo   没装 Python 时敲它会静默跳转商店，每条命令都零输出零报错。
    echo.
    echo   请这样做：
    echo     1. 打开 https://www.python.org/downloads/ 下载安装包
    echo     2. 安装第一屏务必勾上 "Add python.exe to PATH"（在窗口最下面）
    echo     3. 装完关掉所有命令行窗口，重新双击本文件
    echo.
    start "" "https://www.python.org/downloads/"
    goto :halt
)

if %PYVER% LSS 310 (
    echo   [X] Python 版本太旧（需要 3.10 或更高）。
    echo       请到 https://www.python.org/downloads/ 装一个新版本。
    echo.
    start "" "https://www.python.org/downloads/"
    goto :halt
)

echo   [OK] Python 已就绪

REM ------------------------------------------------------------------ 环境
REM A private virtual environment, so this never disturbs anything else on the
REM machine and `mra` never has to be found on PATH — it is always invoked as
REM `.venv\Scripts\python.exe -m mra`.

if not exist "%VENV%" (
    echo   [..] 首次运行，正在创建独立运行环境（约 1 分钟）
    %PYEXE% -m venv .venv
    if errorlevel 1 (
        echo   [X] 运行环境创建失败。
        echo       如果你的用户名含中文或路径很长，试着把整个文件夹移到 D:\mra 再运行。
        goto :halt
    )
)

if not exist "%VENV%" (
    echo   [X] 运行环境不完整，请删掉本文件夹里的 .venv 目录后重试。
    goto :halt
)

"%VENV%" -c "import mra" >nul 2>&1
if errorlevel 1 (
    echo   [..] 正在安装依赖（首次约 2 分钟，需要联网）
    "%VENV%" -m pip install --disable-pip-version-check -q -e .
    if errorlevel 1 (
        echo   [X] 依赖安装失败。最常见原因是网络不通或公司/学校网络拦截了 pypi。
        echo       可以试试国内镜像：
        echo       .venv\Scripts\python.exe -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
        goto :halt
    )
)

echo   [OK] 运行环境已就绪

REM -------------------------------------------------------------------- key
REM setx writes permanently but does NOT affect the window it runs in, which is
REM the single most common reason a key "was set" and nothing works. Set both.

REM Written with labels rather than an if-block on purpose: inside a
REM parenthesised block cmd expands %NEWKEY% while parsing, i.e. before `set /p`
REM has run, so the key would always be saved empty.

if defined ANTHROPIC_API_KEY goto :havekey
echo.
echo   还没有设置 API key。
echo   到 https://platform.claude.com 的 Settings -^> API Keys 建一个，
echo   然后在下面粘贴（在窗口里点右键就是粘贴）。直接回车可跳过，
echo   但除了 lint / refs / status 之外的命令都会用不了。
echo.
set "NEWKEY="
set /p "NEWKEY=API key: "
if not defined NEWKEY goto :havekey
setx ANTHROPIC_API_KEY "%NEWKEY%" >nul
set "ANTHROPIC_API_KEY=%NEWKEY%"
echo   [OK] 已保存，以后不用再输
:havekey

REM -------------------------------------------------------------- 工作目录
REM The knowledge base lives beside this file rather than inside it, so the
REM researcher's unpublished data never sits in the code folder.

if not exist "%WORK%" mkdir "%WORK%"
pushd "%WORK%"
if exist ".mra" goto :inited
echo.
echo   首次初始化工作目录。
set "EMAIL="
set /p "EMAIL=你的邮箱（NCBI 检索要求提供，可直接回车跳过）: "
"%~dp0%VENV%" -m mra init --email "%EMAIL%" >nul 2>&1
:inited

REM ------------------------------------------------------------------- 菜单
:menu
echo.
echo ============================================================
echo   数据目录: %WORK%
echo ============================================================
echo.
echo    1  导入文献      PDF / PubMed XML / 纯文本
echo    2  提炼文献      逐篇结构化提炼（每篇一次调用）
echo    3  科学对话      基于你的文献库追问
echo    4  评估数据      打分 + 推荐候选期刊
echo    5  查看状态      文献数、假说、花费
echo    6  引用核对      检查文稿里的引用是否真实（不花钱）
echo    7  试用示例      导入仓库自带的 8 篇示例文献
echo    8  打开数据目录
echo    9  连接自检      模型连不通时先跑这个
echo    0  退出
echo.
set "CHOICE="
set /p "CHOICE=请输入数字后回车: "

if "%CHOICE%"=="1" goto :do_import
if "%CHOICE%"=="2" goto :do_digest
if "%CHOICE%"=="3" goto :do_chat
if "%CHOICE%"=="4" goto :do_assess
if "%CHOICE%"=="5" goto :do_status
if "%CHOICE%"=="6" goto :do_refs
if "%CHOICE%"=="7" goto :do_demo
if "%CHOICE%"=="8" goto :do_open
if "%CHOICE%"=="9" goto :do_doctor
if "%CHOICE%"=="0" goto :done
echo   没有这个选项，请重新选。
goto :menu

:do_import
echo.
echo   把 PDF 文件拖进这个窗口然后回车（可以先拖一个试试）。
set "TARGET="
set /p "TARGET=文件: "
if not defined TARGET goto :menu
"%~dp0%VENV%" -m mra import %TARGET%
goto :after

:do_digest
"%~dp0%VENV%" -m mra digest
goto :after

:do_chat
echo.
echo   直接输入你的问题，中文英文都行。留空回车返回菜单。
set "MSG="
set /p "MSG=问题: "
if not defined MSG goto :menu
"%~dp0%VENV%" -m mra chat "%MSG%"
goto :after

:do_assess
echo.
echo   把数据文件（csv / txt / md）拖进窗口然后回车。
set "DATAF="
set /p "DATAF=数据文件: "
if not defined DATAF goto :menu
set "NOTE="
set /p "NOTE=补充说明（例如 n=12/组，可直接回车跳过）: "
"%~dp0%VENV%" -m mra assess %DATAF% --notes "%NOTE%"
goto :after

:do_status
"%~dp0%VENV%" -m mra status
echo.
"%~dp0%VENV%" -m mra usage
goto :after

:do_refs
echo.
echo   把要检查的文稿（.md / .txt）拖进窗口然后回车。
set "DOC="
set /p "DOC=文稿: "
if not defined DOC goto :menu
"%~dp0%VENV%" -m mra refs %DOC% --list
goto :after

:do_demo
"%~dp0%VENV%" -m mra import "%~dp0examples\demo_corpus.xml"
echo.
echo   导入完成。可以选 3 试着问：这批文献里最大的矛盾是什么
goto :after

:do_doctor
"%~dp0%VENV%" -m mra doctor
goto :after

:do_open
start "" "%WORK%"
goto :menu

:after
echo.
echo   ---- 完成，按任意键回到菜单 ----
pause >nul
goto :menu

:done
popd
endlocal
exit /b 0

:halt
echo.
echo   ---- 按任意键关闭 ----
pause >nul
endlocal
exit /b 1
