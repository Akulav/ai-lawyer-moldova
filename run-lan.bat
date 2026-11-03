@echo off
rem Like run.bat, but the interface can also be opened from other devices on the LAN or VPN.
rem There is no login: anyone who can reach the port can use the app. Use only on networks you trust.
set "MAL_HOST=0.0.0.0"
call "%~dp0run.bat"
