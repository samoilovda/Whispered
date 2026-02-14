#!/bin/bash
set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Starting Standalone Build...${NC}"

# Check for virtual environment
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -d "$PROJECT_DIR/.venv" ]; then
        echo -e "${YELLOW}Activating virtual environment...${NC}"
        source "$PROJECT_DIR/.venv/bin/activate"
    else
        echo -e "${YELLOW}No virtual environment found active or at .venv${NC}"
        # We can try to proceed if pyinstaller is in path
    fi
fi

# Install PyInstaller if missing
if ! command -v pyinstaller &> /dev/null; then
    echo -e "${YELLOW}Installing PyInstaller...${NC}"
    pip install pyinstaller
fi

# Clean previous build
echo -e "${YELLOW}Cleaning previous build...${NC}"
rm -rf "$PROJECT_DIR/build" "$PROJECT_DIR/dist" "$PROJECT_DIR/Whisper Fedora.spec"

# Build
echo -e "${GREEN}Running PyInstaller...${NC}"
cd "$PROJECT_DIR"

# Check for icon
ICON_ARG=""
if [ -f "$SCRIPT_DIR/whisper-fedora.icns" ]; then
    ICON_ARG="--icon=$SCRIPT_DIR/whisper-fedora.icns"
fi

pyinstaller --noconfirm --clean \
    --name "Whisper Fedora" \
    --windowed \
    $ICON_ARG \
    --add-data "ui:ui" \
    --add-data "models:models" \
    --collect-all pywhispercpp \
    --hidden-import PyQt6 \
    --hidden-import pyqtdarktheme \
    --hidden-import pywhispercpp \
    main.py

echo -e "${GREEN}Build Complete!${NC}"
echo "App bundle: $PROJECT_DIR/dist/Whisper Fedora.app"
