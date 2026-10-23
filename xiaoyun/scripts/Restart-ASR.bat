@echo off
rem =====================================================================
rem  Restart-ASR.bat  --  restart Xiaoyun ASR service (8091)
rem  All env vars are declared HERE so the service never depends on the
rem  caller's environment block (which is polluted on this machine).
rem  NOTE: this file MUST keep CRLF line endings. With LF only, Windows
rem  cmd silently skips lines and double-click appears to do nothing.
rem =====================================================================
echo Restarting Xiaoyun ASR (8091) ... please wait.
echo.

setlocal
set "ROOT=D:\LunaAI\xiaoyun"
set "PY=%ROOT%\.venv-asr\Scripts\pythonw.exe"
set "APP=%ROOT%\asr_service.py"

rem ---- recognition engine ----
rem auto = try Baidu cloud first, fall back to local Whisper on failure
rem baidu / whisper = force one engine
set "XIAOYUN_ASR_PROVIDER=auto"
set "XIAOYUN_BAIDU_APP_ID=8072096"
set "XIAOYUN_BAIDU_API_KEY=oMwnDYb1puNq9DAV6fTOWY2j"
set "XIAOYUN_BAIDU_SECRET_KEY=uIECMsPRBuMHIrk1GT8sL2bRj4ToHskG"
set "XIAOYUN_BAIDU_DEV_PID=1537"

rem ---- local fallback + microphone ----
set "XIAOYUN_ASR_MODEL=D:\LunaAI\xiaoyun\models\faster-whisper-small"
set "HF_HUB_DISABLE_XET=1"
set "HF_HUB_OFFLINE=1"
set "XIAOYUN_ASR_INPUT=USB Audio Device"
set "XIAOYUN_ASR_THR=0.0010"
set "XIAOYUN_ASR_NOISE_K=1.3"

echo [1/3] stopping any running ASR ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*asr_service.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*asr_service.py*' } | ForEach-Object { if($_.ParentProcessId){ Stop-Process -Id $_.ParentProcessId -Force -ErrorAction SilentlyContinue } }"
rem ping instead of "timeout /t": immune to MSYS path mangling and always present
ping -n 3 127.0.0.1 >nul

echo [2/3] starting asr_service.py ...
cd /d "%ROOT%"
"%PY%" "%APP%" > "%ROOT%\asr_out.log" 2> "%ROOT%\asr_err.log"

echo [3/3] waiting for /health ...
set RETRY=0
:wait
ping -n 2 127.0.0.1 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{ Invoke-RestMethod 'http://127.0.0.1:8091/health' -TimeoutSec 3 | Out-Null; exit 0 }catch{ exit 1 }"
if "%ERRORLEVEL%"=="0" goto ready
set /a RETRY+=1
if %RETRY% GEQ 45 goto fail
goto wait

:ready
echo.
echo [OK] ASR is up on 8091. Voice recognition restored.
echo.
pause
exit /b 0

:fail
echo.
echo [FAIL] ASR did not become ready within 45 tries.
echo Please send me the content of: D:\LunaAI\xiaoyun\logs\asr_err.log
echo.
pause
exit /b 1
