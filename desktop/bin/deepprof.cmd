@echo off
rem ============================================================
rem DeepProf CLI shim（随安装包落在 <安装目录>\resources\bin\，
rem 安装器会把本目录加入用户 PATH —— 见 ../build/installer.nsh）
rem
rem 开发布局：本文件位于 <仓库>\desktop\bin\，向上两级就是仓库根，
rem 直接转发给仓库根的 deepprof.cmd。
rem 安装版布局：后端运行时打包（runtime\python + runtime\backend）落地后，
rem 在此按安装目录内的路径转发；当前先给出明确提示而不是静默失败。
rem ============================================================
setlocal
set "REPO_CMD=%~dp0..\..\deepprof.cmd"
if exist "%REPO_CMD%" (
  call "%REPO_CMD%" %*
  goto :eof
)
echo [deepprof] CLI 需要后端运行时：安装版尚未打包 Python 后端，
echo [deepprof] 请改用源码仓库根目录的 deepprof.cmd（或设置 DEEPPROF_ROOT 后重试）。
exit /b 1
