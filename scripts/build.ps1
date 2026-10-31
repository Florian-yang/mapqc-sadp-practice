param(
    [string]$PythonPath = 'C:/Users/flori/AppData/Local/Programs/Python/Python313/python.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $PythonPath)) { throw "Python解释器不存在：$PythonPath" }
$savedBuildPath = $env:Path
$pythonRoot = Split-Path -Parent $PythonPath
$windowsRoot = [Environment]::GetFolderPath('Windows')
# Resolve Windows/Qt dependencies without unrelated tools' DLLs on PATH.
$env:Path = (@($pythonRoot, (Join-Path $pythonRoot 'Scripts'), (Join-Path $windowsRoot 'System32'), $windowsRoot) -join ';')
Push-Location -LiteralPath $projectRoot
try {
    & $PythonPath -m PyInstaller --noconfirm --onedir --windowed --name MapQCPractice --distpath dist --workpath build/package --specpath build/spec --paths $projectRoot main.py
    if ($LASTEXITCODE -ne 0) { throw '打包失败，请查看构建输出。' }
    $programPath = Join-Path $projectRoot 'dist/MapQCPractice/MapQCPractice.exe'
    if (-not (Test-Path -LiteralPath $programPath)) { throw '未生成预期的可执行文件。' }
    Write-Output $programPath
} finally {
    Pop-Location
    $env:Path = $savedBuildPath
}
