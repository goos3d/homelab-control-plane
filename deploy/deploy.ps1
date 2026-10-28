[CmdletBinding()]
param(
    [string]$Branch = 'main',
    [string]$SshTarget = $env:CONTROL_PLANE_DEPLOY_SSH_TARGET,
    [string]$RemoteRepository = $env:CONTROL_PLANE_DEPLOY_REPO,
    [string]$RemoteUser = $env:CONTROL_PLANE_DEPLOY_USER,
    [string]$RemoteService = 'control-plane-api.service',
    [string]$HealthUrl = 'http://127.0.0.1:8000/health',
    [string]$RemoteStateDirectory = '.local/state/control-plane/deployments',
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$remoteScriptSource = Join-Path $PSScriptRoot 'remote-deploy.sh'

function Invoke-Checked {
    param(
        [Parameter(Mandatory)]
        [scriptblock]$Command,
        [string]$FailureMessage = 'Command failed.'
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Exit code: $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Local Python environment is missing: $python"
}

if (-not (Test-Path -LiteralPath $remoteScriptSource)) {
    throw "Remote deployment helper is missing: $remoteScriptSource"
}

& git -C $repo check-ref-format --branch $Branch
if ($LASTEXITCODE -ne 0) {
    throw "Invalid deployment branch: '$Branch'."
}

$branch = (& git -C $repo branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -ne $Branch) {
    throw "Deployments must be run from local '$Branch'; current branch is '$branch'."
}

$status = @(& git -C $repo status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to read local Git status.'
}
if ($status.Count -ne 0) {
    throw "Local working tree is not clean:`n$($status -join "`n")"
}

$head = (& git -C $repo rev-parse HEAD).Trim()
$originBranch = (& git -C $repo rev-parse "refs/remotes/origin/$Branch").Trim()
if ($LASTEXITCODE -ne 0 -or $head -ne $originBranch) {
    throw "Local HEAD ($head) must exactly match origin/$Branch ($originBranch)."
}

$productionPush = (& git -C $repo remote get-url --push production).Trim()
if ($LASTEXITCODE -ne 0 -or $productionPush -ne 'disabled://production') {
    throw "The production push guard is missing. Expected disabled://production; found '$productionPush'."
}

Write-Host "Testing reviewed commit $head ..."
Invoke-Checked -FailureMessage 'Tests failed; deployment stopped.' -Command {
    & $python -m pytest -q -c (Join-Path $repo 'pytest.ini') (Join-Path $repo 'tests')
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("control-plane-deploy-" + [guid]::NewGuid().ToString('N'))
$bundle = Join-Path $tempRoot 'control-plane.bundle'
$remoteScript = Join-Path $tempRoot 'remote-deploy.sh'
$shortHead = $head.Substring(0, 12)
$remoteDirectory = "~/$RemoteStateDirectory"
$remoteBundle = "$remoteDirectory/$head.bundle"
$remoteHelper = "$remoteDirectory/remote-deploy-$head.sh"

New-Item -ItemType Directory -Path $tempRoot | Out-Null

try {
    Invoke-Checked -FailureMessage 'Unable to create deployment bundle.' -Command {
        & git -C $repo bundle create $bundle "refs/heads/$Branch"
    }
    Invoke-Checked -FailureMessage 'Deployment bundle verification failed.' -Command {
        & git -C $repo bundle verify $bundle
    }

    # Ensure the helper is transferred with Unix line endings regardless of
    # the developer machine's Git configuration.
    $helperContent = [System.IO.File]::ReadAllText($remoteScriptSource)
    $helperContent = $helperContent.Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText(
        $remoteScript,
        $helperContent,
        [System.Text.UTF8Encoding]::new($false)
    )

    if (-not $Deploy) {
        Write-Host ''
        Write-Host "Preflight passed for $head."
        Write-Host 'No production files were changed.'
        Write-Host 'Run this command when ready to enter the production approval step:'
        Write-Host "  .\deploy\deploy.ps1 -Deploy"
        exit 0
    }

    if ($SshTarget -notmatch '^[A-Za-z0-9._@:-]+$' -or
        $Branch -notmatch '^[A-Za-z0-9._/-]+$' -or
        $RemoteRepository -notmatch '^/[A-Za-z0-9._/-]+$' -or
        $RemoteUser -notmatch '^[A-Za-z_][A-Za-z0-9_.-]*$' -or
        $RemoteService -notmatch '^[A-Za-z0-9_.@-]+$' -or
        $HealthUrl -notmatch '^http://(127\.0\.0\.1|localhost):[0-9]{1,5}/health$' -or
        $RemoteStateDirectory -notmatch '^[A-Za-z0-9._/-]+$' -or
        $RemoteStateDirectory.StartsWith('/') -or
        $RemoteStateDirectory -match '(^|/)\.\.?(/|$)') {
        throw 'Deployment settings are missing or contain unsupported characters.'
    }

    Write-Host ''
    Write-Host "Production currently serves the Control Plane at $SshTarget."
    Write-Host "This will stage and activate exact commit $head, restart the user service,"
    Write-Host 'and automatically return to the previous commit if health verification fails.'
    $confirmation = Read-Host "Type DEPLOY $shortHead to approve"
    if ($confirmation -cne "DEPLOY $shortHead") {
        throw 'Production deployment was not approved.'
    }

    Invoke-Checked -FailureMessage 'Unable to create the remote staging directory.' -Command {
        & ssh $SshTarget "mkdir -p $remoteDirectory"
    }
    Invoke-Checked -FailureMessage 'Unable to transfer the deployment bundle.' -Command {
        & scp $bundle "${SshTarget}:$remoteBundle"
    }
    Invoke-Checked -FailureMessage 'Unable to transfer the remote deployment helper.' -Command {
        & scp $remoteScript "${SshTarget}:$remoteHelper"
    }
    Invoke-Checked -FailureMessage 'Production deployment failed. Review the remote output above.' -Command {
        & ssh $SshTarget "bash $remoteHelper $remoteBundle $head $RemoteRepository $Branch $RemoteService $HealthUrl $RemoteStateDirectory $RemoteUser"
    }

    Write-Host ''
    Write-Host "Production deployment completed successfully at $head."
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
