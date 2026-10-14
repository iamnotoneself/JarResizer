# Build the JSR-256 sensor API stub: compile 1.4 -> preverify ->

# classes (merged into converted game jars) + sync into jartool\assets

$ErrorActionPreference = "Stop"

$dir = $PSScriptRoot

# jartool\sensorstub -> 上两级到工作区根（工具链所在）

$root = Split-Path -Parent $dir  # stub dir -> repo root

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

$out = Join-Path $dir "classes"

Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue

Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path (Join-Path $build "classes") | Out-Null

New-Item -ItemType Directory -Force -Path $out | Out-Null



Write-Host "[1/4] compile JSR-256 sensor stub (jr.sensor, CLDC+MIDP bootclasspath)"

$n = Join-Path $dir "src\jr\sensor"

& $java17 -jar $ecj -source 1.4 -target 1.4 -encoding UTF-8 `

    -bootclasspath "$cldc;$midp" `

    -d (Join-Path $build "classes") `

    "$n\Unit.java" "$n\MeasurementRange.java" "$n\ChannelInfo.java" `

    "$n\Data.java" "$n\DataListener.java" "$n\SensorInfo.java" `

    "$n\SensorConnection.java" "$n\SensorManager.java" `

    "$n\FakeChannelInfo.java" "$n\FakeSensorInfo.java" "$n\FakeData.java" `

    "$n\FakeSensorConnection.java"

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

-keep class jr.sensor.** { *; }

"@

Set-Content -Path (Join-Path $build "stub.pro") -Value $pro -Encoding ASCII



Write-Host "[2/4] preverify (ProGuard -microedition)"

Push-Location $build

& $java17 -jar $proguard "@stub.pro"

Pop-Location

if (-not (Test-Path (Join-Path $build "pre.jar"))) { throw "preverify failed" }



Write-Host "[3/4] extract classes into sensorstub\classes"

Add-Type -AssemblyName System.IO.Compression.FileSystem

$zip = [System.IO.Compression.ZipFile]::OpenRead((Join-Path $build "pre.jar"))

try {

    foreach ($entry in $zip.Entries) {

        if (-not $entry.FullName.EndsWith(".class")) { continue }

        $dest = Join-Path $out $entry.FullName

        New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null

        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true)

    }

} finally {

    $zip.Dispose()

}



Write-Host "[4/4] sync classes into jartool\assets (bundled into the exe)"

$assets = Join-Path $dir "..\assets"

$toDir = Join-Path $assets "jr\sensor"

New-Item -ItemType Directory -Force -Path $toDir | Out-Null

Get-ChildItem -Recurse -Filter "*.class" $out | ForEach-Object {

    Copy-Item $_.FullName (Join-Path $toDir $_.Name) -Force

}

Write-Host "sensor stub ready: sensorstub\classes (+ assets sync)"

