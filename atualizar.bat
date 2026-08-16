@echo off
REM ============================================================
REM  UNIAO DOS AFUNDADOS - atualizacao diaria
REM  Rode manualmente ou pelo Agendador de Tarefas do Windows.
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist "data" mkdir "data"

echo. >> "data\log.txt"
echo ===== %DATE% %TIME% ===== >> "data\log.txt"

python run.py >> "data\log.txt" 2>&1
set CODE=%ERRORLEVEL%

if %CODE% NEQ 0 (
    echo [FALHOU com codigo %CODE%] >> "data\log.txt"
    echo.
    echo  A atualizacao FALHOU. Causa mais provavel: a chave da Riot expirou.
    echo  Development Keys duram 24h. Gere outra em developer.riotgames.com
    echo  e atualize o arquivo .env
    echo.
    echo  Detalhes em: data\log.txt
    if not "%1"=="/silent" pause
    exit /b %CODE%
)

echo [OK] >> "data\log.txt"
echo.
echo  Dashboard atualizado: dashboard.html
if not "%1"=="/silent" (
    start "" "dashboard.html"
)
exit /b 0
