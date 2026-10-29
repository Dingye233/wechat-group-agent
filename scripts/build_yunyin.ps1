$ErrorActionPreference = 'Continue'
$src = 'D:\assets\yx_src\yunyin-xiuxian-main'
$log = 'D:\assets\yx_build.log'
function Log($m) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }

Log '=== npm install start ==='
Set-Location $src
& npm.cmd install --ignore-scripts --no-audit --no-fund --loglevel=error 2>&1 | ForEach-Object { Log ("npm: " + $_) }
Log ("install exit " + $LASTEXITCODE)

Log '=== vite build start ==='
& npx.cmd vite build --logLevel warn 2>&1 | ForEach-Object { Log ("vite: " + $_) }
Log ("build exit " + $LASTEXITCODE)
if (Test-Path (Join-Path $src 'dist')) {
    $size = (Get-ChildItem (Join-Path $src 'dist') -Recurse -File | Measure-Object -Sum Length).Sum
    Log ("dist size " + [math]::Round($size / 1MB, 2) + " MB")
} else { Log 'dist missing' }
Log '=== done ==='
