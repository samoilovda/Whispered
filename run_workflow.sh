#!/bin/bash
# Zoom to Blog Workflow Runner
# Usage: ./run_workflow.sh /path/to/recording.mp4 [options]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/zoom_to_blog.py"

# Check dependencies
check_deps() {
    local missing=""
    
    if ! command -v ffmpeg &> /dev/null; then
        missing="$missing ffmpeg"
    fi
    
    if ! command -v whisper &> /dev/null; then
        missing="$missing openai-whisper"
    fi
    
    if [ -n "$missing" ]; then
        echo "❌ Missing dependencies:$missing"
        if [[ "$(uname)" == "Darwin" ]]; then
            echo "   Install with: brew install$missing"
        elif command -v dnf &> /dev/null; then
            echo "   Install with: sudo dnf install$missing"
        else
            echo "   Install with: sudo apt install$missing"
        fi
        exit 1
    fi
}

# Check LM Studio
check_lm_studio() {
    if curl -s "http://localhost:1234/v1/models" > /dev/null 2>&1; then
        echo "✅ LM Studio is running"
    else
        echo "⚠️  LM Studio not detected on port 1234"
        echo "   The workflow will run transcription only."
        echo "   To enable AI content generation:"
        echo "   1. Open LM Studio"
        echo "   2. Load a model"
        echo "   3. Start the local server (Developer tab → Start Server)"
        echo ""
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 0
        fi
    fi
}

main() {
    if [ $# -lt 1 ]; then
        echo "Usage: $0 <input_file> [options]"
        echo ""
        echo "Options are passed directly to zoom_to_blog.py:"
        echo "  --whisper-model, -m   Model size (tiny/base/small/medium/large/turbo)"
        echo "  --language, -l        Language code or 'auto'"
        echo "  --output-dir, -o      Output directory"
        echo "  --skip-lm             Skip LM Studio (transcription only)"
        echo ""
        echo "Examples:"
        echo "  $0 recording.mp4"
        echo "  $0 recording.mp4 -m large -l ru"
        echo "  $0 recording.mp4 --skip-lm"
        exit 1
    fi
    
    echo "🚀 Zoom to Blog Workflow"
    echo "========================"
    echo ""
    
    check_deps
    check_lm_studio
    
    echo ""
    python3 "$PYTHON_SCRIPT" "$@"
}

main "$@"
