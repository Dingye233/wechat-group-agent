$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$root = 'D:\android-build'
$log = Join-Path $root 'setup.log'
New-Item -ItemType Directory -Path $root -Force | Out-Null
function Log($m) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }

function Fetch($url, $out, $minBytes) {
    for ($i = 1; $i -le 12; $i++) {
        $have = 0
        if (Test-Path $out) { $have = (Get-Item $out).Length }
        if ($have -ge $minBytes) { Log ("have " + [math]::Round($have/1MB) + " MB -> $out"); return $true }
        Log ("fetch attempt $i : " + [math]::Round($have/1MB) + " MB so far")
        & curl.exe -L --ssl-revoke-best-effort -C - --max-time 900 -o $out $url 2>$null
        Start-Sleep -Seconds 2
    }
    $have = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
    return ($have -ge $minBytes)
}

Log '=== setup start ==='

# JDK 21
$jdkZip = Join-Path $root 'jdk.zip'
$jdkDir = Join-Path $root 'jdk'
if (-not (Test-Path (Join-Path $jdkDir 'bin\java.exe'))) {
    if (Fetch 'https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jdk/hotspot/normal/eclipse' $jdkZip 150MB) {
        Log 'extracting JDK'
        Expand-Archive -LiteralPath $jdkZip -DestinationPath $root -Force
        $inner = Get-ChildItem $root -Directory | Where-Object { $_.Name -like 'jdk-21*' } | Select-Object -First 1
        if ($inner) { if (Test-Path $jdkDir) { Remove-Item $jdkDir -Recurse -Force }; Move-Item $inner.FullName $jdkDir }
        Log 'JDK ready'
    } else { Log 'JDK download FAILED' }
}

# cmdline-tools
$sdk = Join-Path $root 'sdk'
$clZip = Join-Path $root 'cmdline-tools.zip'
$clDir = Join-Path $sdk 'cmdline-tools\latest'
if (-not (Test-Path (Join-Path $clDir 'bin\sdkmanager.bat'))) {
    if (Fetch 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip' $clZip 80MB) {
        Log 'extracting cmdline-tools'
        $tmp = Join-Path $root 'cl-tmp'
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        Expand-Archive -LiteralPath $clZip -DestinationPath $tmp -Force
        New-Item -ItemType Directory -Path (Split-Path $clDir -Parent) -Force | Out-Null
        if (Test-Path $clDir) { Remove-Item $clDir -Recurse -Force }
        Move-Item (Join-Path $tmp 'cmdline-tools') $clDir
        Log 'cmdline-tools ready'
    } else { Log 'cmdline-tools download FAILED' }
}

# Gradle
$gradleZip = Join-Path $root 'gradle.zip'
$gradleDir = Join-Path $root 'gradle-8.7'
if (-not (Test-Path (Join-Path $gradleDir 'bin\gradle.bat'))) {
    if (Fetch 'https://services.gradle.org/distributions/gradle-8.7-bin.zip' $gradleZip 100MB) {
        Log 'extracting gradle'
        Expand-Archive -LiteralPath $gradleZip -DestinationPath $root -Force
        Log 'gradle ready'
    } else { Log 'gradle download FAILED' }
}

$env:JAVA_HOME = $jdkDir
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
$env:PATH = "$jdkDir\bin;$sdk\platform-tools;$env:PATH"

# SDK packages
$sdkmanager = Join-Path $clDir 'bin\sdkmanager.bat'
if (Test-Path $sdkmanager) {
    Log 'accepting licenses'
    $yes = ("y`n" * 60)
    $yes | & $sdkmanager --sdk_root=$sdk --licenses 2>&1 | ForEach-Object { Log ("lic: " + $_) }
    Log 'installing sdk packages'
    & $sdkmanager --sdk_root=$sdk 'platform-tools' 'platforms;android-34' 'build-tools;34.0.0' 2>&1 | ForEach-Object { Log ("sdk: " + $_) }
    Log 'sdk packages done'
} else {
    Log 'sdkmanager missing'
}
Log '=== setup finished ==='
