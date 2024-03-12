@echo off
setlocal
set PYTHONPATH=%~dp0\..
python -m chonky %*
endlocal