#!/bin/bash
set -e

VERSION=$1
if [ -z "$VERSION" ]; then
    echo "用法: ./build_desktop.sh <版本号>"
    echo "示例: ./build_desktop.sh 0.2.0"
    exit 1
fi

echo "============================================"
echo "  langgraph_agent Desktop Build"
echo "  Version: $VERSION"
echo "  pywebview + PyInstaller"
echo "============================================"
echo ""

echo "[1/6] Setting version to $VERSION..."
sed -i "s/__version__ = \"[^\"]*\"/__version__ = \"$VERSION\"/" app/config/version.py
sed -i "0,/version = \"[^\"]*\"/{s/version = \"[^\"]*\"/version = \"$VERSION\"/}" pyproject.toml
echo "[OK] Version set to $VERSION"
echo ""

echo "[2/6] Checking frontend build..."
if [ ! -f "frontend/dist/index.html" ]; then
    echo "[INFO] Frontend dist not found, building..."
    cd frontend
    npm run build
    cd ..
    echo "[OK] Frontend build done"
else
    echo "[OK] Frontend dist exists, skip build"
fi
echo ""

echo "[3/6] Generating static imports..."
poetry run python scripts/generate_static_imports.py
echo ""

echo "[4/6] Compiling app package with Nuitka (--module)..."
mkdir -p build
rm -f build/app.cpython-*.so
poetry run nuitka --module app --include-package=app --output-dir=build --show-progress
echo "[OK] Nuitka module compile done"
if [ -f "app.pyi" ]; then cp app.pyi build/app.pyi; fi
echo ""

echo "[5/6] Running PyInstaller with desktop.spec..."
rm -rf dist
rm -rf build/langgraph_agent
poetry run pyinstaller desktop.spec --noconfirm
echo "[OK] PyInstaller build done"
echo ""

echo "[6/6] Creating runtime directories..."
mkdir -p dist/langgraph_agent/uploads
mkdir -p dist/langgraph_agent/data
mkdir -p dist/langgraph_agent/logs
mkdir -p dist/langgraph_agent/temp
if [ -f ".env.example" ]; then
    cp .env.example dist/langgraph_agent/.env
    echo "[OK] .env.example copied as .env"
else
    echo "[WARN] .env.example not found, skip copy"
fi

echo ""
echo "============================================"
echo "  Desktop build complete!"
echo "============================================"
echo ""
echo "  Output: dist/langgraph_agent/"
echo "  Executable: dist/langgraph_agent/langgraph_agent"
echo "  Version: $VERSION"
echo ""
