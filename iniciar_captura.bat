@echo off
echo =============================================
echo   Captura de Precos - Trader AI v5
echo =============================================
echo.
echo Verificando dependencias...
pip install mss pillow pytesseract --quiet
echo.
echo Iniciando captura...
python captura_precos.py
pause
