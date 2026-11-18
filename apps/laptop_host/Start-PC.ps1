# Run from PowerShell: powershell -ExecutionPolicy Bypass -File .\Start-PC.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
foreach ($port in @(15602, 15603, 15604, 5500, 5503)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Close old demo/SSH windows before running this launcher."
    }
}
$sshArgs = @('-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
    '-L', '127.0.0.1:15602:127.0.0.1:5602',
    '-L', '127.0.0.1:15603:127.0.0.1:5603',
    '-L', '127.0.0.1:15604:127.0.0.1:5604',
    '-J', 'yukuan@stujump.comp.nus.edu.sg', 'xilinx@makerslab-fpga-41.ddns.comp.nus.edu.sg')
$tunnel = Start-Process -FilePath 'ssh.exe' -ArgumentList $sshArgs -PassThru
try {
    Write-Host 'Finish SSH login in the new window. Then power boards and open the phone app.'
    py .\pipeline.py pc
} finally {
    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id }
}
