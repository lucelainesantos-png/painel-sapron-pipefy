@echo off
rem Refresh do painel Sapron x Pipefy — usado pelo Task Scheduler.
cd /d "%~dp0"
python refresh.py
exit /b %errorlevel%
