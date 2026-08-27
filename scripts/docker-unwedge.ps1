<#
    Docker Desktop on this host gets into a self-perpetuating crash loop:

        starting services: initializing Ingest server: listening on
        unix://.../Docker/run/sailor-ingest.sock: remove ...: The file cannot be
        accessed by the system.

    The cause is orphaned Windows AF_UNIX socket files. They appear in a
    directory listing but have no backing object, so Windows can neither stat
    nor delete them — `rm -f` and `Remove-Item -Force` both return
    "The file cannot be accessed by the system."

    The loop: Docker crashes or is killed uncleanly -> sockets are left orphaned
    -> the next start fails trying to remove them -> crash -> repeat. Every
    failed start recreates them, so this is not a one-off.

    Renaming the CONTAINING DIRECTORY works where deleting the files does not,
    because rename never opens the child objects. Docker recreates both
    directories on the next start.

    This is a WORKAROUND, not a fix. The durable prevention is to quit Docker
    Desktop cleanly (tray icon -> Quit) rather than killing it or letting the
    host sleep through a shutdown, so the sockets are closed properly.

    Usage:  pwsh -File scripts/docker-unwedge.ps1 [-Start]
#>
param([switch]$Start)

$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$targets = @(
    "$env:LOCALAPPDATA\docker-secrets-engine",
    "$env:LOCALAPPDATA\Docker\run"
)

Write-Host 'Stopping any Docker processes...'
Get-Process -Name '*docker*', 'com.docker*' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

$cleared = 0
foreach ($dir in $targets) {
    if (Test-Path -LiteralPath $dir) {
        try {
            Rename-Item -LiteralPath $dir -NewName ((Split-Path $dir -Leaf) + ".stale-$stamp") -ErrorAction Stop
            Write-Host "  cleared: $dir"
            $cleared++
        } catch {
            Write-Warning "  could not clear $dir : $($_.Exception.Message)"
        }
    }
}
Write-Host "Cleared $cleared stale socket director$(if ($cleared -eq 1) {'y'} else {'ies'})."

# Old .stale-* copies are inert; prune ones older than a week so they do not pile up.
Get-ChildItem -Path $env:LOCALAPPDATA -Filter '*.stale-*' -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.CreationTime -lt (Get-Date).AddDays(-7) } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

if ($Start) {
    Write-Host 'Starting Docker Desktop...'
    Start-Process -FilePath "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    Write-Host 'Launched. The daemon takes 1-3 minutes to accept connections.'
}
