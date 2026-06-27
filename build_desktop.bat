@echo off
setlocal enabledelayedexpansion

set VERSION=%1
if "%VERSION%"=="" (
    echo Usage: build_desktop.bat [version]
    echo Example: build_desktop.bat 0.2.0
    pause
    exit /b 1
)

echo ============================================
echo   langgraph_agent Desktop Build
echo   Version: %VERSION%
echo   pywebview + PyInstaller
echo ============================================
echo.

echo [1/6] Setting version to %VERSION%...
poetry run python -c "import pathlib,re;f=pathlib.Path('app/config/version.py');f.write_text(re.sub(r'__version__ = \"[^\"]+\"','__version__ = \"%VERSION%\"',f.read_text(encoding='utf-8')),encoding='utf-8')"
poetry run python -c "import pathlib,re;f=pathlib.Path('pyproject.toml');t=f.read_text(encoding='utf-8');f.write_text(re.sub(r'^version = \"[^\"]+\"','version = \"%VERSION%\"',t,flags=re.M),encoding='utf-8')"
echo [OK] Version set to %VERSION%
echo.

echo [2/6] Checking frontend build...
if not exist "frontend\dist\index.html" goto :build_frontend
echo [OK] Frontend dist exists, skip build
goto :after_frontend

:build_frontend
echo [INFO] Frontend dist not found, building...
cd frontend
call npm run build
if errorlevel 1 (
    cd ..
    echo [ERROR] Frontend build failed!
    pause
    exit /b 1
)
cd ..
echo [OK] Frontend build done
goto :after_frontend

:after_frontend
echo.

echo [3/6] Generating static imports...
poetry run python scripts/generate_static_imports.py
echo.
echo [4/6] Compiling app package with Nuitka (--module)...
if not exist "build" mkdir build
if exist "build\app.cp312-win_amd64.pyd" del "build\app.cp312-win_amd64.pyd"
poetry run nuitka --module app --include-package=app --output-dir=build --show-progress
if errorlevel 1 (
    echo [ERROR] Nuitka module compile failed!
    pause
    exit /b 1
)
if exist "app.pyi" copy /y "app.pyi" "build\app.pyi" >nul
echo [OK] Nuitka module compile done
echo.

echo [5/6] Running PyInstaller with desktop.spec...
if exist "dist" rmdir /s /q dist
if exist "build\langgraph_agent" rmdir /s /q "build\langgraph_agent"
poetry run pyinstaller desktop.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)
echo [OK] PyInstaller build done
echo.

echo [6/6] Creating runtime directories...
if not exist "dist\langgraph_agent\uploads" mkdir "dist\langgraph_agent\uploads"
if not exist "dist\langgraph_agent\data" mkdir "dist\langgraph_agent\data"
if not exist "dist\langgraph_agent\logs" mkdir "dist\langgraph_agent\logs"
if not exist "dist\langgraph_agent\temp" mkdir "dist\langgraph_agent\temp"
if exist ".env.example" (
    copy /y ".env.example" "dist\langgraph_agent\.env" >nul
    echo [OK] .env.example copied as .env
) else (
    echo [WARN] .env.example not found, skip copy
)

echo.
echo ============================================
echo   Desktop build complete!
echo ============================================
echo.
echo   Output: dist\langgraph_agent\
echo   Executable: dist\langgraph_agent\langgraph_agent.exe
echo   Version: %VERSION%
echo.
echo   Double-click langgraph_agent.exe to launch
echo.
pause
