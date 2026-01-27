param(
    [string]$RepoPath = "",
    [string]$PthName = "repo.pth",
    [switch]$NoTranscript,
    [string]$TranscriptDir = "",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"


function Start-WorkrootTranscript {
    param(
        [Parameter(Mandatory = $true)][string]$WorkrootPath,
        [switch]$NoTranscript,
        [string]$TranscriptDir
    )
    if ($NoTranscript) { return }
    $transcriptVar = Get-Variable -Name WorkrootTranscriptActive -Scope Global -ErrorAction SilentlyContinue
    if ($transcriptVar -and $transcriptVar.Value) { return }

    if ([string]::IsNullOrWhiteSpace($TranscriptDir)) {
        $TranscriptDir = Join-Path $WorkrootPath "_workroot_transcripts"
    }

    if (-not (Test-Path -LiteralPath $TranscriptDir)) {
        New-Item -ItemType Directory -Path $TranscriptDir -Force | Out-Null
    }

    $stamp = [DateTime]::Now.ToString("yyyyMMdd_HHmmss")
    $shortId = ([Guid]::NewGuid().ToString("N").Substring(0,6))
    $path = Join-Path $TranscriptDir ("session_{0}_{1}.log" -f $stamp, $shortId)

    try {
        Start-Transcript -Path $path -Append -ErrorAction Stop | Out-Null
        $global:WorkrootTranscriptActive = $true
        $global:WorkrootTranscriptPath = $path
        Write-Host "Transcript: $path"
    } catch {
        $msg = $_.Exception.Message
        if ($msg -match "transcrib" -and $msg -match "progress|already") {
            $global:WorkrootTranscriptActive = $true
        } else {
            Write-Warning ("Transcript failed: {0}" -f $msg)
        }
    }

    $exitVar = Get-Variable -Name WorkrootTranscriptExitRegistered -Scope Global -ErrorAction SilentlyContinue
    if (-not ($exitVar -and $exitVar.Value)) {
        Register-EngineEvent PowerShell.Exiting -Action { try { Stop-Transcript | Out-Null } catch {} } | Out-Null
        $global:WorkrootTranscriptExitRegistered = $true
    }
}

# Load the same repo-derivation logic by calling bootstrap in DryRun mode is tempting,
# but bootstrap activates the venv; we want to ensure venv is active here too.
# So: activate venv and derive repo in the same way.

$Workroot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkrootItem = Get-Item -LiteralPath $Workroot

if (-not $DryRun) {
    Start-WorkrootTranscript -WorkrootPath $Workroot -NoTranscript:$NoTranscript -TranscriptDir $TranscriptDir
}
$script:TranscriptActive = $false
$transcriptVar = Get-Variable -Name WorkrootTranscriptActive -Scope Global -ErrorAction SilentlyContinue
if ($transcriptVar -and $transcriptVar.Value) { $script:TranscriptActive = $true }

function Invoke-WorkrootNative {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Args,
        [switch]$CaptureText
    )
    if ($CaptureText) {
        $output = $null
        if ($script:TranscriptActive) {
            $output = & $Exe @Args 2>&1
            if ($output) { $output | Out-Host }
        } else {
            $output = & $Exe @Args
        }
        if ($null -eq $output) { return "" }
        return ($output | Out-String).TrimEnd()
    }

    if ($script:TranscriptActive) {
        & $Exe @Args 2>&1 | Out-Host
    } else {
        & $Exe @Args
    }
    return $null
}

$venvDir = Join-Path $Workroot ".venv"
$activate = Join-Path $Workroot ".venv\Scripts\Activate.ps1"

if (-not (Test-Path -LiteralPath $activate)) {
    Write-Host "No venv found. Creating .venv in workroot..."
    Invoke-WorkrootNative -Exe "py" -Args @("-m","venv",$venvDir)
    $req = Join-Path $Workroot "requirements.txt"
    if (Test-Path -LiteralPath $req) {
        $py = Join-Path $venvDir "Scripts\python.exe"
        Write-Host "Installing workroot requirements..."
        Invoke-WorkrootNative -Exe $py -Args @("-m","pip","install","--upgrade","pip")
        if ($LASTEXITCODE -ne 0) { throw "pip failed during upgrade." }
        Invoke-WorkrootNative -Exe $py -Args @("-m","pip","install","-r",$req)
        if ($LASTEXITCODE -ne 0) { throw "pip failed installing workroot requirements." }
        Write-Host "Workroot requirements installed."
    }
}

. $activate

$env:PYTHONDONTWRITEBYTECODE = "1"

if ([string]::IsNullOrWhiteSpace($RepoPath)) {
    $leaf = $WorkrootItem.Name
    if ($leaf -notlike "*-workroot") {
        throw "Expected workroot folder name to end with '-workroot'. Got: '$leaf'. Pass -RepoPath or rename the folder."
    }
    $repoName = $leaf.Substring(0, $leaf.Length - "-workroot".Length)
    $RepoPath = Join-Path -Path $WorkrootItem.Parent.FullName -ChildPath $repoName
}

if (-not (Test-Path -LiteralPath $RepoPath)) {
    throw "Repo path does not exist: '$RepoPath'. Create it or pass -RepoPath."
}
if (-not (Test-Path -LiteralPath $RepoPath -PathType Container)) {
    throw "Repo path is not a folder: '$RepoPath'. Pass -RepoPath to a directory or rename the workroot."
}

$site = Invoke-WorkrootNative -Exe "python" -Args @("-c","import site; print(site.getsitepackages()[0])") -CaptureText
$pthPath = Join-Path $site $PthName

if ($DryRun) {
    Write-Host "[dry-run] repo: $RepoPath"
    Write-Host "[dry-run] site-packages: $site"
    Write-Host "[dry-run] would write: $pthPath"
    return
}

Set-Content -Path $pthPath -Value $RepoPath -Encoding UTF8
Write-Host "Wrote .pth:" $pthPath
Write-Host "Repo path :" $RepoPath

# Verify: you need SOME importable module in the repo (e.g., repo_marker.py)
try {
    Invoke-WorkrootNative -Exe "python" -Args @("-c","import repo_marker; print('repo_marker:', repo_marker.__file__)")
    Write-Host "Import test: OK"
} catch {
    Write-Warning "Import test failed. Ensure 'repo_marker.py' exists in the repo root (or adjust the test import)."
    throw
}

$toolsPath = Join-Path $Workroot "workroot_tools.ps1"
if (Test-Path -LiteralPath $toolsPath) { . $toolsPath }
if (Get-Command -Name wr -ErrorAction SilentlyContinue) {
    Write-Host "Run commands with manifests using: wr <command> [args...]"
}

