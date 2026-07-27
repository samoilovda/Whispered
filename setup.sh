#!/bin/bash
# Whisper Fedora UI - Setup Script
# Installs dependencies and downloads default model

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
if [ "$(uname -s)" = "Darwin" ]; then
    MODELS_DIR="$HOME/Library/Application Support/Whispered/models"
else
    MODELS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/Whispered/models"
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       Whisper Fedora UI - Installation Script            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check for required system dependencies
echo -e "${YELLOW}→ Checking system dependencies...${NC}"

# Check for Python
PYTHON_CMD=""
if command -v python3.11 &> /dev/null; then
    PYTHON_CMD="python3.11"
elif command -v python3.10 &> /dev/null; then
    PYTHON_CMD="python3.10"
elif command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
else
    echo -e "${RED}❌ Python 3 is required but not found. Please install Python 3.10+${NC}"
    echo "   On Fedora: sudo dnf install python3 python3-pip python3-devel"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2)
echo -e "  ${GREEN}✓${NC} Python: $PYTHON_CMD ($PYTHON_VERSION)"

# Check for pip
if ! $PYTHON_CMD -m pip --version &> /dev/null; then
    echo -e "${RED}❌ pip is not installed.${NC}"
    echo "   On Fedora: sudo dnf install python3-pip"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} pip installed"

# Check for ffmpeg (optional but recommended)
if command -v ffprobe &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} ffmpeg/ffprobe installed (for duration detection)"
else
    echo -e "  ${YELLOW}⚠${NC} ffmpeg not found (optional - install for duration display)"
    echo "     On Fedora: sudo dnf install ffmpeg"
fi

# Check for Qt dependencies
if ! rpm -q qt6-qtbase &> /dev/null 2>&1; then
    echo -e "  ${YELLOW}⚠${NC} Qt6 libraries may not be installed"
    echo "     On Fedora: sudo dnf install qt6-qtbase qt6-qtwayland"
fi

# Create virtual environment
echo ""
echo -e "${YELLOW}→ Creating virtual environment...${NC}"
$PYTHON_CMD -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
echo -e "  ${GREEN}✓${NC} Virtual environment created at $VENV_DIR"

# Upgrade pip
echo ""
echo -e "${YELLOW}→ Upgrading pip...${NC}"
pip install --upgrade pip wheel setuptools > /dev/null
echo -e "  ${GREEN}✓${NC} pip upgraded"

# Detect GPU type
echo ""
echo -e "${YELLOW}→ Detecting GPU acceleration...${NC}"

GPU_TYPE="cpu"

# Check for NVIDIA CUDA
if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    if [ -n "$GPU_NAME" ]; then
        echo -e "  ${GREEN}✓${NC} NVIDIA GPU detected: $GPU_NAME"
        GPU_TYPE="cuda"
        
        # Check for CUDA toolkit
        if command -v nvcc &> /dev/null; then
            CUDA_VERSION=$(nvcc --version | grep "release" | awk '{print $5}' | cut -d',' -f1)
            echo -e "  ${GREEN}✓${NC} CUDA toolkit: $CUDA_VERSION"
        else
            echo -e "  ${YELLOW}⚠${NC} CUDA toolkit not found in PATH"
            echo "     Install: sudo dnf install cuda-toolkit"
        fi
    fi
# Check for AMD ROCm
elif command -v rocminfo &> /dev/null; then
    GPU_NAME=$(rocminfo 2>/dev/null | grep "Marketing Name:" | head -1 | cut -d':' -f2 | xargs)
    if [ -n "$GPU_NAME" ]; then
        echo -e "  ${GREEN}✓${NC} AMD GPU detected: $GPU_NAME"
        GPU_TYPE="rocm"
    else
        echo -e "  ${GREEN}✓${NC} AMD ROCm detected"
        GPU_TYPE="rocm"
    fi
else
    echo -e "  ${BLUE}ℹ${NC} No GPU detected - using CPU mode"
fi

# Install PyQt6 and dark theme
echo ""
echo -e "${YELLOW}→ Installing PyQt6 and theme...${NC}"
pip install 'PyQt6>=6.6.0' > /dev/null
pip install 'pyqtdarktheme>=2.1.0' --ignore-requires-python > /dev/null
echo -e "  ${GREEN}✓${NC} PyQt6 and pyqtdarktheme installed"

# Install pywhispercpp with appropriate GPU support
echo ""
echo -e "${YELLOW}→ Installing pywhispercpp ($GPU_TYPE mode)...${NC}"
echo "   This may take a few minutes..."

if [ "$GPU_TYPE" = "cuda" ]; then
    GGML_CUDA=1 pip install pywhispercpp 2>&1 | tail -3
elif [ "$GPU_TYPE" = "rocm" ]; then
    GGML_HIPBLAS=1 pip install pywhispercpp 2>&1 | tail -3
else
    pip install pywhispercpp 2>&1 | tail -3
fi
echo -e "  ${GREEN}✓${NC} pywhispercpp installed"

# Create models directory
echo ""
echo -e "${YELLOW}→ Setting up models directory...${NC}"
mkdir -p "$MODELS_DIR"
echo -e "  ${GREEN}✓${NC} Models directory: $MODELS_DIR"

# Download default model (base)
echo ""
echo -e "${YELLOW}→ Downloading default Whisper model (base)...${NC}"
echo "   ~142MB download - this may take a moment..."
$PYTHON_CMD -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from pywhispercpp.model import Model
import os

models_dir = '$MODELS_DIR'
try:
    model = Model('base', models_dir=models_dir)
    print('  ✓ Model downloaded successfully')
except Exception as e:
    print(f'  ⚠ Could not download model: {e}')
    print('    The model will be downloaded on first use.')
"

# Save GPU configuration
echo "$GPU_TYPE" > "$SCRIPT_DIR/.gpu_type"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                 Installation Complete!                    ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  GPU Mode:${NC} $GPU_TYPE"
echo -e "${GREEN}║  To run:${NC}   ./run.sh"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
