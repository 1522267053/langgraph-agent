@echo off
setlocal

set VERSION=%1
if "%VERSION%"=="" (
    echo Usage: build.bat [version]
    echo Example: build.bat 0.2.0
    pause
    exit /b 1
)

echo ============================================
echo   langgraph_agent PyInstaller Build
echo   Version: %VERSION%
echo   (Nuitka module + PyInstaller)
echo ============================================
echo.

echo [0/5] Setting version to %VERSION%...
poetry run python -c "import pathlib,re;f=pathlib.Path('app/config/version.py');f.write_text(re.sub(r'__version__ = \"[^\"]+\"','__version__ = \"%VERSION%\"',f.read_text(encoding='utf-8')),encoding='utf-8')"
poetry run python -c "import pathlib,re;f=pathlib.Path('pyproject.toml');t=f.read_text(encoding='utf-8');f.write_text(re.sub(r'^version = \"[^\"]+\"','version = \"%VERSION%\"',t,flags=re.M),encoding='utf-8')"
echo [OK] Version set to %VERSION%
echo.

echo [1/5] Checking frontend build...
if not exist "frontend\dist\index.html" goto :build_frontend
echo [OK] Frontend dist exists, skip build
goto :after_frontend

:build_frontend
echo [INFO] Frontend dist not found, building...
cd frontend
call npm run build
if errorlevel 1 goto :frontend_failed
cd ..
echo [OK] Frontend build done
goto :after_frontend

:frontend_failed
cd ..
echo [ERROR] Frontend build failed!
pause
exit /b 1

:after_frontend

echo.
echo [2/5] Generating static imports...
poetry run python scripts/generate_static_imports.py
echo.

echo [3/5] Compiling app package with Nuitka (--module)...
if not exist "build" mkdir build
if exist "build\app.cp312-win_amd64.pyd" del "build\app.cp312-win_amd64.pyd"
poetry run nuitka --module app --include-package=app --output-dir=build --show-progress
if errorlevel 1 goto :nuitka_failed
if exist "app.pyi" copy /y "app.pyi" "build\app.pyi" >nul
echo [OK] Nuitka module compile done
goto :after_nuitka

:nuitka_failed
echo [ERROR] Nuitka module compile failed!
pause
exit /b 1

:after_nuitka

echo.
echo [4/5] Running PyInstaller (--onedir)...
if exist "dist" rmdir /s /q dist
if exist "build\langgraph_agent" rmdir /s /q "build\langgraph_agent"
poetry run pyinstaller langgraph_agent.spec --noconfirm
if errorlevel 1 goto :pyinstaller_failed
echo [OK] PyInstaller build done
goto :after_pyinstaller

:pyinstaller_failed
echo [ERROR] PyInstaller build failed!
pause
exit /b 1

:after_pyinstaller

echo.
echo [5/5] Creating runtime directories...
if not exist "dist\langgraph_agent\uploads" mkdir "dist\langgraph_agent\uploads"
if not exist "dist\langgraph_agent\data" mkdir "dist\langgraph_agent\data"
if not exist "dist\langgraph_agent\logs" mkdir "dist\langgraph_agent\logs"
if exist ".env.example" (
    copy /y ".env.example" "dist\langgraph_agent\.env" >nul
    echo [OK] .env.example copied as .env to dist\langgraph_agent\
) else (
    echo [WARN] .env.example not found, skip copy
)

echo.
echo ============================================
echo   Build complete!
echo ============================================
echo.
echo   Output: dist\langgraph_agent\
echo   Executable: dist\langgraph_agent\langgraph_agent.exe
echo   Version: %VERSION%
echo.
echo   Business code: app.cp312-win_amd64.pyd (compiled binary)
echo   Third-party libs: .pyc (PyInstaller default)
echo.
echo   Ready to run: just double-click langgraph_agent.exe
echo.
pause
