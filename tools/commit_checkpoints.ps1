# Run from a normal PowerShell session after write access to .git is available.
# Apply each checkpoint to the index only; leave the current source files intact.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $projectRoot
try {
    $head = & git rev-parse --verify HEAD 2>$null
    if ($LASTEXITCODE -eq 0) {
        throw 'This recovery script expects the original repository with no commits. Existing history was found.'
    }
    $indexed = @(& git ls-files)
    if ($LASTEXITCODE -ne 0 -or $indexed.Count -gt 0) {
        throw 'This recovery script requires an empty Git index. Review any staged files first.'
    }
    $patches = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'checkpoints') -Filter '*.patch' | Sort-Object Name)
    if ($patches.Count -eq 0) { throw 'No checkpoint patches found.' }
    foreach ($patch in $patches) {
        $subjectLine = Get-Content -LiteralPath $patch.FullName -Encoding utf8 | Where-Object { $_ -match '^Subject: ' } | Select-Object -First 1
        $subject = $subjectLine -replace '^Subject: \[PATCH\] ', ''
        if ([string]::IsNullOrWhiteSpace($subject)) { throw "Missing subject in $($patch.Name)" }
        & git apply --cached --whitespace=error $patch.FullName
        if ($LASTEXITCODE -ne 0) { throw "Could not apply checkpoint $($patch.Name). The index was retained for inspection." }
        & git commit -m $subject
        if ($LASTEXITCODE -ne 0) { throw "Could not commit checkpoint $($patch.Name). The index was retained for inspection." }
    }
    & git status --short
} finally {
    Pop-Location
}
