$ErrorActionPreference = 'Stop'

function Assert-ExitCode([string] $Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

# Install for the current user; GitHub-hosted Windows runners are elevated.
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
$installer = Join-Path $env:RUNNER_TEMP 'install-scoop.ps1'
Invoke-RestMethod -Uri 'https://get.scoop.sh' -OutFile $installer
& $installer -RunAsAdmin
Assert-ExitCode 'Scoop installation'
$env:PATH = "$(Join-Path $env:USERPROFILE 'scoop\shims');$env:PATH"

scoop bucket add dutchman-cad $env:GITHUB_WORKSPACE
Assert-ExitCode 'Adding the bucket'
try {
    # Install the actual workspace files, including manifests downloaded from
    # the bump job, rather than the bucket clone's committed copies.
    foreach ($app in @('dutchman', 'dutchman-pilot')) {
        $manifest = Join-Path $env:GITHUB_WORKSPACE "bucket\$app.json"
        scoop install $manifest
        Assert-ExitCode "Installing $app"
    }
    dutchman --version
    Assert-ExitCode 'dutchman --version'
    dutchman-pilot --version
    Assert-ExitCode 'dutchman-pilot --version'
    foreach ($command in @('dutchman-mcp', 'dutchman-pilot-eval')) {
        Get-Command $command -ErrorAction Stop | Out-Null
    }
    $shortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Scoop Apps\Dutchman Convert.lnk'
    if (-not (Test-Path $shortcut)) {
        throw "Missing shortcut: $shortcut"
    }
} finally {
    foreach ($app in @('dutchman-pilot', 'dutchman')) {
        if (Test-Path (Join-Path $env:USERPROFILE "scoop\apps\$app")) {
            scoop uninstall $app
            Assert-ExitCode "Uninstalling $app"
        }
    }
}
