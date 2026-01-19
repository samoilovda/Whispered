#!/usr/bin/env python3
"""
Whisper Fedora - Diarization Setup Script
Configure Hugging Face token for speaker diarization
"""

import sys
import getpass
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import get_config, save_config


def print_header():
    print("=" * 60)
    print("  Whisper Fedora - Speaker Diarization Setup")
    print("=" * 60)
    print()


def check_pyannote():
    """Check if pyannote is installed."""
    try:
        import pyannote.audio
        print("✅ pyannote.audio is installed")
        return True
    except ImportError:
        print("❌ pyannote.audio is NOT installed")
        print()
        print("   Install it with:")
        print("   pip install pyannote.audio torch")
        return False


def check_torch():
    """Check PyTorch and available accelerators."""
    try:
        import torch
        print(f"✅ PyTorch {torch.__version__} is installed")
        
        if torch.backends.mps.is_available():
            print("   🍎 Apple Silicon GPU (MPS) available")
        elif torch.cuda.is_available():
            print(f"   🚀 NVIDIA GPU (CUDA) available: {torch.cuda.get_device_name(0)}")
        else:
            print("   💻 CPU mode (no GPU acceleration)")
        
        return True
    except ImportError:
        print("❌ PyTorch is NOT installed")
        return False


def setup_hf_token():
    """Configure Hugging Face token."""
    print()
    print("Hugging Face Token Setup")
    print("-" * 40)
    print()
    print("Speaker diarization requires access to pyannote models on Hugging Face.")
    print()
    print("Steps:")
    print("1. Create a free account at https://huggingface.co")
    print("2. Go to https://huggingface.co/settings/tokens")
    print("3. Create a new token with 'read' access")
    print("4. Accept the model license at:")
    print("   https://huggingface.co/pyannote/speaker-diarization-3.1")
    print("   https://huggingface.co/pyannote/segmentation-3.0")
    print()
    
    config = get_config()
    
    if config.has_hf_token():
        print(f"Current token: {config.hf_token[:8]}...")
        choice = input("Replace existing token? [y/N]: ").strip().lower()
        if choice != 'y':
            print("Keeping existing token.")
            return True
    
    print()
    token = getpass.getpass("Enter your Hugging Face token: ").strip()
    
    if not token:
        print("No token entered. Skipping.")
        return False
    
    if len(token) < 10:
        print("Token seems too short. Please check and try again.")
        return False
    
    config.hf_token = token
    config.diarization_enabled = True
    
    if save_config():
        print()
        print("✅ Token saved successfully!")
        print("   Diarization is now enabled.")
        return True
    else:
        print("❌ Failed to save token.")
        return False


def verify_setup():
    """Verify the complete setup."""
    print()
    print("Verification")
    print("-" * 40)
    
    config = get_config()
    
    if not config.has_hf_token():
        print("❌ No HF token configured")
        return False
    
    print("Testing pyannote pipeline access...")
    
    try:
        from pyannote.audio import Pipeline
        
        # Just check if we can access the model info (doesn't download full model)
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=config.hf_token
        )
        print("✅ Successfully connected to pyannote models!")
        print()
        print("🎉 Setup complete! Speaker diarization is ready to use.")
        return True
        
    except Exception as e:
        error_msg = str(e).lower()
        
        if "401" in error_msg or "unauthorized" in error_msg:
            print("❌ Invalid token or unauthorized access")
            print("   Make sure you've accepted the model license at:")
            print("   https://huggingface.co/pyannote/speaker-diarization-3.1")
        elif "403" in error_msg or "forbidden" in error_msg:
            print("❌ Access forbidden")
            print("   You need to accept the model license at:")
            print("   https://huggingface.co/pyannote/speaker-diarization-3.1")
        else:
            print(f"❌ Error: {e}")
        
        return False


def main():
    print_header()
    
    # Check dependencies
    has_torch = check_torch()
    has_pyannote = check_pyannote()
    
    if not has_torch or not has_pyannote:
        print()
        print("Please install missing dependencies first.")
        print()
        print("Run:")
        print("  pip install pyannote.audio torch")
        print()
        return 1
    
    # Setup token
    if not setup_hf_token():
        print()
        print("Setup incomplete. Run this script again to configure.")
        return 1
    
    # Verify
    if not verify_setup():
        print()
        print("Verification failed. Check your token and model access.")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
