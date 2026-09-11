@echo off
setlocal
cd /d D:\AIInvest\SAG\apps\api
set PYTHONUTF8=1
.venv\Scripts\python.exe -m sag_api.worker --log-file .data\sag-worker-live.log >> .data\sag-worker-launch.log 2>&1
