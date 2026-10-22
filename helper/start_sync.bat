@echo off
cd /d "%~dp0"
python guildsheet_sync.py %*
pause
