[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "The workspace Python runtime was not found: $python"
}

$env:PYTHONIOENCODING = "utf-8"
$steps = @(
    @{ Name = "build"; Args = @("tools/hormozi-brain/build_brain.py") },
    @{ Name = "skill validation"; Args = @("tools/hormozi-brain/validate_skills.py") },
    @{ Name = "embedding estimate"; Args = @("tools/hormozi-brain/estimate_embeddings.py", "--pack", "private-input/packs/alex-hormozi-brain-v1") },
    @{ Name = "quality benchmark"; Args = @("tools/hormozi-brain/quality_check.py", "--pack", "private-input/packs/alex-hormozi-brain-v1") },
    @{ Name = "company readiness"; Args = @("tools/hormozi-brain/company_readiness.py") },
    @{ Name = "acceptance audit"; Args = @("tools/hormozi-brain/acceptance_audit.py") }
)

Push-Location $repositoryRoot
try {
    foreach ($step in $steps) {
        Write-Host "==> $($step.Name)"
        & $python @($step.Args)
        if ($LASTEXITCODE -ne 0) {
            throw "Step failed: $($step.Name)"
        }
    }
} finally {
    Pop-Location
}

Write-Host "Hormozi brain rebuild and acceptance artifacts are ready."
