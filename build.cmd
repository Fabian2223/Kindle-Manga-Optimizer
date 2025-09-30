@echo off
setlocal

echo =====================================================
echo 🔨 Compilando Kindle Manga Optimizer a EXE portable...
echo =====================================================

REM Ruta al Python 3.13
set PYTHON_EXE=C:\Users\arturo.tzakum\AppData\Local\Programs\Python\Python313\python.exe

REM Nombre del archivo principal
set MAIN_FILE=main.py

REM Nombre del ejecutable
set APP_NAME=MangaKindleOptimizer.exe

REM Ejecuta PyInstaller
%PYTHON_EXE% -m PyInstaller --onefile --windowed --name "%APP_NAME%" "%MAIN_FILE%"

REM Copiar dependencias necesarias a dist\
set DIST_DIR=dist
set EXTRA_FILES=KCC_c2e_9.1.0.exe kindlegen.exe

echo =====================================================
echo 📦 Copiando binarios necesarios...
echo =====================================================

for %%F in (%EXTRA_FILES%) do (
    if exist "%%F" (
        copy "%%F" "%DIST_DIR%\"
        echo ✅ Copiado: %%F
    ) else (
        echo ⚠ No se encontró: %%F
    )
)

echo =====================================================
echo ✅ Proceso completado.
echo Ejecutable listo en: %DIST_DIR%\%APP_NAME%
echo =====================================================

pause
