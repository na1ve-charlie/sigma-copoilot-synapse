param(
    [switch]$SkipTests,
    [switch]$SkipModel
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required to build the package. Install uv and try again."
}

if (-not $SkipTests) {
    & uv --cache-dir ".\.uv-cache" run pytest
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; package was not built." }
}

$pyinstallerArgs = @(
    "--noconfirm", "--clean", "--onedir", "--console",
    "--name", "SigmaMaia",
    "--paths", "src",
    "--paths", "..\themis",
    "--collect-submodules", "themis",
    "--collect-submodules", "intent_fusion",
    "--collect-all", "sentence_transformers",
    "--collect-submodules", "transformers",
    "--collect-submodules", "uvicorn",
    "--copy-metadata", "sentence-transformers",
    "--copy-metadata", "transformers",
    "--copy-metadata", "huggingface-hub",
    "--copy-metadata", "torch",
    "--specpath", "build\pyinstaller",
    "--workpath", "build\pyinstaller",
    "--distpath", "dist\windows",
    "src\maia\server.py"
)
& uv --cache-dir ".\.uv-cache" run --with "pyinstaller==6.21.0" pyinstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$packageRoot = Join-Path $projectRoot "dist\windows\SigmaMaia"
Copy-Item "configs" $packageRoot -Recurse -Force
New-Item -ItemType Directory -Force -Path "$packageRoot\config" | Out-Null
Copy-Item "packaging\windows\app.env.example" "$packageRoot\config\app.env" -Force
Copy-Item "packaging\windows\start.cmd" $packageRoot -Force
Copy-Item "packaging\windows\start-demo.cmd" $packageRoot -Force
Copy-Item "packaging\windows\check-config.cmd" $packageRoot -Force
Copy-Item "packaging\windows\run-service.ps1" $packageRoot -Force
Copy-Item "packaging\windows\install-service.ps1" $packageRoot -Force
Copy-Item "packaging\windows\uninstall-service.ps1" $packageRoot -Force
Copy-Item "docs\windows-production-deployment.md" $packageRoot -Force
New-Item -ItemType Directory -Force -Path "$packageRoot\demo" | Out-Null
Copy-Item "demo-chat\chat.html" "$packageRoot\demo\chat.html" -Force

if (-not $SkipModel) {
    $modelName = "models--Qwen--Qwen3-Embedding-0.6B"
    $modelCache = Join-Path $env:USERPROFILE ".cache\huggingface\hub\$modelName"
    if (-not (Test-Path $modelCache)) {
        throw "Embedding model cache not found: $modelCache"
    }
    $modelHub = "$packageRoot\models\huggingface\hub"
    New-Item -ItemType Directory -Force -Path $modelHub | Out-Null
    Copy-Item $modelCache $modelHub -Recurse -Force
}

$checkMode = if ($SkipModel) { "--check-config" } else { "--check-model" }
& "$packageRoot\SigmaMaia.exe" --config "$packageRoot\config\app.env" $checkMode
if ($LASTEXITCODE -ne 0) { throw "Packaged configuration check failed." }
Write-Host "Windows package created: $packageRoot"
