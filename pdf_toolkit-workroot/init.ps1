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
    $convertOutput = {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { $_ }
    }
    if ($CaptureText) {
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $output = $null
        try {
            $output = & $Exe @Args 2>&1 | ForEach-Object $convertOutput
            if ($script:TranscriptActive -and $output) { $output | Out-Host }
        } finally {
            $ErrorActionPreference = $prevErrorAction
        }
        if ($null -eq $output) { return "" }
        return ($output | Out-String).TrimEnd()
    }

    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($script:TranscriptActive) {
            & $Exe @Args 2>&1 | ForEach-Object $convertOutput | Out-Host
        } else {
            & $Exe @Args 2>&1 | ForEach-Object $convertOutput
        }
    } finally {
        $ErrorActionPreference = $prevErrorAction
    }
    return $null
}

function Ensure-WorkrootRequirementsInstalled {
    param(
        [Parameter(Mandatory = $true)][string]$Workroot
    )

    $reqPath = Join-Path $Workroot "requirements.txt"
    if (-not (Test-Path -LiteralPath $reqPath)) {
        return
    }

    $stampPath = Join-Path $Workroot ".venv\.workroot_requirements_stamp.json"
    $requirementsMtimeUtc = (Get-Item -LiteralPath $reqPath).LastWriteTimeUtc.ToString("o")

    $needsInstall = $false
    if (-not (Test-Path -LiteralPath $stampPath)) {
        $needsInstall = $true
    } else {
        try {
            $stamp = Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json
            if (-not ($stamp.PSObject.Properties.Name -contains "requirements_mtime_utc")) {
                $needsInstall = $true
            } elseif ($stamp.requirements_mtime_utc -ne $requirementsMtimeUtc) {
                $needsInstall = $true
            }
        } catch {
            $needsInstall = $true
        }
    }

    if (-not $needsInstall) { return }

    Write-Host "Installing workroot requirements..."
    Invoke-WorkrootNative -Exe "python" -Args @("-m","pip","install","--upgrade","pip")
    if ($LASTEXITCODE -ne 0) { throw "pip failed during upgrade." }
    Invoke-WorkrootNative -Exe "python" -Args @("-m","pip","install","-r",$reqPath)
    if ($LASTEXITCODE -ne 0) { throw "pip failed installing workroot requirements." }

    $stampObj = [pscustomobject]@{
        requirements_mtime_utc = $requirementsMtimeUtc
        installed_at_utc = ([DateTime]::UtcNow.ToString("o"))
    }
    $stampObj | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stampPath -Encoding UTF8
    Write-Host "Workroot requirements installed."
}

function Ensure-EditableRepoInstall {
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [Parameter(Mandatory = $true)][string]$Workroot,
        [switch]$Skip,
        [switch]$Force
    )

    if ($Skip) { return }

    $pyproject = Join-Path $RepoPath "pyproject.toml"
    if (-not (Test-Path -LiteralPath $pyproject)) {
        # Not an installable package (in the modern, standard sense). Do nothing.
        return
    }

    $reqPath = Join-Path $Workroot "requirements.txt"
    $hasReq = Test-Path -LiteralPath $reqPath

    # Stamp lives inside the venv so it resets naturally if the venv is recreated.
    $stampPath = Join-Path $Workroot ".venv\.workroot_repo_editable_stamp.json"

    # Get current pyproject "version" via LastWriteTimeUtc (good enough for “did deps/metadata change?”).
    $pyprojectMtimeUtc = (Get-Item -LiteralPath $pyproject).LastWriteTimeUtc.ToString("o")
    $requirementsMtimeUtc = $null
    if ($hasReq) {
        $requirementsMtimeUtc = (Get-Item -LiteralPath $reqPath).LastWriteTimeUtc.ToString("o")
    }
    $installMode = if ($hasReq) { "editable_no_deps" } else { "editable_with_deps" }

    $needsInstall = $Force

    if (-not $needsInstall) {
        if (-not (Test-Path -LiteralPath $stampPath)) {
            $needsInstall = $true
        } else {
            try {
                $stamp = Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json
                $requiredFields = @("repo_path","pyproject_mtime_utc","requirements_present","requirements_mtime_utc","install_mode")
                foreach ($field in $requiredFields) {
                    if (-not ($stamp.PSObject.Properties.Name -contains $field)) {
                        $needsInstall = $true
                        break
                    }
                }
                if (-not $needsInstall) {
                    if ($stamp.repo_path -ne $RepoPath) { $needsInstall = $true }
                    elseif ($stamp.pyproject_mtime_utc -ne $pyprojectMtimeUtc) { $needsInstall = $true }
                    elseif ([bool]$stamp.requirements_present -ne $hasReq) { $needsInstall = $true }
                    elseif ($hasReq -and $stamp.requirements_mtime_utc -ne $requirementsMtimeUtc) { $needsInstall = $true }
                    elseif ($stamp.install_mode -ne $installMode) { $needsInstall = $true }
                }
            } catch {
                # Corrupt/old stamp? Just reinstall.
                $needsInstall = $true
            }
        }
    }

    if (-not $needsInstall) {
        Write-Host "Repo editable install: OK (stamp present, unchanged)"
        return
    }

    if ($hasReq) {
        Write-Host "Repo editable install: running pip install -e --no-deps ..."
        Invoke-WorkrootNative -Exe "python" -Args @("-m","pip","install","-e",$RepoPath,"--no-deps")
    } else {
        Write-Host "Repo editable install: running pip install -e ..."
        Invoke-WorkrootNative -Exe "python" -Args @("-m","pip","install","-e",$RepoPath)
    }
    if ($LASTEXITCODE -ne 0) { throw "pip install -e failed for repo: $RepoPath" }

    $stampObj = [pscustomobject]@{
        repo_path = $RepoPath
        pyproject_mtime_utc = $pyprojectMtimeUtc
        requirements_present = $hasReq
        requirements_mtime_utc = $requirementsMtimeUtc
        install_mode = $installMode
        installed_at_utc = ([DateTime]::UtcNow.ToString("o"))
    }
    $stampObj | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stampPath -Encoding UTF8
    Write-Host "Repo editable install: DONE (stamp updated)"
}

$venvDir = Join-Path $Workroot ".venv"
$activate = Join-Path $Workroot ".venv\Scripts\Activate.ps1"

if (-not (Test-Path -LiteralPath $activate)) {
    Write-Host "No venv found. Creating .venv in workroot..."
    Invoke-WorkrootNative -Exe "py" -Args @("-m","venv",$venvDir)
}

. $activate

$env:PYTHONDONTWRITEBYTECODE = "1"
Ensure-WorkrootRequirementsInstalled -Workroot $Workroot

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

$env:REPO = $RepoPath
Ensure-EditableRepoInstall -RepoPath $env:REPO -Workroot $Workroot

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

