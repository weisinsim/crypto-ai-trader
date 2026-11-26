# One-time isolated Pionex GET-only positions probe for Windows PowerShell.
# Run from the repository root: powershell -NoProfile -File .\scripts\run_pionex_readonly_probe.ps1
# Does not save credentials to files, GitHub, Render, or command-line arguments.
$ErrorActionPreference = "Stop"
$original = @{}
$names = @("PIONEX_READ_ONLY_ENABLED", "PIONEX_LIVE_PROBE_APPROVED", "PIONEX_READ_API_KEY", "PIONEX_READ_API_SECRET")
foreach ($name in $names) { $original[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
try {
    if (-not (Test-Path ".\notifications\pionex_connection_probe.py")) {
        throw "Run this script from the crypto-ai-trader repository root."
    }
    & python -c "import httpx; import notifications.pionex_connection_probe"
    if ($LASTEXITCODE -ne 0) { throw "Python 3 and httpx are required; install dependencies before running." }

    Write-Host "GET-only positions connectivity probe. No orders. No raw account data printed."
    $keySecure = Read-Host "Paste your Pionex READ-ONLY API Key (input hidden)" -AsSecureString
    $secretSecure = Read-Host "Paste your Pionex READ-ONLY API Secret (input hidden)" -AsSecureString
    $keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($keySecure)
    $secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretSecure)
    try {
        $env:PIONEX_READ_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
        $env:PIONEX_READ_API_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
        Remove-Variable keySecure,secretSecure -ErrorAction SilentlyContinue
    }
    $env:PIONEX_READ_ONLY_ENABLED = "true"
    $env:PIONEX_LIVE_PROBE_APPROVED = "true"
    & python -m notifications.pionex_connection_probe
    $probeExit = $LASTEXITCODE
    Write-Host "Probe exit code: $probeExit"
    if ($probeExit -ne 0) { exit $probeExit }
} finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $original[$name], "Process")
    }
}
