param(
    [string]$RuntimeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DataHome = "E:\Github\deepprof\开发者测试.deepprof",
    [string]$RHome = "E:\Code2026\R"
)
$ErrorActionPreference = "Stop"
$RuntimeRoot = (Resolve-Path $RuntimeRoot).Path
$CliDir = Join-Path $RuntimeRoot "apps\cli"
$Node = (Get-Command node.exe -ErrorAction Stop).Source
$Python = Join-Path $RuntimeRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "仓库 Python 环境不存在：$Python" }
if (-not (Test-Path -LiteralPath (Join-Path $RHome "bin\x64\Rscript.exe"))) { throw "R 未安装在指定目录：$RHome" }

$env:DEEPPROF_HOME = $DataHome
$env:DEEPPROF_RUNTIME_ROOT = $RuntimeRoot
$env:DEEPPROF_PYTHON = $Python
$env:R_HOME = $RHome
$env:R_LIBS_USER = Join-Path $RHome "library"
$env:DEEPPROF_RSCRIPT = Join-Path $RHome "bin\x64\Rscript.exe"
$materialsDir = Join-Path $DataHome "course\source"
$questionPdf = Get-ChildItem -LiteralPath $materialsDir -Filter "数据结构题集*.pdf" -File -ErrorAction SilentlyContinue | Select-Object -First 1
$bookPdf = Get-ChildItem -LiteralPath $materialsDir -Filter "数据结构（C语言版）*.pdf" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($questionPdf) { $env:DEEPPROF_QUESTION_PDF = $questionPdf.FullName }
if ($bookPdf) { $env:DEEPPROF_DATA_STRUCTURES_PDF = $bookPdf.FullName }
$env:DEEPPROF_SANDBOX_ALLOWLIST = @($DataHome, $RuntimeRoot) -join ","

Push-Location $CliDir
try { npm run build } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "CLI 构建失败。" }

$binDir = Join-Path $env:LOCALAPPDATA "DeepProf\bin"
New-Item -ItemType Directory -Path $binDir -Force | Out-Null
$shimPath = Join-Path $binDir "deepprof.cmd"
function ConvertTo-PowerShellLiteral([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
$questionLine = if ($questionPdf) { '$env:DEEPPROF_QUESTION_PDF = ' + (ConvertTo-PowerShellLiteral $questionPdf.FullName) } else { "# Question PDF can be configured with DEEPPROF_QUESTION_PDF" }
$bookLine = if ($bookPdf) { '$env:DEEPPROF_DATA_STRUCTURES_PDF = ' + (ConvertTo-PowerShellLiteral $bookPdf.FullName) } else { "# Textbook PDF can be configured with DEEPPROF_DATA_STRUCTURES_PDF" }
$powerShellShimPath = Join-Path $binDir "deepprof.ps1"
$powerShellShim = @'
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
if (-not $env:DEEPPROF_HOME) { $env:DEEPPROF_HOME = __DATA_HOME__ }
$env:DEEPPROF_RUNTIME_ROOT = __RUNTIME_ROOT__
$env:DEEPPROF_PYTHON = __PYTHON__
$env:R_HOME = __R_HOME__
$env:R_LIBS_USER = __R_LIBRARY__
$env:DEEPPROF_RSCRIPT = __RSCRIPT__
$env:DEEPPROF_SANDBOX_ALLOWLIST = __ALLOWLIST__
__QUESTION_LINE__
__BOOK_LINE__
& __NODE__ __ENTRYPOINT__ @Arguments
exit $LASTEXITCODE
'@
$powerShellShim = $powerShellShim.Replace("__DATA_HOME__", (ConvertTo-PowerShellLiteral $DataHome))
$powerShellShim = $powerShellShim.Replace("__RUNTIME_ROOT__", (ConvertTo-PowerShellLiteral $RuntimeRoot))
$powerShellShim = $powerShellShim.Replace("__PYTHON__", (ConvertTo-PowerShellLiteral $Python))
$powerShellShim = $powerShellShim.Replace("__R_HOME__", (ConvertTo-PowerShellLiteral $RHome))
$powerShellShim = $powerShellShim.Replace("__R_LIBRARY__", (ConvertTo-PowerShellLiteral (Join-Path $RHome "library")))
$powerShellShim = $powerShellShim.Replace("__RSCRIPT__", (ConvertTo-PowerShellLiteral (Join-Path $RHome "bin\x64\Rscript.exe")))
$powerShellShim = $powerShellShim.Replace("__ALLOWLIST__", (ConvertTo-PowerShellLiteral "$DataHome,$RuntimeRoot"))
$powerShellShim = $powerShellShim.Replace("__QUESTION_LINE__", $questionLine).Replace("__BOOK_LINE__", $bookLine)
$powerShellShim = $powerShellShim.Replace("__NODE__", (ConvertTo-PowerShellLiteral $Node))
$powerShellShim = $powerShellShim.Replace("__ENTRYPOINT__", (ConvertTo-PowerShellLiteral (Join-Path $RuntimeRoot "apps\cli\dist\apps\cli\src\index.js")))
[System.IO.File]::WriteAllText($powerShellShimPath, $powerShellShim + "`r`n", [System.Text.UTF8Encoding]::new($true))
$shim = @'
@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deepprof.ps1" %*
'@
[System.IO.File]::WriteAllText($shimPath, ($shim -replace "`r?`n", "`r`n") + "`r`n", [System.Text.Encoding]::ASCII)

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$segments = @($userPath -split ";" | Where-Object { $_ })
if (-not ($segments | Where-Object { [string]::Equals($_.TrimEnd('\'), $binDir.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase) })) {
    [Environment]::SetEnvironmentVariable("Path", (@($segments) + $binDir -join ";"), "User")
}
[void](New-Item -ItemType Directory -Path $DataHome -Force)
Write-Host "安装完成：$shimPath"
Write-Host "开发测试数据：$DataHome"
Write-Host "新终端运行 deepprof 即可启动；当前窗口的测试命令：$shimPath doctor"
