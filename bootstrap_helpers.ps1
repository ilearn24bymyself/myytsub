# Helpers dot-sourced by bootstrap.ps1. Kept in a separate file so they can be tested on their own.
# ASCII only on purpose: Windows PowerShell 5.1 misreads UTF-8 without a BOM.

# Runs $Attempt up to $MaxAttempts times. $Attempt must output $true on success.
# A thrown exception counts as a failed attempt. Returns $true/$false, never throws.
# Why: downloads from GitHub / nodejs.org / npm sometimes drop the connection midway
# (ECONNRESET); a single glitch should not abort the whole first-run install.
function Invoke-WithRetry {
    param(
        [Parameter(Mandatory)] [scriptblock]$Attempt,
        [Parameter(Mandatory)] [string]$Label,
        [int]$MaxAttempts = 3,
        [int]$DelaySeconds = $(if ($env:BOOTSTRAP_RETRY_DELAY) { [int]$env:BOOTSTRAP_RETRY_DELAY } else { 5 })
    )
    for ($n = 1; $n -le $MaxAttempts; $n++) {
        try {
            if (& $Attempt) { return $true }
        } catch {
            Write-Host "[Setup] $Label raised an error: $($_.Exception.Message)"
        }
        if ($n -lt $MaxAttempts) {
            Write-Host "[Setup] $Label failed (attempt $n/$MaxAttempts), retrying in $DelaySeconds seconds..."
            Start-Sleep -Seconds $DelaySeconds
        }
    }
    return $false
}
