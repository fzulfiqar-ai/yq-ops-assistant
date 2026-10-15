@echo off
rem Runs the backup job (scripts/nightly_backup.py) once, by hand, its output added to nightly.log.
rem The job reads the backup password from YQ_BACKUP_PASSWORD in this window's environment only.
rem The daily backup does not use this file: the scheduled task "YQ Daily Backup" runs the starter,
rem python -m scripts.yq_nightly, which reads the password from Credential Manager and starts the job
rem only when its archive can be encrypted. To make that task, in a PowerShell window in the project
rem folder:  python -m scripts.yq_nightly --print-task   (see docs/BACKUPS.md).
rem The archive goes to %LOCALAPPDATA%\yq-backups (never OneDrive) and is never uploaded.
cd /d "%~dp0.."
if not exist "%LOCALAPPDATA%\yq-backups" mkdir "%LOCALAPPDATA%\yq-backups"
echo ==== %DATE% %TIME% >> "%LOCALAPPDATA%\yq-backups\nightly.log"
python -m scripts.nightly_backup >> "%LOCALAPPDATA%\yq-backups\nightly.log" 2>&1
exit /b %ERRORLEVEL%
