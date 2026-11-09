@echo off
REM Grava o config.json do agente. Cole o token gerado em Central > FGTS Digital > Agentes.
setlocal
set /p TOKEN=Cole o token do agente (comeca com fgag_) e aperte Enter:
if "%TOKEN:~0,5%" NEQ "fgag_" (
  echo Token invalido. Gere um na tela FGTS Digital da Central, aba Agentes.
  pause
  exit /b 1
)
set "URL=https://ecac.gestaoempresa.com"
> "%~dp0config.json" (
  echo {
  echo   "central_url": "%URL%",
  echo   "token": "%TOKEN%",
  echo   "nome_maquina": "%COMPUTERNAME%",
  echo   "porta_chrome": 9222,
  echo   "perfil_chrome": "%%LOCALAPPDATA%%\\Nescon\\agente-fgts-chrome"
  echo }
)
echo Configurado. Agora abra "Iniciar agente FGTS.bat".
pause
