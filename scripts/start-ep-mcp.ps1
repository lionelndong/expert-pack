[CmdletBinding()]
param(
    [string]$ConfigPath,
    [switch]$AllowRemoteEmbedding
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$server = Join-Path $repositoryRoot ".venv\Scripts\ep-mcp.exe"
$localConfig = Join-Path $repositoryRoot "config\ep-mcp.local.yaml"
$defaultConfig = Join-Path $repositoryRoot "config\ep-mcp.dev.yaml"

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $config = if (Test-Path -LiteralPath $localConfig) { $localConfig } else { $defaultConfig }
} else {
    $config = $ConfigPath
}

if (-not (Test-Path -LiteralPath $server)) {
    throw "EP MCP is not installed. Run .\.venv\Scripts\python.exe -m pip install -e .\runtime\ep-mcp[dev] first."
}

if (-not (Test-Path -LiteralPath $config)) {
    throw "EP MCP configuration was not found: $config"
}

$config = (Resolve-Path -LiteralPath $config).Path
$usingPrivateConfig = (Test-Path -LiteralPath $localConfig) -and (
    $config -ieq (Resolve-Path -LiteralPath $localConfig).Path
)

if ($usingPrivateConfig -and -not $AllowRemoteEmbedding) {
    throw @"
The selected private MCP configuration will send approved pack text to its configured
embedding provider during first-time indexing. Review and approve that data flow first,
then rerun with -AllowRemoteEmbedding. This script does not send data until the server starts.
"@
}

$env:PYTHONIOENCODING = "utf-8"
& $server serve --config $config
