@echo off
rem One click: start the FO3ID private server (own window; close it to stop the server)
rem and then the game. Setup first (README): Python + cryptography, hosts entry
rem "127.0.0.1 APSEODESK172", certs\server.pem, gamedata\fifadb.sqlite and
rem launcher\fifalauncher.exe built from launcher\fifalauncher.cs.
cd /d "%~dp0"
rem a server still running from an earlier play.bat holds the old code: stop it first
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'start_all\.py|http_config\.py|messenger\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
rem the game gets this server's cards, clubs, 3D faces and patches when they are not in it yet
rem (another PC, a fresh client, a change on the admin page or in the tools); a minute or two then
rem (FO3_GAME = the client's folder, default D:\FO3ID\32795)
python tools\fo4_season.py install --if-needed
rem launcher\fifalauncher.exe must be built from fifalauncher.cs (a few KB). Garena's original
rem (1.5 MB, the one in the game folder) needs its dead servers and exits without showing anything
set REBUILD=
if not exist launcher\fifalauncher.exe set REBUILD=1
for %%F in (launcher\fifalauncher.exe) do if %%~zF GTR 100000 set REBUILD=1
if defined REBUILD "%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe" /nologo /target:winexe /out:launcher\fifalauncher.exe launcher\fifalauncher.cs
start "FO3ID server" python start_all.py
timeout /t 4 /nobreak >nul
rem RunAsInvoker: start the game without the UAC prompt (it runs fine unelevated)
set __COMPAT_LAYER=RunAsInvoker
start "" "%~dp0launcher\fifalauncher.exe"

