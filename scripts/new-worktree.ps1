<#
.SYNOPSIS
  Create a git worktree for sw2020 in the one agreed place, with its own venv and .mcp.json.

.DESCRIPTION
  Worktrees go in a sibling folder of the main clone: <Projects>\sw2020-worktrees\<Name>.
  Each gets its own .venv (an editable install of ITS OWN src) and its own .mcp.json pointing at
  that venv, so tests and the MCP server never run the main clone's code by accident.

  Remove one when its branch is merged:
    git worktree remove <path>
    git branch -d <branch>

.EXAMPLE
  .\scripts\new-worktree.ps1 -Name shop-drawings
  .\scripts\new-worktree.ps1 -Name kb-fix -Branch fix/kb-search -Base origin/main
#>
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$Branch = "feat/$Name",
    [string]$Base = "main",
    [switch]$NoVenv
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$root = Join-Path (Split-Path $repo -Parent) "sw2020-worktrees"
$path = Join-Path $root $Name

if (Test-Path $path) { throw "$path already exists." }
New-Item -ItemType Directory -Force $root | Out-Null

# Reuse the branch if it already exists, otherwise create it from $Base.
git -C $repo show-ref --verify --quiet "refs/heads/$Branch"
if ($LASTEXITCODE -eq 0) {
    git -C $repo worktree add $path $Branch
} else {
    git -C $repo worktree add -b $Branch $path $Base
}
if ($LASTEXITCODE -ne 0) { throw "git worktree add failed." }

# .mcp.json is machine-specific and not tracked: keep it out of git status (local exclude only).
$exclude = git -C $repo rev-parse --path-format=absolute --git-path info/exclude
if (-not (Select-String -Path $exclude -Pattern '^\.mcp\.json$' -Quiet -ErrorAction SilentlyContinue)) {
    Add-Content -Path $exclude -Value ".mcp.json"
}

if (-not $NoVenv) {
    $mainPython = Join-Path $repo ".venv\Scripts\python.exe"
    $python = if (Test-Path $mainPython) { $mainPython } else { "python" }

    & $python -m venv (Join-Path $path ".venv")
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed." }

    $venvPython = Join-Path $path ".venv\Scripts\python.exe"
    Push-Location $path
    try {
        & $venvPython -m pip install -e ".[dev]"
        if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

        $where = & $venvPython -c "import solidworks_mcp; print(solidworks_mcp.__file__)"
        if (-not $where.StartsWith($path, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "solidworks_mcp imports from $where, not from the worktree."
        }
    } finally {
        Pop-Location
    }

    $mcp = @{
        mcpServers = @{
            solidworks = @{
                command = $venvPython
                args    = @("-m", "solidworks_mcp.server")
            }
        }
    } | ConvertTo-Json -Depth 5
    Set-Content -Path (Join-Path $path ".mcp.json") -Value $mcp -Encoding utf8
}

Write-Host ""
Write-Host "Worktree ready: $path (branch $Branch)"
Write-Host "Remove it when merged: git worktree remove `"$path`"; git branch -d $Branch"
