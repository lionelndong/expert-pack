[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$server = Join-Path $repositoryRoot ".venv\Scripts\ep-mcp.exe"
$config = Join-Path $repositoryRoot "config\ep-mcp.dev.yaml"

if (-not (Test-Path -LiteralPath $server)) {
    throw "EP MCP is not installed. Run .\.venv\Scripts\python.exe -m pip install -e .\runtime\ep-mcp[dev] first."
}

$env:PYTHONIOENCODING = "utf-8"
& $server serve --config $config
