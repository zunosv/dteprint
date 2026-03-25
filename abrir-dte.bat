@echo off
set CHROME1="C:\Program Files\Google\Chrome\Application\chrome.exe"
set CHROME2="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
set PERFIL="C:\tmp\chrome-dte"
set PAGINA="%~dp0dte-database.html"

if exist %CHROME1% (
    start "" %CHROME1% --disable-web-security --user-data-dir=%PERFIL% --new-window %PAGINA%
    goto :fin
)
if exist %CHROME2% (
    start "" %CHROME2% --disable-web-security --user-data-dir=%PERFIL% --new-window %PAGINA%
    goto :fin
)

echo No se encontro Chrome en las rutas habituales.
echo Edita este archivo y ajusta la ruta de chrome.exe
pause

:fin
