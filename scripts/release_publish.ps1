param(
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Tool {
    param(
        [string]$Name
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required tool was not found in PATH: $Name"
    }
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )
    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git command failed: git $($Args -join ' ')"
    }
}

function Get-GitOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )
    $output = & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git command failed: git $($Args -join ' ')"
    }
    return ($output -join "`n").Trim()
}

function Get-GhOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )
    $output = & gh @Args
    if ($LASTEXITCODE -ne 0) {
        throw "gh command failed: gh $($Args -join ' ')"
    }
    return ($output -join "`n").Trim()
}

function Test-IsSksePath {
    param([string]$Path)
    return $Path -match '^(src/|cmake/|config/|CMakeLists\.txt$|CMakePresets\.json$|vcpkg\.json$|vcpkg-configuration\.json$)'
}

function Test-IsMo2Path {
    param([string]$Path)
    return $Path -match '^MO2 Plugin/'
}

function Get-UniqueSubjects {
    param(
        [string]$Range,
        [string[]]$Pathspec
    )
    $gitLogArgs = @("log", "--pretty=format:%s")
    if ($Range) {
        $gitLogArgs += $Range
    }
    $gitLogArgs += "--"
    $gitLogArgs += $Pathspec
    $raw = Get-GitOutput -Args $gitLogArgs
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @()
    }
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    $result = [System.Collections.Generic.List[string]]::new()
    foreach ($line in ($raw -split "`n")) {
        $subject = $line.Trim()
        if (-not $subject) {
            continue
        }
        if ($seen.Add($subject)) {
            $result.Add($subject) | Out-Null
        }
        if ($result.Count -ge 8) {
            break
        }
    }
    return $result
}

Invoke-Tool -Name "git"
Invoke-Tool -Name "gh"

$repoRoot = Get-GitOutput -Args @("rev-parse", "--show-toplevel")
if (-not $repoRoot) {
    throw "Could not determine git repository root."
}

Set-Location $repoRoot

$status = Get-GitOutput -Args @("status", "--porcelain")
if ($status) {
    throw "Working tree is not clean. Commit or stash changes before publishing a release."
}

Get-GhOutput -Args @("auth", "status")

$tagListRaw = Get-GitOutput -Args @("tag", "--list", "RAPID-v*", "--sort=-v:refname")
$previousTag = $null
if ($tagListRaw) {
    $previousTag = ($tagListRaw -split "`n")[0].Trim()
}

$changedFiles = @()
if ($previousTag) {
    $diffRaw = Get-GitOutput -Args @("diff", "--name-only", "$previousTag..HEAD")
    if ($diffRaw) {
        $changedFiles = $diffRaw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
} else {
    $allTracked = Get-GitOutput -Args @("ls-files")
    if ($allTracked) {
        $changedFiles = $allTracked -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
}

$skseChanged = $false
$mo2Changed = $false
foreach ($path in $changedFiles) {
    if (Test-IsSksePath -Path $path) {
        $skseChanged = $true
    }
    if (Test-IsMo2Path -Path $path) {
        $mo2Changed = $true
    }
}

if (-not $skseChanged -and -not $mo2Changed) {
    Write-Host "No SKSE or MO2 plugin changes detected since the previous release tag. Nothing to publish."
    exit 0
}

$cmakePath = Join-Path $repoRoot "CMakeLists.txt"
$mo2Path = Join-Path $repoRoot "MO2 Plugin/RAPID.py"

$cmakeContent = Get-Content -Path $cmakePath -Raw
$projectMatch = [regex]::Match($cmakeContent, 'project\((?<name>[A-Za-z0-9_\-]+)\s+VERSION\s+(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)')
if (-not $projectMatch.Success) {
    throw "Could not parse project version from CMakeLists.txt"
}

$projectName = $projectMatch.Groups["name"].Value
$major = [int]$projectMatch.Groups["major"].Value
$minor = [int]$projectMatch.Groups["minor"].Value
$patch = [int]$projectMatch.Groups["patch"].Value
$nextPatch = $patch + 1
$nextVersion = "$major.$minor.$nextPatch"
$tagName = "RAPID-v$nextVersion"

if ($previousTag -and $previousTag -eq $tagName) {
    throw "Next tag would collide with existing tag: $tagName"
}

$updatedCmake = [regex]::Replace(
    $cmakeContent,
    'project\((?<name>[A-Za-z0-9_\-]+)\s+VERSION\s+\d+\.\d+\.\d+',
    { param($m) "project($($m.Groups['name'].Value) VERSION $nextVersion" },
    1
)
if ($updatedCmake -eq $cmakeContent) {
    throw "Failed to update version in CMakeLists.txt"
}

$mo2Content = Get-Content -Path $mo2Path -Raw
$versionPattern = 'mobase\.VersionInfo\(\d+,\s*\d+,\s*\d+,\s*mobase\.ReleaseType\.FINAL\)'
$mo2Matches = [regex]::Matches($mo2Content, $versionPattern)
if ($mo2Matches.Count -eq 0) {
    throw "Failed to find MO2 VersionInfo entries in RAPID.py"
}
$updatedMo2 = [regex]::Replace(
    $mo2Content,
    $versionPattern,
    "mobase.VersionInfo($major, $minor, $nextPatch, mobase.ReleaseType.FINAL)"
)

$releaseRoot = Join-Path $repoRoot "release"
$releaseDir = Join-Path $releaseRoot $tagName

$dllPath = Join-Path $repoRoot "build/release-msvc/$projectName.dll"
if (-not (Test-Path -LiteralPath $dllPath)) {
    throw "Release DLL not found: $dllPath. Build release first."
}

$configPath = Join-Path $repoRoot "config"
$mo2PluginPath = Join-Path $repoRoot "MO2 Plugin"

$zipAssets = [System.Collections.Generic.List[string]]::new()

if ($skseChanged) {
    $skseZip = Join-Path $releaseDir "RAPID-SKSE-v$nextVersion.zip"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
        $skseStage = Join-Path $releaseDir "_stage_skse"
        if (Test-Path $skseStage) {
            Remove-Item -Recurse -Force $skseStage
        }
        $pluginsStage = Join-Path $skseStage "SKSE/Plugins"
        New-Item -ItemType Directory -Path $pluginsStage -Force | Out-Null
        Copy-Item -LiteralPath $dllPath -Destination $pluginsStage -Force
        if (Test-Path -LiteralPath $configPath) {
            $rapidConfigTarget = Join-Path $pluginsStage "RAPID"
            New-Item -ItemType Directory -Path $rapidConfigTarget -Force | Out-Null
            Copy-Item -Path (Join-Path $configPath "*") -Destination $rapidConfigTarget -Recurse -Force
        }
        if (Test-Path $skseZip) {
            Remove-Item -Force $skseZip
        }
        Compress-Archive -Path (Join-Path $skseStage "SKSE") -DestinationPath $skseZip -CompressionLevel Optimal
        Remove-Item -Recurse -Force $skseStage
    }
    $zipAssets.Add($skseZip) | Out-Null
}

if ($mo2Changed) {
    $mo2Zip = Join-Path $releaseDir "RAPID-MO2-v$nextVersion.zip"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
        $mo2Stage = Join-Path $releaseDir "_stage_mo2"
        if (Test-Path $mo2Stage) {
            Remove-Item -Recurse -Force $mo2Stage
        }
        $pluginsRoot = Join-Path $mo2Stage "plugins"
        New-Item -ItemType Directory -Path $pluginsRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $mo2PluginPath "*") -Destination $pluginsRoot -Recurse -Force
        if (Test-Path $mo2Zip) {
            Remove-Item -Force $mo2Zip
        }
        Compress-Archive -Path $pluginsRoot -DestinationPath $mo2Zip -CompressionLevel Optimal
        Remove-Item -Recurse -Force $mo2Stage
    }
    $zipAssets.Add($mo2Zip) | Out-Null
}

$rangeForLog = $null
if ($previousTag) {
    $rangeForLog = "$previousTag..HEAD"
}

$skseSubjects = @()
if ($skseChanged) {
    $skseSubjects = Get-UniqueSubjects -Range $rangeForLog -Pathspec @(
        "src",
        "cmake",
        "config",
        "CMakeLists.txt",
        "CMakePresets.json",
        "vcpkg.json",
        "vcpkg-configuration.json"
    )
}

$mo2Subjects = @()
if ($mo2Changed) {
    $mo2Subjects = Get-UniqueSubjects -Range $rangeForLog -Pathspec @("MO2 Plugin")
}

$changelogLines = [System.Collections.Generic.List[string]]::new()
$changelogLines.Add("Release $tagName") | Out-Null
$changelogLines.Add("") | Out-Null
if ($previousTag) {
    $changelogLines.Add("Changes since $previousTag.") | Out-Null
} else {
    $changelogLines.Add("Initial tagged release from repository history.") | Out-Null
}
$changelogLines.Add("") | Out-Null
$changelogLines.Add("## SKSE Plugin Changes") | Out-Null
if ($skseChanged -and $skseSubjects.Count -gt 0) {
    foreach ($subject in $skseSubjects) {
        $changelogLines.Add("- $subject") | Out-Null
    }
} elseif ($skseChanged) {
    $changelogLines.Add("- Updated SKSE plugin files.") | Out-Null
} else {
    $changelogLines.Add("- No SKSE plugin changes.") | Out-Null
}
$changelogLines.Add("") | Out-Null
$changelogLines.Add("## MO2 Plugin Changes") | Out-Null
if ($mo2Changed -and $mo2Subjects.Count -gt 0) {
    foreach ($subject in $mo2Subjects) {
        $changelogLines.Add("- $subject") | Out-Null
    }
} elseif ($mo2Changed) {
    $changelogLines.Add("- Updated MO2 plugin files.") | Out-Null
} else {
    $changelogLines.Add("- No MO2 plugin changes.") | Out-Null
}

$changelog = $changelogLines -join "`n"
$changelogFile = Join-Path $releaseDir "release-notes-$nextVersion.md"

if ($DryRun) {
    Write-Host "Dry run complete."
    Write-Host "Next version: $nextVersion"
    Write-Host "Would update: CMakeLists.txt, MO2 Plugin/RAPID.py"
    Write-Host "Would create tag: $tagName"
    Write-Host "Release notes: $changelogFile"
    Write-Host ""
    Write-Host $changelog
    Write-Host ""
    Write-Host "Zip assets:"
    foreach ($zip in $zipAssets) {
        Write-Host " - $zip"
    }
    exit 0
}

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
Set-Content -Path $changelogFile -Value $changelog -NoNewline -Encoding UTF8

Set-Content -Path $cmakePath -Value $updatedCmake -NoNewline -Encoding UTF8
Set-Content -Path $mo2Path -Value $updatedMo2 -NoNewline -Encoding UTF8

Invoke-Git -Args @("add", "--", "CMakeLists.txt", "MO2 Plugin/RAPID.py")
Invoke-Git -Args @("commit", "-m", "Release $tagName")
Invoke-Git -Args @("push")

Invoke-Git -Args @("tag", "-a", $tagName, "-F", $changelogFile)
Invoke-Git -Args @("push", "origin", $tagName)

$releaseArgs = @("release", "create", $tagName, "--title", "RAPID v$nextVersion", "--notes-from-tag")
foreach ($asset in $zipAssets) {
    $releaseArgs += $asset
}
Get-GhOutput -Args $releaseArgs | Out-Null

Write-Host "Published $tagName"
Write-Host "Release notes file: $changelogFile"
foreach ($zip in $zipAssets) {
    Write-Host "Uploaded asset: $zip"
}
