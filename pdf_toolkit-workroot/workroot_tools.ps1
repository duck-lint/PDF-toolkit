Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:Workroot = $PSScriptRoot
$script:ManifestsDir = Join-Path $script:Workroot "_workroot_manifests"

function global:Get-RelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$FullPath
    )
    $base = $BasePath.TrimEnd('\','/')
    if ($FullPath.StartsWith($base, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $FullPath.Substring($base.Length).TrimStart('\','/')
    }
    return $FullPath
}

function global:Get-WorkrootSnapshot {
    param([Parameter(Mandatory = $true)][string]$Root)

    $rootNorm = $Root.TrimEnd('\','/')
    $excludeRoots = @(
        (Join-Path $rootNorm ".venv"),
        (Join-Path $rootNorm ".git"),
        (Join-Path $rootNorm "_workroot_manifests"),
        (Join-Path $rootNorm "manifests")
    )

    $entries = @()
    $items = Get-ChildItem -LiteralPath $rootNorm -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
        $full = $_.FullName
        $skip = $false
        foreach ($ex in $excludeRoots) {
            if ($full.StartsWith($ex, [System.StringComparison]::OrdinalIgnoreCase)) { $skip = $true; break }
        }
        -not $skip
    }

    foreach ($item in $items) {
        $entries += [ordered]@{
            relative_path = Get-RelativePath -BasePath $rootNorm -FullPath $item.FullName
            size_bytes    = $item.Length
            mtime_utc     = $item.LastWriteTimeUtc.ToString("o")
        }
    }

    return $entries
}

function global:Get-PythonInfo {
    try {
        $json = & python -c "import json,sys; print(json.dumps({'executable':sys.executable,'version':sys.version}))"
        if ($json) { return ($json | ConvertFrom-Json) }
    } catch {
        return $null
    }
    return $null
}

function global:Get-GitInfo {
    param([string]$RepoPath)
    try {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $null }
        if (-not $RepoPath) { return $null }
        $inside = & git -C $RepoPath rev-parse --is-inside-work-tree 2>$null
        if ($inside -ne "true") { return $null }

        $branch = (& git -C $RepoPath rev-parse --abbrev-ref HEAD 2>$null).Trim()
        $commit = (& git -C $RepoPath rev-parse HEAD 2>$null).Trim()
        $status = (& git -C $RepoPath status --porcelain 2>$null)
        $dirty = $false
        if ($status) { $dirty = $true }

        return [ordered]@{
            branch   = $branch
            commit   = $commit
            is_dirty = $dirty
        }
    } catch {
        return $null
    }
}

function global:Quote-CommandArg {
    param([string]$Arg)
    if ($null -eq $Arg) { return "" }
    if ($Arg -match '[\s"''`$()\[\]{};|&<>]') {
        return '"' + ($Arg -replace '"', '""') + '"'
    }
    return $Arg
}

function global:Format-CommandLine {
    param([string]$Exe, [string[]]$CmdArgs)
    $parts = @()
    if ($Exe) { $parts += $Exe }
    if ($CmdArgs) { $parts += $CmdArgs }
    $quoted = $parts | ForEach-Object { Quote-CommandArg -Arg $_ }
    return ($quoted -join ' ')
}

function global:Get-OutputEncoding {
    param([string]$EncodingName)
    $name = $EncodingName
    if ([string]::IsNullOrWhiteSpace($name)) { $name = "utf8" }
    try {
        return [System.Text.Encoding]::GetEncoding(
            $name,
            [System.Text.EncoderFallback]::ReplacementFallback,
            [System.Text.DecoderFallback]::ReplacementFallback
        )
    } catch {
        return [System.Text.Encoding]::UTF8
    }
}

function global:Read-OutputFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$MaxBytes,
        [Parameter(Mandatory = $true)][string]$EncodingName
    )

    $result = [ordered]@{
        text      = ""
        truncated = $false
        error     = $null
    }

    try {
        $fileInfo = Get-Item -LiteralPath $Path -ErrorAction Stop
        $max = [Math]::Max(0, $MaxBytes)
        if ($fileInfo.Length -gt $max) { $result.truncated = $true }

        $toRead = 0
        if ($max -gt 0) { $toRead = [int][Math]::Min($fileInfo.Length, $max) }
        $bytes = [byte[]]@()
        if ($toRead -gt 0) {
            $bytes = New-Object 'System.Byte[]' $toRead
            $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            try {
                $read = $fs.Read($bytes, 0, $toRead)
                if ($read -lt $toRead) {
                    if ($read -gt 0) { $bytes = $bytes[0..($read - 1)] } else { $bytes = [byte[]]@() }
                }
            } finally {
                $fs.Dispose()
            }
        }

        $encoding = Get-OutputEncoding -EncodingName $EncodingName
        if ($bytes.Length -gt 0) {
            $result.text = $encoding.GetString($bytes)
        } else {
            $result.text = ""
        }
    } catch {
        $result.error = $_.Exception.Message
    }

    return $result
}

function global:Invoke-WorkrootCommand {
    $dryRun = $false
    $snapshot = $false
    $includeUser = $false
    $captureOutput = $false
    $rawNativeStderr = $false
    $maxOutputBytes = 65536
    $outputEncoding = if ($PSVersionTable.PSEdition -eq "Desktop") { "unicode" } else { "utf8" }
    $all = @()
    if ($args) { $all = @($args) }

    if ($all.Count -eq 0) {
        throw "Usage: wr <command> [args...]"
    }

    if ($all[0] -eq "--") {
        if ($all.Count -gt 1) { $all = $all[1..($all.Count - 1)] } else { $all = @() }
    } else {
        $i = 0
        :parse while ($i -lt $all.Count) {
            $a = $all[$i]
            switch ($a.ToLowerInvariant()) {
                "-dryrun"   { $dryRun = $true; $i++; continue parse }
                "-snapshot" { $snapshot = $true; $i++; continue parse }
                "-includeuser" { $includeUser = $true; $i++; continue parse }
                "-captureoutput" { $captureOutput = $true; $i++; continue parse }
                "-nocapture" { $captureOutput = $false; $i++; continue parse }
                "-rawnativestderr" { $rawNativeStderr = $true; $captureOutput = $true; $i++; continue parse }
                "-maxoutputbytes" {
                    if ($i + 1 -ge $all.Count) { throw "Missing value for -MaxOutputBytes" }
                    $maxOutputBytes = [int]$all[$i + 1]
                    $i += 2
                    continue parse
                }
                "-outputencoding" {
                    if ($i + 1 -ge $all.Count) { throw "Missing value for -OutputEncoding" }
                    $outputEncoding = [string]$all[$i + 1]
                    $i += 2
                    continue parse
                }
                "--" {
                    $i++
                    break parse
                }
                default     { break parse }
            }
        }
        if ($i -gt 0) {
            if ($i -lt $all.Count) { $all = $all[$i..($all.Count - 1)] } else { $all = @() }
        }
    }

    if ($rawNativeStderr -and -not $captureOutput) {
        Write-Host "Note: -RawNativeStderr requires -CaptureOutput; enabling output capture."
        $captureOutput = $true
    }

    if ($all.Count -eq 0) {
        throw "Usage: wr <command> [args...]"
    }

    $exe = $all[0]
    $cmdArgs = @()
    if ($all.Count -gt 1) { $cmdArgs = $all[1..($all.Count - 1)] }

    $workroot = $script:Workroot
    if (-not $workroot) { $workroot = (Get-Location).Path }
    $repoPath = $env:REPO

    if (-not (Test-Path -LiteralPath $workroot)) {
        throw "Workroot not found: '$workroot'"
    }

    $runId = "{0}_{1}" -f ([DateTime]::UtcNow.ToString("yyyyMMdd_HHmmss")), ([Guid]::NewGuid().ToString("N").Substring(0,6))
    $manifestPath = Join-Path $script:ManifestsDir ("run_{0}.json" -f $runId)

    if ($dryRun) {
        Write-Host "[dry-run] workroot: $workroot"
        Write-Host "[dry-run] repo:     $repoPath"
        Write-Host "[dry-run] command:  $(Format-CommandLine -Exe $exe -CmdArgs $cmdArgs)"
        Write-Host "[dry-run] manifest: $manifestPath"
        if ($snapshot) { Write-Host "[dry-run] snapshot: enabled" }
        return
    }

    if (-not (Test-Path -LiteralPath $script:ManifestsDir)) {
        New-Item -ItemType Directory -Path $script:ManifestsDir -Force | Out-Null
    }

    $startUtc = [DateTime]::UtcNow
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

    $before = $null
    if ($snapshot) { $before = Get-WorkrootSnapshot -Root $workroot }

    $runError = $null
    $success = $true
    $exitCode = 0
    $outputInfo = $null
    $stdoutFile = $null
    $stderrFile = $null
    $handledExitCode = $false
    $nativeErrPrefVar = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    $nativeErrPrefOriginal = $null
    if ($nativeErrPrefVar) {
        $nativeErrPrefOriginal = $nativeErrPrefVar.Value
        $PSNativeCommandUseErrorActionPreference = $false
    }

    Push-Location $workroot
    try {
        $global:LASTEXITCODE = 0
        $oldErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            if ($captureOutput) {
                $tempDir = $env:TEMP
                if (-not $tempDir) { $tempDir = $workroot }
                $stdoutFile = Join-Path $tempDir ("wr_stdout_{0}.tmp" -f ([Guid]::NewGuid().ToString("N")))
                $stderrFile = Join-Path $tempDir ("wr_stderr_{0}.tmp" -f ([Guid]::NewGuid().ToString("N")))
                if ($rawNativeStderr) {
                    $argString = ""
                    if ($cmdArgs -and $cmdArgs.Count -gt 0) {
                        $argString = ($cmdArgs | ForEach-Object { Quote-CommandArg -Arg $_ }) -join ' '
                    }
                    $proc = Start-Process -FilePath $exe -ArgumentList $argString -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile
                    $exitCode = $proc.ExitCode
                    $success = ($exitCode -eq 0)
                    $handledExitCode = $true
                    $global:LASTEXITCODE = $exitCode
                } else {
                    & $exe @cmdArgs 1> $stdoutFile 2> $stderrFile
                }
            } else {
                & $exe @cmdArgs
            }
        } finally {
            $ErrorActionPreference = $oldErrorAction
        }
        if (-not $handledExitCode) {
            $success = $?
            if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE }
            elseif (-not $success) { $exitCode = 1 }
        }
    } catch {
        $success = $false
        $exitCode = 1
        $runError = $_.Exception.Message
    } finally {
        if ($nativeErrPrefVar) { $PSNativeCommandUseErrorActionPreference = $nativeErrPrefOriginal }
        Pop-Location
    }

    if ($captureOutput) {
        $outputErrors = @()
        $stdoutResult = $null
        $stderrResult = $null
        if ($stdoutFile) { $stdoutResult = Read-OutputFile -Path $stdoutFile -MaxBytes $maxOutputBytes -EncodingName $outputEncoding }
        if ($stderrFile) { $stderrResult = Read-OutputFile -Path $stderrFile -MaxBytes $maxOutputBytes -EncodingName $outputEncoding }

        $stdoutText = ""
        $stderrText = ""
        $truncated = $false

        if ($stdoutResult) {
            $stdoutText = $stdoutResult.text
            if ($stdoutResult.truncated) { $truncated = $true }
            if ($stdoutResult.error) { $outputErrors += ("stdout: {0}" -f $stdoutResult.error) }
        }
        if ($stderrResult) {
            $stderrText = $stderrResult.text
            if ($stderrResult.truncated) { $truncated = $true }
            if ($stderrResult.error) { $outputErrors += ("stderr: {0}" -f $stderrResult.error) }
        }

        if ($stdoutText) { $stdoutText = $stdoutText -replace "^\uFEFF","" }
        if ($stderrText) { $stderrText = $stderrText -replace "^\uFEFF","" }

        $outputInfo = [ordered]@{
            captured  = $true
            truncated = [bool]$truncated
            max_bytes = $maxOutputBytes
            stdout    = $stdoutText
            stderr    = $stderrText
        }
        if ($outputErrors.Count -gt 0) { $outputInfo["error"] = ($outputErrors -join "; ") }

        if ($stdoutText) { Write-Host -NoNewline ($stdoutText -replace "^\uFEFF","") }
        if ($stderrText) { Write-Host -NoNewline ($stderrText -replace "^\uFEFF","") }

        foreach ($path in @($stdoutFile, $stderrFile)) {
            if ($path -and (Test-Path -LiteralPath $path)) {
                Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            }
        }
    }

    $stopwatch.Stop()
    $endUtc = [DateTime]::UtcNow

    $after = $null
    $changes = $null
    if ($snapshot) {
        $after = Get-WorkrootSnapshot -Root $workroot
        $beforeIndex = @{}
        foreach ($e in $before) { $beforeIndex[$e.relative_path] = $e }
        $afterIndex = @{}
        foreach ($e in $after) { $afterIndex[$e.relative_path] = $e }

        $newFiles = @()
        $modifiedFiles = @()
        $deletedFiles = @()

        foreach ($k in $afterIndex.Keys) {
            if (-not $beforeIndex.ContainsKey($k)) {
                $newFiles += $k
            } else {
                $b = $beforeIndex[$k]
                $a = $afterIndex[$k]
                if ($a.size_bytes -ne $b.size_bytes -or $a.mtime_utc -ne $b.mtime_utc) {
                    $modifiedFiles += $k
                }
            }
        }
        foreach ($k in $beforeIndex.Keys) {
            if (-not $afterIndex.ContainsKey($k)) { $deletedFiles += $k }
        }

        $changes = [ordered]@{
            new_files      = $newFiles
            modified_files = $modifiedFiles
            deleted_files  = $deletedFiles
        }
    }

    $envInfo = [ordered]@{}
    if ($env:REPO) { $envInfo.REPO = $env:REPO }
    if ($env:PYTHONDONTWRITEBYTECODE) { $envInfo.PYTHONDONTWRITEBYTECODE = $env:PYTHONDONTWRITEBYTECODE }

    $pyInfo = Get-PythonInfo
    $gitInfo = Get-GitInfo -RepoPath $repoPath

    $psEditionValue = $null
    if ($PSVersionTable.PSEdition) { $psEditionValue = $PSVersionTable.PSEdition }

    $hostInfo = [ordered]@{
        powershell_version = $PSVersionTable.PSVersion.ToString()
        powershell_edition = $psEditionValue
        os                = [System.Environment]::OSVersion.VersionString
        machine           = $env:COMPUTERNAME
        username          = $null
    }
    if ($includeUser) { $hostInfo.username = $env:USERNAME }

    $manifest = [ordered]@{
        run_id      = $runId
        start_utc   = $startUtc.ToString("o")
        end_utc     = $endUtc.ToString("o")
        duration_ms = [int]$stopwatch.Elapsed.TotalMilliseconds
        cwd         = $workroot
        workroot    = $workroot
        repo        = $repoPath
        command     = [ordered]@{
            command = $exe
            args    = $cmdArgs
            full    = (Format-CommandLine -Exe $exe -CmdArgs $cmdArgs)
        }
        exit_code   = $exitCode
        success     = [bool]$success
        python      = $pyInfo
        git         = $gitInfo
        host        = $hostInfo
        env         = $envInfo
    }

    if ($runError) { $manifest["error"] = $runError }
    if ($outputInfo) { $manifest["output"] = $outputInfo }
    if ($snapshot) {
        $manifest["snapshot"] = [ordered]@{
            before  = $before
            after   = $after
            changes = $changes
        }
    }

    $json = $manifest | ConvertTo-Json -Depth 8
    Set-Content -LiteralPath $manifestPath -Value $json -Encoding UTF8

    Write-Host "Manifest: $manifestPath"
}

Set-Alias -Name wr -Value Invoke-WorkrootCommand -Scope Global

# Quick test (not executed):
#   .\boot.cmd bootstrap.ps1
#   wr python -c "print('hello')"
#   # Expect: _workroot_manifests\run_<run_id>.json created in the workroot
