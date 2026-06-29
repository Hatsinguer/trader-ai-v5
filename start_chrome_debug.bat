@echo off
echo =====================================================
echo   Abrindo Chrome com porta de debug (9222)
echo   Trader AI v5 - Captura de Precos
echo =====================================================
echo.
echo Apos o Chrome abrir:
echo   1. Faca login no Home Broker da XP normalmente
echo   2. Deixe a pagina de posicoes/watchlist aberta
echo   3. Rode: python captura_precos.py
echo.

:: Caminhos comuns do Chrome no Windows
set CHROME1="%ProgramFiles%\Google\Chrome\Application\chrome.exe"
set CHROME2="%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
set CHROME3="%LocalAppData%\Google\Chrome\Application\chrome.exe"

if exist %CHROME1% (
    start "" %CHROME1% --remote-debugging-port=9222 --user-data-dir="%TEMP%\chrome_trader_ai"
    goto :fim
)
if exist %CHROME2% (
    start "" %CHROME2% --remote-debugging-port=9222 --user-data-dir="%TEMP%\chrome_trader_ai"
    goto :fim
)
if exist %CHROME3% (
    start "" %CHROME3% --remote-debugging-port=9222 --user-data-dir="%TEMP%\chrome_trader_ai"
    goto :fim
)

echo ERRO: Chrome nao encontrado nos caminhos padrao.
echo Edite este arquivo e ajuste o caminho do chrome.exe
pause
exit /b 1

:fim
echo Chrome iniciado. Aguarde carregar e faca login no Home Broker.
timeout /t 3 >nul
