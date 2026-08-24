[CmdletBinding()]
param(
    [switch]$RunOpenAIGates,
    [string]$RestrictedAuthorizationPath,
    [switch]$RunRestrictedOcr
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "The workspace Python runtime was not found: $python"
}

$env:PYTHONIOENCODING = "utf-8"

function Invoke-PythonStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Args
    )

    Write-Host "==> $Name"
    & $python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Name"
    }
}

function Invoke-ValidationSteps {
    Invoke-PythonStep "OCR review packet" @(
        "tools/hormozi-brain/make_ocr_review_packet.py",
        "--root", "private-input/ocr-results",
        "--output", "private-input/ocr-results/manual-review"
    )
    Invoke-PythonStep "skill validation" @("tools/hormozi-brain/validate_skills.py")
    Invoke-PythonStep "embedding estimate" @("tools/hormozi-brain/estimate_embeddings.py", "--pack", "private-input/packs/alex-hormozi-brain-v1")
    Invoke-PythonStep "quality benchmark" @("tools/hormozi-brain/quality_check.py", "--pack", "private-input/packs/alex-hormozi-brain-v1")
    Invoke-PythonStep "official transcription estimate" @(
        "tools/hormozi-brain/transcribe_official.py",
        "--video-id", "OVhNSzFSoZs",
        "--video-id", "FXzDLLdxsCk",
        "--video-id", "FKWEybUIda4",
        "--video-id", "sSEm3qJUh9s",
        "--video-id", "A4L3byKcYQg",
        "--video-id", "Gh9zWsP8JpI",
        "--catalog", "private-input/packs/alex-hormozi-brain-v1/meta/official-channel-catalog.json",
        "--output", "private-input/packs/alex-hormozi-brain-v1",
        "--metadata-cache", "private-input/official-channel-metadata-cache.json",
        "--estimate-only"
    )
    Invoke-PythonStep "decision-support contract preflight" @("tools/hormozi-brain/evaluate_decision_support.py")
    Invoke-PythonStep "company readiness" @("tools/hormozi-brain/company_readiness.py")
    Invoke-PythonStep "acceptance audit" @("tools/hormozi-brain/acceptance_audit.py")
}

$officialCaptionlessIds = @(
    "OVhNSzFSoZs", "FXzDLLdxsCk", "FKWEybUIda4",
    "sSEm3qJUh9s", "A4L3byKcYQg", "Gh9zWsP8JpI"
)

Push-Location $repositoryRoot
try {
    if ($RunRestrictedOcr -and [string]::IsNullOrWhiteSpace($RestrictedAuthorizationPath)) {
        throw "-RunRestrictedOcr requires -RestrictedAuthorizationPath"
    }
    if ($RunOpenAIGates -and [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
        throw "-RunOpenAIGates requires OPENAI_API_KEY; no media or source text was opened"
    }
    if (-not [string]::IsNullOrWhiteSpace($RestrictedAuthorizationPath)) {
        if (-not (Test-Path -LiteralPath $RestrictedAuthorizationPath)) {
            throw "Restricted authorization record not found: $RestrictedAuthorizationPath"
        }
        $restrictedArgs = @(
            "tools/hormozi-brain/process_restricted.py",
            "--authorization", (Resolve-Path -LiteralPath $RestrictedAuthorizationPath).Path
        )
        if ($RunRestrictedOcr) {
            $restrictedArgs += "--run-ocr"
        }
        Invoke-PythonStep "restricted authorization handoff" $restrictedArgs
    }

    Invoke-PythonStep "build" @("tools/hormozi-brain/build_brain.py")

    if ($RunOpenAIGates) {
        $catalog = "private-input/packs/alex-hormozi-brain-v1/meta/official-channel-catalog.json"
        foreach ($videoId in $officialCaptionlessIds) {
            Invoke-PythonStep "OpenAI transcription $videoId" @(
                "tools/hormozi-brain/transcribe_official.py",
                "--video-id", $videoId,
                "--catalog", $catalog,
                "--output", "private-input/packs/alex-hormozi-brain-v1"
            )
        }

        $audioOutput = "private-input/audio-transcriptions"
        New-Item -ItemType Directory -Force -Path $audioOutput | Out-Null
        $manifest = Get-Content "private-input/inventory/hormozi-source-manifest.json" -Raw | ConvertFrom-Json
        $seenAudioHashes = @{}
        foreach ($source in @($manifest.sources)) {
            $sourcePath = [string]$source.path
            $extension = [IO.Path]::GetExtension($sourcePath).ToLowerInvariant()
            if ($source.rights_status -eq "excluded" -or $extension -notin @(".mp3", ".wav", ".m4a", ".flac")) {
                continue
            }
            $dedupeKey = if ([string]::IsNullOrWhiteSpace([string]$source.hash)) { $sourcePath } else { [string]$source.hash }
            if ($seenAudioHashes.ContainsKey($dedupeKey)) {
                continue
            }
            $seenAudioHashes[$dedupeKey] = $true
            $stem = [IO.Path]::GetFileNameWithoutExtension($sourcePath) -replace "[^A-Za-z0-9_-]+", "-"
            Invoke-PythonStep "OpenAI audio transcription $stem" @(
                "tools/hormozi-brain/transcribe_audio.py",
                "--audio", $sourcePath,
                "--output", (Join-Path $audioOutput "$stem.json")
            )
        }
        Invoke-PythonStep "rebuild with OpenAI enrichments" @(
            "tools/hormozi-brain/build_brain.py",
            "--audio-transcriptions-dir", $audioOutput
        )
    }

    Invoke-ValidationSteps
} finally {
    Pop-Location
}

Write-Host "Hormozi brain rebuild and acceptance artifacts are ready."
