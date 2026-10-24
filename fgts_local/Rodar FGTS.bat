@echo off
REM Prepara o ambiente (so na 1a vez) e abre o app local no navegador.
setlocal
cd /d "%~dp0"
where py >nul 2>&1 && (set "PY=py") || (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
  echo Preparando pela primeira vez. Pode demorar alguns minutos...
  %PY% -m venv .venv || (echo Falha ao criar o ambiente Python. Instale o Python 3.10+ e tente de novo. & pause & exit /b 1)
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  pip install -r requirements.txt || (echo Falha ao instalar as dependencias. & pause & exit /b 1)
) else (
  call ".venv\Scripts\activate.bat"
)
echo.
echo FGTS local em http://127.0.0.1:5055  (feche esta janela para encerrar)
python app_local.py
pause
