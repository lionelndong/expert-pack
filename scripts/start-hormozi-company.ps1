[CmdletBinding()]
param(
    [string]$ConfigPath,
    [switch]$GatewayReady,
    [switch]$AllowRemoteEmbedding
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$server = Join-Path $repositoryRoot ".venv\Scripts\ep-mcp.exe"
$defaultConfig = Join-Path $repositoryRoot "config\ep-mcp.company.example.yaml"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Workspace Python runtime was not found: $python"
}
if (-not (Test-Path -LiteralPath $server)) {
    throw "EP MCP is not installed. Install runtime/ep-mcp before starting the company service."
}
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = $defaultConfig
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Company MCP configuration was not found: $ConfigPath"
}
if (-not $GatewayReady) {
    throw "Refusing to bind beyond loopback until the company gateway, TLS, identity policy, and audit sink are confirmed. Rerun with -GatewayReady after that external check."
}
if ([string]::IsNullOrWhiteSpace($env:EP_MCP_KEY_ALEX_HORMOZI_BRAIN)) {
    throw "EP_MCP_KEY_ALEX_HORMOZI_BRAIN is required; load it from the company secret manager."
}
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    throw "OPENAI_API_KEY is required for the configured direct OpenAI embedding provider."
}
if (-not $AllowRemoteEmbedding) {
    throw "The company service sends approved pack text to OpenAI for embeddings. Rerun with -AllowRemoteEmbedding after confirming that data flow."
}

# Run non-secret configuration checks before binding. The readiness report may
# still show the gateway as externally staged; -GatewayReady is the explicit
# operator acknowledgement for that one external control.
$env:PYTHONIOENCODING = "utf-8"
& $python (Join-Path $repositoryRoot "tools\hormozi-brain\company_readiness.py") --config (Resolve-Path -LiteralPath $ConfigPath).Path
if ($LASTEXITCODE -ne 0) {
    throw "Company MCP readiness checks failed. See the generated readiness report before retrying."
}

& $server serve --config (Resolve-Path -LiteralPath $ConfigPath).Path
