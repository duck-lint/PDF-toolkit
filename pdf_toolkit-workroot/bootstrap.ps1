param(
    [string]$RepoPath = "",
    [switch]$NoBytecode = $true,
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

$Workroot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkrootItem = Get-Item -LiteralPath $Workroot

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

$activate = Join-Path $Workroot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path -LiteralPath $activate)) {
    throw "Venv not found at '$activate'. Create it: py -m venv .venv"
}

if ($DryRun) {
    Write-Host "[dry-run] workroot: $Workroot"
    Write-Host "[dry-run] repo:     $RepoPath"
    return
}

. $activate

if ($NoBytecode) { $env:PYTHONDONTWRITEBYTECODE = "1" }
$env:REPO = $RepoPath
Start-WorkrootTranscript -WorkrootPath $Workroot -NoTranscript:$NoTranscript -TranscriptDir $TranscriptDir
Write-Host "workroot:" $Workroot
Write-Host "repo:    " $env:REPO
Write-Host ""
Write-Host "Bootstrap active. Use 'exit' to return to the previous shell."

$toolsPath = Join-Path $Workroot "workroot_tools.ps1"
if (Test-Path -LiteralPath $toolsPath) { . $toolsPath }
if (Get-Command -Name wr -ErrorAction SilentlyContinue) {
    Write-Host "Run commands with manifests using: wr <command> [args...]"
}
