$ErrorActionPreference = 'Continue'
$root = 'D:\android-build'
$log = Join-Path $root 'build.log'
$proj = 'C:\Users\17924\Documents\默认项目\android'
$dist = 'C:\Users\17924\Documents\默认项目\dist'
function Log($m) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }

$env:JAVA_HOME = Join-Path $root 'jdk'
$env:ANDROID_HOME = Join-Path $root 'sdk'
$env:ANDROID_SDK_ROOT = Join-Path $root 'sdk'
$env:PATH = "$($env:JAVA_HOME)\bin;$env:PATH"

Log '=== build start ==='
& "$($env:JAVA_HOME)\bin\java.exe" -version 2>&1 | ForEach-Object { Log ("java: " + $_) }

Push-Location $proj
# local.properties 指向 SDK
"sdk.dir=D\:\\android-build\\sdk" | Set-Content -LiteralPath (Join-Path $proj 'local.properties') -Encoding ASCII

& "$root\gradle-8.7\bin\gradle.bat" --no-daemon assembleDebug 2>&1 | ForEach-Object { Log ("gradle: " + $_) }
$code = $LASTEXITCODE
Log ("gradle exit " + $code)
Pop-Location

$apk = Get-ChildItem (Join-Path $proj 'app\build\outputs\apk\debug\*.apk') -ErrorAction SilentlyContinue | Select-Object -First 1
if ($apk) {
    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    Copy-Item $apk.FullName (Join-Path $dist 'qunban-rpg.apk') -Force
    Log ("APK ok: " + $apk.FullName + " -> dist\qunban-rpg.apk  " + [math]::Round($apk.Length/1MB, 2) + " MB")
} else {
    Log 'APK NOT FOUND'
}
Log '=== build finished ==='
