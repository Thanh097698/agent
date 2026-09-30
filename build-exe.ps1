[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [ValidateSet('32', '64')][string]$Architecture = '32',
    [string]$ISCCPath = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    [string]$SigningThumbprint,
    [switch]$AllowUnsignedForTesting,
    [switch]$PublishWeb
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Push-Location $PSScriptRoot
try {
    $pythonPath = (Resolve-Path -LiteralPath $PythonExe).Path
    $compilerPath = (Resolve-Path -LiteralPath $ISCCPath).Path
    $actualBits = 'import struct; print(struct.calcsize("P") * 8)' | & $pythonPath -
    if ($LASTEXITCODE -ne 0 -or "$actualBits".Trim() -ne $Architecture) { throw 'Python architecture does not match selected driver architecture' }
    & $pythonPath -c 'import sys; assert sys.version_info >= (3, 13); import tkinter'
    if ($LASTEXITCODE -ne 0) { throw 'Use a maintained Python 3.13+ Windows installation with Tkinter' }
    if (!$SigningThumbprint -and !$AllowUnsignedForTesting) { throw 'A code-signing certificate is required. For local testing only, pass -AllowUnsignedForTesting.' }
    if ($PublishWeb -and !$SigningThumbprint) { throw 'Unsigned builds cannot be published for users' }
    $certificate = $null
    if ($SigningThumbprint) {
        $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$SigningThumbprint"
        if (!$certificate.HasPrivateKey) { throw 'Signing certificate has no private key' }
    }
    $buildRoot = Join-Path $PSScriptRoot "build\$Architecture"
    $distRoot = Join-Path $PSScriptRoot "dist\$Architecture"
    New-Item -ItemType Directory -Path $buildRoot, $distRoot -Force | Out-Null
    $venvDir = Join-Path $buildRoot 'venv'
    if (!(Test-Path -LiteralPath "$venvDir\Scripts\python.exe")) {
        & $pythonPath -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated build environment' }
    }
    $buildPython = Join-Path $venvDir 'Scripts\python.exe'
    & $buildPython -m pip install --disable-pip-version-check -r requirements.txt -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & $buildPython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Agent tests failed' }
    $version = & $buildPython -c 'from agent_common import VERSION; print(VERSION)'
    if ($LASTEXITCODE -ne 0 -or "$version" -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid agent version' }
    $packArgs = @('-m', 'PyInstaller', '--onedir', '--windowed', '--noconfirm', '--name', 'ViDentalImagingAgent',
        '--distpath', $distRoot, '--workpath', "$buildRoot\pyinstaller", '--specpath', $buildRoot,
        '--hidden-import', 'twain', '--hidden-import', 'PIL.ImageDraw', '--hidden-import', 'pystray._win32', 'agent.py')
    & $buildPython @packArgs
    if ($LASTEXITCODE -ne 0) { throw 'Application packaging failed' }
    $applicationExe = Join-Path $distRoot 'ViDentalImagingAgent\ViDentalImagingAgent.exe'
    & $buildPython tests/smoke_packaged.py --exe $applicationExe
    if ($LASTEXITCODE -ne 0) { throw 'Packaged application smoke test failed' }
    if ($certificate) {
        $signature = Set-AuthenticodeSignature -FilePath $applicationExe -Certificate $certificate -HashAlgorithm SHA256 -TimestampServer 'http://timestamp.digicert.com'
        if ($signature.Status -ne 'Valid') { throw 'Application signing failed' }
    }
    & $compilerPath "/DAgentVersion=$version" "/DAgentBits=$Architecture" "/DAgentDist=$distRoot" installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
    $installerName = "ViDentalImagingAgent-$version-$Architecture-Setup.exe"
    $installerPath = Join-Path $distRoot $installerName
    if ($certificate) {
        $signature = Set-AuthenticodeSignature -FilePath $installerPath -Certificate $certificate -HashAlgorithm SHA256 -TimestampServer 'http://timestamp.digicert.com'
        if ($signature.Status -ne 'Valid') { throw 'Installer signing failed' }
    }
    $release = [ordered]@{
        version = "$version"; protocol = 2
        installers = @([ordered]@{
            architecture = $Architecture; url = "/downloads/$installerName"
            sha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
            signed = [bool]$certificate
        })
    }
    $release | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$distRoot\imaging-agent-release.json" -Encoding UTF8
    if ($PublishWeb) {
        $webDownloads = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\clinic-management-web\public\downloads'))
        New-Item -ItemType Directory -Path $webDownloads -Force | Out-Null
        $manifestPath = Join-Path $webDownloads 'imaging-agent-release.json'
        if (Test-Path -LiteralPath $manifestPath) {
            $existing = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
            if ($existing.version -eq "$version") {
                $release.installers += @($existing.installers | Where-Object { $_.architecture -ne $Architecture -and $_.signed })
            }
        }
        Copy-Item -LiteralPath $installerPath -Destination (Join-Path $webDownloads $installerName)
        $release | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    }
    Write-Host "Created: $installerPath"
    if (!$certificate) { Write-Warning 'LOCAL TEST BUILD ONLY: not code-signed and not published to the web.' }
} finally {
    Pop-Location
}
