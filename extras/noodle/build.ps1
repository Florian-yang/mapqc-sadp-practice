param([string]$Compiler = "g++")
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    & $Compiler -std=c++17 -O2 -Wall -Wextra -pedantic -static -finput-charset=UTF-8 -fexec-charset=UTF-8 noodle_shop.cpp -o noodle_shop.exe
    if ($LASTEXITCODE -ne 0) { throw "Build failed" }
} finally { Pop-Location }
