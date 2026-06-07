#!/bin/bash
set -e

VERSION=$1
if [ -z "$VERSION" ]; then
    echo "用法: ./build.sh <版本号>"
    echo "示例: ./build.sh 0.2.0"
    exit 1
fi

echo "============================================"
echo "  langgraph_agent PyInstaller Build"
echo "  Version: $VERSION"
echo "  (Nuitka module + PyInstaller)"
echo "============================================"
echo ""

echo "[0/5] Setting version to $VERSION..."
sed -i "s/__version__ = \"[^\"]*\"/__version__ = \"$VERSION\"/" app/config/version.py
sed -i "0,/version = \"[^\"]*\"/{s/version = \"[^\"]*\"/version = \"$VERSION\"/}" pyproject.toml
echo "[OK] Version set to $VERSION"
echo ""

echo "[1/5] Checking frontend build..."
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
echo "[2/5] Generating static imports..."
poetry run python scripts/generate_static_imports.py

echo ""
echo "[3/5] Compiling app package with Nuitka (--module)..."
mkdir -p build
rm -f build/app.cpython-*.so
poetry run nuitka --module app --include-package=app --output-dir=build --show-progress
echo "[OK] Nuitka module compile done"
if [ -f "app.pyi" ]; then cp app.pyi build/app.pyi; fi

echo ""
echo "[4/5] Running PyInstaller (--onedir)..."
rm -rf dist
rm -rf build/langgraph_agent
poetry run pyinstaller langgraph_agent.spec --noconfirm
echo "[OK] PyInstaller build done"

echo ""
echo "[5/5] Creating runtime directories..."
mkdir -p dist/langgraph_agent/uploads
mkdir -p dist/langgraph_agent/data
mkdir -p dist/langgraph_agent/logs
if [ -f ".env.example" ]; then
    cp .env.example dist/langgraph_agent/.env
    echo "[OK] .env.example copied as .env to dist/langgraph_agent/"
else
    echo "[WARN] .env.example not found, skip copy"
fi

echo ""
echo "============================================"
echo "  Build complete!"
echo "============================================"
echo ""
echo "  Output: dist/langgraph_agent/"
echo "  Executable: dist/langgraph_agent/langgraph_agent"
echo "  Version: $VERSION"
echo ""
echo "  Business code: app.*.so (compiled binary)"
echo "  Third-party libs: .pyc (PyInstaller default)"
echo ""
