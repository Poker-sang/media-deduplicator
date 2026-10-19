param([string]$Destination = (Join-Path $PSScriptRoot '..\release'))
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv312\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv312 and install requirements-build.txt first.' }
Push-Location (Join-Path $PSScriptRoot 'frontend')
try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
Push-Location $PSScriptRoot
try {
    & $pythonPath -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    & $pythonPath -m PyInstaller --noconfirm --clean --distpath $Destination --workpath (Join-Path $PSScriptRoot '..\build') 'media-deduplicator.spec'
    if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed' }
} finally { Pop-Location }
