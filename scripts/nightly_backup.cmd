@echo off
rem Nightly encrypted backup (scripts/nightly_backup.py). Task Scheduler runs this file; see the
rem module docstring for the one-time setup (setx YQ_BACKUP_PASSWORD, schtasks /Create).
rem The archive goes to %LOCALAPPDATA%\yq-backups (never OneDrive) and is never uploaded.
cd /d "%~dp0.."
if not exist "%LOCALAPPDATA%\yq-backups" mkdir "%LOCALAPPDATA%\yq-backups"
echo ==== %DATE% %TIME% >> "%LOCALAPPDATA%\yq-backups\nightly.log"
python -m scripts.nightly_backup >> "%LOCALAPPDATA%\yq-backups\nightly.log" 2>&1
exit /b %ERRORLEVEL%
