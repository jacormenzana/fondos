@echo off
rem Starts the Backlog v2 DDL pre-flight as the isolated account fondos_db_owner (FND-0354, Step 3).
rem Runs from the read-only deployed copy only: C:\ProgramData\fondos\db_owner\app\scripts\ops\windows\run_db_owner.cmd
rem runas asks for the account's password here; /savecred is never used (it would write the password to the Credential Manager).
rem Usage: run_db_owner.cmd --check | --phase relations|iam [--down] [--apply]
setlocal
set "R=C:\ProgramData\fondos\db_owner"
if /i not "%~dp0"=="%R%\app\scripts\ops\windows\" (
    echo This launcher runs only from %R%\app\scripts\ops\windows\ ; this copy is in %~dp0
    exit /b 1
)
runas /user:%COMPUTERNAME%\fondos_db_owner "\"%R%\runtime\python.exe\" -I -B \"%R%\app\scripts\ops\db_owner_preflight.py\" %*"
exit /b %errorlevel%
