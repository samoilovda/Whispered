import os
import sys
import subprocess
import shutil

def main():
    print("==================================================")
    print("🚀 Starting Optimized Build Process for Whispered")
    print("==================================================")
    
    # 1. Clean previous builds
    for dir_name in ['build', 'dist']:
        if os.path.exists(dir_name):
            print(f"🧹 Cleaning {dir_name}/ directory...")
            try:
                shutil.rmtree(dir_name)
            except Exception as e:
                print(f"Warning: Could not remove {dir_name}/: {e}")
            
    for spec_file in ['Whisper Fedora.spec', 'Whispered.spec', 'main.spec']:
        if os.path.exists(spec_file):
            try:
                os.remove(spec_file)
            except OSError as e:
                print(f"Warning: Could not remove {spec_file}: {e}")
        
    # 2. Base arguments for PyInstaller
    args = [
        sys.executable,
        '-m',
        'PyInstaller',
        '--noconfirm',
        '--clean',
        '--name=Whispered',  # Cleaner application name
        '--windowed',
        '--add-data=ui:ui',
        # CRITICAL: We deliberately omit --add-data "models:models" here
        # Models are now downloaded dynamically at runtime to save gigabytes of space!
        '--hidden-import=PyQt6',
        '--hidden-import=pyqtdarktheme',
        '--hidden-import=pywhispercpp',
    ]
    
    # Check for icons depending on platform
    if sys.platform == 'darwin' and os.path.exists('macos/whisper-fedora.icns'):
        args.append('--icon=macos/whisper-fedora.icns')
    elif sys.platform == 'win32' and os.path.exists('windows/whisper-fedora.ico'):
        args.append('--icon=windows/whisper-fedora.ico')
    elif sys.platform == 'linux' and os.path.exists('appimage/whisper-fedora.png'):
        args.append('--icon=appimage/whisper-fedora.png')
        
    # 3. Aggressive Exclusions to reduce bloat
    excludes = [
        'matplotlib',
        'tkinter',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'unittest',
        'pydoc',
        'tensorboard',
        'triton',
        'sphinx',
        'PySide6',
        'PyQt5',
        'wx',
        'cv2' # Exclude OpenCV unless needed
    ]
    
    # 4. OS-specific Exclusions (CUDA/ROCm)
    if sys.platform == 'darwin':
        print("\n🍎 macOS detected: Excluding NVIDIA CUDA and AMD ROCm dependencies...")
        excludes.extend(['torch.cuda', 'nvidia', 'triton', 'torch.backends.cuda'])
        
        print(f"\n💡 [TIP for macOS Size Reduction]")
        print(f"To drastically reduce the macOS build size from ~3GB to ~300MB,")
        print(f"make sure your virtual environment has the CPU/MPS-only version of PyTorch:")
        print(f"   pip uninstall -y torch torchvision torchaudio")
        print(f"   pip install torch torchvision torchaudio")
        
    elif sys.platform == 'linux':
        print("\n🐧 Linux detected.")
        print(f"\n💡 [TIP for Linux Size Reduction]")
        print(f"If you don't need NVIDIA GPU support on Linux, install CPU-only PyTorch:")
        print(f"   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu")
    
    elif sys.platform == 'win32':
        print("\n🪟 Windows detected.")
        print(f"\n💡 [TIP for Windows Size Reduction]")
        print(f"If you don't have an NVIDIA GPU, install CPU-only PyTorch to save space:")
        print(f"   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu")

    # Append all exclusion rules
    for ex in excludes:
        args.append(f'--exclude-module={ex}')
        
    # 5. Collect necessary C/C++ libraries for the whisper wrapper
    args.append('--collect-all=pywhispercpp')
    
    # Target entry point
    args.append('main.py')
    
    print(f"\n⚙️ Running PyInstaller with {len(excludes)} exclusions...")
    # print(f"Command: {' '.join(args)}\n")
    
    # Run PyInstaller
    try:
        subprocess.run(args, check=True)
        print("\n✅ Build completed successfully!")
        
        # Output info
        if sys.platform == 'darwin':
            dist_path = os.path.join('dist', 'Whispered.app')
        elif sys.platform == 'win32':
            dist_path = os.path.join('dist', 'Whispered')
        else:
            dist_path = os.path.join('dist', 'Whispered')
            
        print(f"📂 Output located at: {dist_path}")
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Build failed with return code {e.returncode}")
        sys.exit(1)

if __name__ == "__main__":
    main()
