@echo off
chcp 65001 >nul
cd /d "%~dp0"
pip install -q -r requirements.txt
python fetch_zsigma_us.py %*
start "" "Zsigma籌碼掃描_美股.html"
pause
