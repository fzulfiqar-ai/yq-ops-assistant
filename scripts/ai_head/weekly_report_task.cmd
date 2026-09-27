@echo off
rem Windows Task Scheduler entry for the weekly management email (scripts/ai_head/README.md).
rem   schtasks ... /TR "\"<repo>\scripts\ai_head\weekly_report_task.cmd\" \"a@example.com,b@example.com\""
rem The recipients are the task's one argument, quoted (cmd would split an unquoted list at the commas).
rem Output is appended to %LOCALAPPDATA%\yq_weekly_report.log.
if "%~1"=="" (
  echo weekly_report_task: no recipients given >> "%LOCALAPPDATA%\yq_weekly_report.log"
  exit /b 2
)
cd /d "%~dp0..\.."
echo ==== %DATE% %TIME% >> "%LOCALAPPDATA%\yq_weekly_report.log"
python -m scripts.weekly_report --send --to "%~1" >> "%LOCALAPPDATA%\yq_weekly_report.log" 2>&1
exit /b %ERRORLEVEL%
