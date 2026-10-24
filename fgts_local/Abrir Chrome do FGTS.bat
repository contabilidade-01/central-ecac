@echo off
REM Abre um Chrome dedicado ao FGTS (perfil proprio) com a porta de depuracao ligada,
REM para o app local se conectar DEPOIS que voce entrar no FGTS Digital.
REM O LOGIN (certificado + captcha) e sempre feito por voce, aqui, a mao.
setlocal
set PORTA=9222
set PERFIL=%~dp0chrome-perfil
set URL=https://fgtsdigital.sistema.gov.br/
set CHROME=
for %%p in (
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do if exist "%%~p" set "CHROME=%%~p"
if "%CHROME%"=="" (
  echo Nao encontrei o Google Chrome instalado.
  echo Instale o Chrome e rode este atalho de novo.
  pause
  exit /b 1
)
echo Abrindo o Chrome do FGTS...
echo Entre no FGTS Digital (certificado e captcha) e deixe esta janela aberta.
REM --remote-allow-origins e exigido pelo Chrome atual para a ligacao local do app.
start "" "%CHROME%" --remote-debugging-port=%PORTA% --remote-allow-origins=* --user-data-dir="%PERFIL%" "%URL%"
