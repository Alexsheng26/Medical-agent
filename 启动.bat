@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
title 科研中间体 MRA

set "PYEXE="
set "RUN="
set "PIPFLAGS="
set "VENVPY=%~dp0.venv\Scripts\python.exe"
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
if defined PYVER set "PYEXE=py -3"
if defined PYEXE goto :py_found
for /f "delims=" %%v in ('python -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2^>nul') do set "PYVER=%%v"
if defined PYVER set "PYEXE=python"
:py_found

if defined PYEXE goto :py_ok
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
:py_ok

if %PYVER% GEQ 310 goto :py_new_enough
echo   [X] Python 版本太旧（需要 3.10 或更高）。
echo       请到 https://www.python.org/downloads/ 装一个新版本。
echo.
start "" "https://www.python.org/downloads/"
goto :halt
:py_new_enough

echo   [OK] Python 已就绪

REM ------------------------------------------------------------------ 环境
REM A private virtual environment, so this never disturbs anything else on the
REM machine and `mra` never has to be found on PATH — everything downstream is
REM invoked as `"%RUN%" -m mra`, and %RUN% is an absolute interpreter path
REM whichever branch below set it.

if exist "%VENVPY%" goto :venv_ready
echo   [..] 首次运行，正在创建独立运行环境（约 1 分钟）
%PYEXE% -m venv "%~dp0.venv"
if exist "%VENVPY%" goto :venv_ready

REM Creating a venv copies python.exe, which is exactly what antivirus tools
REM block (WinError 5). That is not a reason to stop: installing into the user
REM directory copies no interpreter and reaches the same place.

echo.
echo   [!] 独立运行环境建不起来。最常见的是杀毒软件拦住了复制 python.exe，
echo       其次是这个盘/文件夹不让写（报 WinError 5 拒绝访问）。
echo       不影响使用 —— 改成装到你的用户目录下，功能完全一样。
echo.
for /f "delims=" %%p in ('%PYEXE% -c "import sys;print(sys.executable)" 2^>nul') do set "RUN=%%p"
set "PIPFLAGS=--user"
goto :env_chosen

:venv_ready
set "RUN=%VENVPY%"

:env_chosen
if defined RUN goto :run_ok
echo   [X] 找不到可用的 Python 解释器路径，无法继续。
goto :halt
:run_ok

"%RUN%" -c "import mra" >nul 2>&1
if not errorlevel 1 goto :deps_ok
echo   [..] 正在安装依赖（首次约 2 分钟，需要联网）
"%RUN%" -m pip install --disable-pip-version-check -q %PIPFLAGS% -e .
if not errorlevel 1 goto :deps_ok
echo   [X] 依赖安装失败。最常见原因是网络不通，或公司/学校网络拦截了 pypi。
echo       试试国内镜像 —— 把下面这一整行复制到本窗口里回车：
echo       "%RUN%" -m pip install %PIPFLAGS% -e "%~dp0." -i https://pypi.tuna.tsinghua.edu.cn/simple
goto :halt
:deps_ok

echo   [OK] 运行环境已就绪
for /f "delims=" %%v in ('"%RUN%" -c "import mra;print(mra.__version__)" 2^>nul') do set "MRAVER=%%v"
if defined MRAVER echo   [OK] 版本 mra %MRAVER%

REM -------------------------------------------------------------------- key
REM setx writes permanently but does NOT affect the window it runs in, which is
REM the single most common reason a key "was set" and nothing works. Set both.

REM Written with labels rather than an if-block on purpose: inside a
REM parenthesised block cmd expands %NEWKEY% while parsing, i.e. before `set /p`
REM has run, so the key would always be saved empty.

if defined ANTHROPIC_API_KEY goto :havekey
if defined MRA_API_KEY goto :havekey
echo.
echo   还没有设置 API key。先选一个模型服务：
echo.
echo     1  Claude      判断质量最好，按量计费
echo     2  DeepSeek    便宜很多，全部命令都实测跑通过
echo     0  先跳过      只剩 5 查状态 / 6 引用核对 / 7 试用示例 这几条不花钱的能用
echo.
set "PICK="
set /p "PICK=请输入数字后回车: "
if "%PICK%"=="1" goto :key_claude
if "%PICK%"=="2" goto :key_deepseek
goto :havekey

:key_claude
echo.
echo   到 https://platform.claude.com 的 Settings -^> API Keys 建一个，
echo   然后在下面粘贴（在窗口里点右键就是粘贴）。
start "" "https://platform.claude.com/settings/keys"
set "NEWKEY="
set /p "NEWKEY=API key (sk-ant-...): "
if not defined NEWKEY goto :havekey
setx ANTHROPIC_API_KEY "%NEWKEY%" >nul
set "ANTHROPIC_API_KEY=%NEWKEY%"
echo   [OK] 已保存，以后不用再输
goto :havekey

:key_deepseek
echo.
echo   到 https://platform.deepseek.com 的 API keys 建一个，
echo   然后在下面粘贴（在窗口里点右键就是粘贴）。
start "" "https://platform.deepseek.com/api_keys"
set "NEWKEY="
set /p "NEWKEY=API key (sk-...): "
if not defined NEWKEY goto :havekey
setx MRA_PROVIDER "openai" >nul
setx MRA_BASE_URL "https://api.deepseek.com" >nul
setx MRA_MODEL "deepseek-chat" >nul
setx MRA_API_KEY "%NEWKEY%" >nul
set "MRA_PROVIDER=openai"
set "MRA_BASE_URL=https://api.deepseek.com"
set "MRA_MODEL=deepseek-chat"
set "MRA_API_KEY=%NEWKEY%"
echo   [OK] 已保存，以后不用再输
:havekey

REM DeepSeek 走的是 OpenAI 兼容接口，要多装一个包。检查放在分支外面，
REM 因为用户也可能是自己设好环境变量来的，没走上面那个菜单。

if /i not "%MRA_PROVIDER%"=="openai" goto :ready
"%RUN%" -c "import openai" >nul 2>&1
if not errorlevel 1 goto :ready
echo   [..] 正在安装 DeepSeek 需要的组件（约 20 秒）
"%RUN%" -m pip install --disable-pip-version-check -q openai
if errorlevel 1 echo   [X] 装不上。菜单里选 9 连接自检，能看到具体原因。
:ready

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
"%RUN%" -m mra init --email "%EMAIL%" >nul 2>&1
:inited

REM ------------------------------------------------------------------- 菜单
:menu
echo.
echo ============================================================
echo   数据目录: %WORK%
echo ============================================================
echo.
echo    1  网页界面      推荐 —— 在浏览器里操作，不用记命令
echo.
echo    2  导入并阅读    PDF / PubMed XML / 纯文本，读完直接出分析
echo    3  提炼文献      逐篇结构化提炼（每篇一次调用）
echo    4  科学对话      基于你的文献库追问
echo    5  评估数据      打分 + 推荐候选期刊
echo    6  文献列表      看库里有什么、每篇提炼出了什么（不花钱）
echo    7  引用核对      检查文稿里的引用是否真实（不花钱）
echo    8  试用示例      导入仓库自带的 8 篇示例文献
echo    9  连接自检      模型连不通时先跑这个
echo   10  打开数据目录
echo   11  查看状态      文献数、假说、花费
echo    0  退出
echo.
set "CHOICE="
set /p "CHOICE=请输入数字后回车: "

if "%CHOICE%"=="1" goto :do_web
if "%CHOICE%"=="2" goto :do_import
if "%CHOICE%"=="3" goto :do_digest
if "%CHOICE%"=="4" goto :do_chat
if "%CHOICE%"=="5" goto :do_assess
if "%CHOICE%"=="6" goto :do_library
if "%CHOICE%"=="7" goto :do_refs
if "%CHOICE%"=="8" goto :do_demo
if "%CHOICE%"=="9" goto :do_doctor
if "%CHOICE%"=="10" goto :do_open
if "%CHOICE%"=="11" goto :do_status
if "%CHOICE%"=="0" goto :done
echo   没有这个选项，请重新选。
goto :menu

:do_web
echo.
echo   正在启动网页界面，浏览器会自动打开。
echo   界面只在这台电脑上，别人访问不到。
echo   要停下来回到这个菜单，在本窗口按 Ctrl-C。
echo.
"%RUN%" -m mra web
goto :after

:do_import
echo.
echo   把 PDF 文件拖进这个窗口然后回车（可以先拖一个试试）。
set "TARGET="
set /p "TARGET=文件: "
if not defined TARGET goto :menu
"%RUN%" -m mra import %TARGET% --digest
goto :after

:do_digest
"%RUN%" -m mra digest
goto :after

:do_chat
echo.
echo   直接输入你的问题，中文英文都行。留空回车返回菜单。
set "MSG="
set /p "MSG=问题: "
if not defined MSG goto :menu
"%RUN%" -m mra chat "%MSG%"
goto :after

:do_assess
echo.
echo   把数据文件（csv / txt / md）拖进窗口然后回车。
set "DATAF="
set /p "DATAF=数据文件: "
if not defined DATAF goto :menu
set "NOTE="
set /p "NOTE=补充说明（例如 n=12/组，可直接回车跳过）: "
"%RUN%" -m mra assess %DATAF% --notes "%NOTE%"
goto :after

:do_library
"%RUN%" -m mra library
echo.
echo   想看某一篇的完整提炼结果，输入它的编号（第一列那一串），直接回车跳过。
set "DOCID="
set /p "DOCID=编号: "
if not defined DOCID goto :after
"%RUN%" -m mra library %DOCID%
goto :after

:do_status
"%RUN%" -m mra status
echo.
"%RUN%" -m mra usage
goto :after

:do_refs
echo.
echo   把要检查的文稿（.md / .txt）拖进窗口然后回车。
set "DOC="
set /p "DOC=文稿: "
if not defined DOC goto :menu
"%RUN%" -m mra refs %DOC% --list
goto :after

:do_demo
"%RUN%" -m mra demo
"%RUN%" -m mra import demo_corpus.xml
echo.
echo   导入完成。可以选 3 试着问：这批文献里最大的矛盾是什么
goto :after

:do_doctor
"%RUN%" -m mra doctor
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
