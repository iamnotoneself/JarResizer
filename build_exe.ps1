# Package the GUI tool into a single Windows exe (dist\JarResizer.exe)
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
Push-Location $dir
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m PyInstaller --noconfirm --onefile --windowed `
    --name JarResizer `
    --hidden-import build_bb `
    --add-data "$dir\assets;assets" `
    --distpath "$dir\dist" `
    --workpath "$dir\build\pyi" `
    --specpath "$dir\build" `
    "$dir\gui.py"
Pop-Location
if (Test-Path (Join-Path $dir "dist\JarResizer.exe")) {
    $f = Get-Item (Join-Path $dir "dist\JarResizer.exe")
    Write-Host ("OK: {0} ({1:N0} bytes)" -f $f.FullName, $f.Length)
} else {
    throw "exe not produced"
}
