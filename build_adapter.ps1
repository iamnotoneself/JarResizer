# Build the injected ScaleCanvas adapter: compile 1.4 -> preverify -> assets

# Run once when adapter/src changes; the shipped tool embeds assets/jr/ScaleCanvas.class

$ErrorActionPreference = "Stop"

$dir = $PSScriptRoot

$root = $dir  # script sits at the repo root

$jdk = $null

foreach ($c in @($env:JAVA17, $env:JAVA_HOME,

    "C:\Program Files\Microsoft\jdk-17*", "C:\Program Files\Eclipse Adoptium\jdk-17*",

    "C:\Program Files\Java\jdk-17*", "$root\tools\jre")) {

    if (-not $c) { continue }

    $p = Resolve-Path $c -ErrorAction SilentlyContinue | Select-Object -First 1

    if ($p) { $jdk = $p.Path; break }

}

if (-not $jdk) { throw "java.exe not found: install JDK17 or keep bundled tools\jre next to the scripts" }

$java17 = Join-Path $jdk "bin\java.exe"

$jarExe = Join-Path $jdk "bin\jar.exe"

if (-not (Test-Path $jarExe)) { throw "jar.exe not found: rebuilding stubs needs a full JDK17 (bundled tools\jre is runtime-only)" }

$ecj = Join-Path $root "tools\ecj.jar"

$proguard = Join-Path $root "tools\proguard\proguard-7.4.2\lib\proguard.jar"

$cldc = Join-Path $root "tools\cldcapi11.jar"

$midp = Join-Path $root "tools\midpapi20.jar"



$build = Join-Path $dir "build"

$assets = Join-Path $dir "assets"

Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path (Join-Path $build "classes") | Out-Null

New-Item -ItemType Directory -Force -Path (Join-Path $assets "jr") | Out-Null



Write-Host "[1/3] compile ScaleCanvas (1.4, CLDC bootclasspath)"

& $java17 -jar $ecj -source 1.4 -target 1.4 -encoding UTF-8 `

    -bootclasspath "$cldc;$midp" `

    -d (Join-Path $build "classes") `

    (Join-Path $dir "adapter\src\jr\ScaleCanvas.java")

if ($LASTEXITCODE -ne 0) { throw "compile failed" }



& $jarExe cf (Join-Path $build "raw.jar") -C (Join-Path $build "classes") .



$pro = @"

-injars raw.jar

-outjars pre.jar

-libraryjars '$cldc'

-libraryjars '$midp'

-microedition

-dontshrink

-dontoptimize

-dontobfuscate

-dontwarn

-keep class jr.ScaleCanvas { *; }

"@

Set-Content -Path (Join-Path $build "adapter.pro") -Value $pro -Encoding ASCII



Write-Host "[2/3] preverify (ProGuard -microedition)"

Push-Location $build

& $java17 -jar $proguard "@adapter.pro"

Pop-Location

if (-not (Test-Path (Join-Path $build "pre.jar"))) { throw "preverify failed" }



Write-Host "[3/3] extract class into assets"

Add-Type -AssemblyName System.IO.Compression.FileSystem

$zip = [System.IO.Compression.ZipFile]::OpenRead((Join-Path $build "pre.jar"))

try {

    $entry = $zip.GetEntry("jr/ScaleCanvas.class")

    [System.IO.Compression.ZipFileExtensions]::ExtractToFile(

        $entry, (Join-Path $assets "jr\ScaleCanvas.class"), $true)

} finally {

    $zip.Dispose()

}

Write-Host "adapter ready: assets\jr\ScaleCanvas.class"

