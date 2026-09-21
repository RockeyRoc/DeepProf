@echo off
rem ============================================================
rem DeepProf CLI 入口（Windows）
rem
rem   deepprof            进入交互：聊天 / /model 换模型 / /voice 换音色 / /pet 开桌宠
rem   deepprof /pet       直接唤起桌面宠物
rem   deepprof --fake     不联网，用 FakeProvider 验证链路
rem
rem 为什么是 .cmd 而不是 .bat：原来的 `启动桌宠.bat` 里有一句 chcp 65001，
rem 而 cmd.exe 是按【字节偏移】读批处理文件的，改代码页会让后面的 rem/echo
rem 行被当成命令执行 —— 那正是"黑窗口一闪而过"的来源。这里全程纯 ASCII，
rem 不碰代码页，也就没有那个坑。
rem
rem 想让 `deepprof` 在任何目录都能用：把本文件所在目录（项目根）加进 PATH。
rem ============================================================
setlocal
cd /d "%~dp0"
if "%DEEPPROF_PYTHON%"=="" (set "PY=python") else (set "PY=%DEEPPROF_PYTHON%")
"%PY%" "scripts\chat_repl.py" %*
endlocal
