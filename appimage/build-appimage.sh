#!/bin/bash
# Whispered AppImage Build Script
# Run this on a Fedora system to build the AppImage

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/build"
APPDIR="$BUILD_DIR/AppDir"
OUTPUT_DIR="$PROJECT_DIR/dist"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║             Whispered - AppImage Build Script            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Clean previous build
echo -e "${YELLOW}→ Cleaning previous build...${NC}"
rm -rf "$BUILD_DIR" "$OUTPUT_DIR"
mkdir -p "$APPDIR" "$OUTPUT_DIR"

# Check for required tools
echo -e "${YELLOW}→ Checking build dependencies...${NC}"

# Check for python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 is required${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Python 3 found"

# Download linuxdeploy if not present
LINUXDEPLOY="$BUILD_DIR/linuxdeploy-x86_64.AppImage"
if [ ! -f "$LINUXDEPLOY" ]; then
    echo -e "${YELLOW}→ Downloading linuxdeploy...${NC}"
    wget -q --show-progress -O "$LINUXDEPLOY" \
        "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage"
    chmod +x "$LINUXDEPLOY"
fi
echo -e "  ${GREEN}✓${NC} linuxdeploy ready"

# Download linuxdeploy Python plugin
PYTHON_PLUGIN="$BUILD_DIR/linuxdeploy-plugin-python-x86_64.AppImage"
if [ ! -f "$PYTHON_PLUGIN" ]; then
    echo -e "${YELLOW}→ Downloading Python plugin...${NC}"
    wget -q --show-progress -O "$PYTHON_PLUGIN" \
        "https://github.com/niess/linuxdeploy-plugin-python/releases/download/continuous/linuxdeploy-plugin-python-x86_64.AppImage"
    chmod +x "$PYTHON_PLUGIN"
fi
echo -e "  ${GREEN}✓${NC} Python plugin ready"

# Create AppDir structure
echo -e "${YELLOW}→ Creating AppDir structure...${NC}"

# Create directories
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib/whisper-fedora"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$APPDIR/usr/share/metainfo"

# Copy application files
echo -e "${YELLOW}→ Copying application files...${NC}"
cp "$PROJECT_DIR/main.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/transcriber.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/exporters.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/utils.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/config.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/diarizer.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/lm_studio_manager.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/text_processor.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/batch_processor.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/article_generator.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/book_pipeline.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/timeline_export.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/video_cut.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/video_edit.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/video_input.py" "$APPDIR/usr/lib/whisper-fedora/"
cp "$PROJECT_DIR/setup_diarization.py" "$APPDIR/usr/lib/whisper-fedora/"
cp -r "$PROJECT_DIR/ui" "$APPDIR/usr/lib/whisper-fedora/"
cp -r "$PROJECT_DIR/core" "$APPDIR/usr/lib/whisper-fedora/"
cp -r "$PROJECT_DIR/covers" "$APPDIR/usr/lib/whisper-fedora/"
cp -r "$PROJECT_DIR/locales" "$APPDIR/usr/lib/whisper-fedora/"
cp -r "$PROJECT_DIR/prompts" "$APPDIR/usr/lib/whisper-fedora/"
cp -r "$PROJECT_DIR/assets" "$APPDIR/usr/lib/whisper-fedora/"

# Copy desktop file
cp "$SCRIPT_DIR/whispered.desktop" "$APPDIR/usr/share/applications/"
cp "$SCRIPT_DIR/io.github.whispered.metainfo.xml" "$APPDIR/usr/share/metainfo/"

# Copy icon
cp "$SCRIPT_DIR/whisper-fedora.png" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/whispered.png" 2>/dev/null || \
    echo -e "  ${YELLOW}⚠${NC} No icon found, using default"

# Create launcher script
cat > "$APPDIR/usr/bin/whispered" << 'EOF'
#!/bin/bash
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
export PYTHONPATH="$SCRIPT_DIR/../lib/whisper-fedora:$PYTHONPATH"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QT_AUTO_SCREEN_SCALE_FACTOR=1
exec python3 "$SCRIPT_DIR/../lib/whisper-fedora/main.py" "$@"
EOF
chmod +x "$APPDIR/usr/bin/whispered"

# Create AppRun
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH}"
export PYTHONPATH="${HERE}/usr/lib/whisper-fedora:${PYTHONPATH}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QT_AUTO_SCREEN_SCALE_FACTOR=1
exec "${HERE}/usr/bin/whispered" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# Link desktop file and icon to AppDir root
ln -sf usr/share/applications/whispered.desktop "$APPDIR/"
ln -sf usr/share/icons/hicolor/256x256/apps/whispered.png "$APPDIR/" 2>/dev/null || true

# Use the same runtime dependency declaration as source installs. Keeping a
# second handwritten subset here previously produced an AppImage without
# recording, DOCX and cover-pipeline dependencies.
cp "$PROJECT_DIR/requirements.txt" "$BUILD_DIR/requirements.txt"

# Build with linuxdeploy
echo -e "${YELLOW}→ Building AppImage...${NC}"
echo "   This may take several minutes..."

cd "$BUILD_DIR"

# Set Python version
export PIP_REQUIREMENTS="$BUILD_DIR/requirements.txt"
export DEPLOY_PYTHON_VERSION=3.11

# Run linuxdeploy with Python plugin
"$LINUXDEPLOY" \
    --appdir "$APPDIR" \
    --desktop-file "$APPDIR/usr/share/applications/whispered.desktop" \
    --plugin python \
    --output appimage \
    2>&1 | tail -20

# Move output
mv Whispered*.AppImage "$OUTPUT_DIR/" 2>/dev/null || \
mv whispered*.AppImage "$OUTPUT_DIR/" 2>/dev/null || \
echo -e "${YELLOW}⚠ AppImage may be in $BUILD_DIR${NC}"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Build Complete!                        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "AppImage location: $OUTPUT_DIR/"
ls -lh "$OUTPUT_DIR/"*.AppImage 2>/dev/null || ls -lh "$BUILD_DIR/"*.AppImage 2>/dev/null
