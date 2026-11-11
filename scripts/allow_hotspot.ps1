param([Parameter(Mandatory=$true)][string]$Address)
$ErrorActionPreference='Stop'
$parsed=[System.Net.IPAddress]::Parse($Address)
if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { throw '须为IPv4地址' }
$interface=Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Address
if (-not $interface) { throw '此地址不属于本机网卡' }
if ($interface.PrefixLength -ne 24) { throw '此脚本仅用于/24热点网段，请先核对所选网卡' }
$prefix=($Address.Split('.')[0..2] -join '.')+'.0/24'
$python='C:/Users/flori/AppData/Local/Programs/Python/Python313/python.exe'
$name='CheckCollect-Hotspot-Demo'
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) { throw '规则已存在，未覆盖，请先核对' }
New-NetFirewallRule -Name $name -DisplayName 'check@collect 手机热点演示' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443,8444 -LocalAddress $Address -RemoteAddress $prefix -Program $python -Profile Any | Out-Null
Write-Output '只允许所选网卡同一/24网段访问8443、8444。演示后用Remove-NetFirewallRule -Name CheckCollect-Hotspot-Demo移除。'
