@echo off
REM Instala na primeira vez (fora do OneDrive) e inicia o agente. Deixe a janela aberta.
setlocal
set "VENV=%LocalAppData%\Nescon\agente-fgts-venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo Preparando na primeira vez...
  python -m venv "%VENV%" || (echo Python nao encontrado. & pause & exit /b 1)
  "%VENV%\Scripts\python.exe" -m pip -q install -r "%~dp0requirements.txt" || (echo Falha ao instalar. & pause & exit /b 1)
)
if not exist "%~dp0config.json" (
  echo Falta o config.json. Copie config.json.exemplo e cole o token gerado na tela FGTS da Central.
  pause
  exit /b 1
)
"%VENV%\Scripts\python.exe" "%~dp0agente.py"
pause
