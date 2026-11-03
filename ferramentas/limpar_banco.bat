@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0limpar_banco.py" (
  echo Coloque o arquivo limpar_banco.py na mesma pasta deste arquivo e abra de novo.
  pause
  exit /b 1
)
set "PY=python"
where python >nul 2>nul || set "PY=py"
set "VENV=%LOCALAPPDATA%\geocheckin_limpeza"
if not exist "%VENV%\Scripts\python.exe" (
  echo Preparando o ambiente da limpeza ^(so na primeira vez, pode demorar um pouco^)...
  %PY% -m venv "%VENV%"
)
if not exist "%VENV%\Scripts\python.exe" (
  echo Nao consegui preparar o Python. Confira se o Python esta instalado.
  pause
  exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install -q psycopg2-binary
set PYTHONUTF8=1
"%VENV%\Scripts\python.exe" "%~dp0limpar_banco.py"
echo.
pause
