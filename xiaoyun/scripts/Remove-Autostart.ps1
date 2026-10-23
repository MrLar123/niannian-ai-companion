# 小芸桌面 - 取消开机自启
$ErrorActionPreference = "SilentlyContinue"
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "小芸桌面.lnk"
if(Test-Path $lnk){
    Remove-Item $lnk -Force
    Write-Host "[OK] 已取消开机自启。" -ForegroundColor Green
} else {
    Write-Host "未找到开机自启项，可能已经取消。"
}
Start-Sleep -Seconds 3
