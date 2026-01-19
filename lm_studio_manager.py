"""
Whisper Fedora - LM Studio Manager
Control LM Studio server and models via CLI
"""

import subprocess
import shutil
import json
import time
from dataclasses import dataclass
from typing import Optional, List


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class ModelInfo:
    """Information about a downloaded model."""
    path: str           # Full model path (e.g., "lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF")
    name: str           # Display name (e.g., "Meta-Llama-3.1-8B-Instruct")
    size_bytes: int     # Size in bytes
    quantization: str   # Quantization type (e.g., "Q4_K_M")
    architecture: str   # Model architecture (e.g., "llama")
    
    @property
    def size_gb(self) -> float:
        """Get size in gigabytes."""
        return self.size_bytes / (1024 ** 3)
    
    @property
    def display_name(self) -> str:
        """Get a short display name for UI."""
        # Extract just the model name part
        parts = self.path.split('/')
        if len(parts) > 1:
            name = parts[-1].replace('-GGUF', '').replace('.gguf', '')
        else:
            name = self.name
        
        # Add quantization if available
        if self.quantization:
            return f"{name} ({self.quantization})"
        return name


# ============================================================================
# LM STUDIO MANAGER
# ============================================================================

class LMStudioManager:
    """
    Manager for LM Studio CLI operations.
    
    Requires LM Studio CLI to be installed. Install from:
    - LM Studio app → Developer menu → Install CLI
    - Or run: npx lmstudio install-cli
    """
    
    def __init__(self):
        self._cli_path: Optional[str] = None
        self._cached_models: Optional[List[ModelInfo]] = None
    
    # =========================================================================
    # CLI DETECTION
    # =========================================================================
    
    def is_cli_available(self) -> bool:
        """Check if LM Studio CLI is installed and available."""
        return self._get_cli_path() is not None
    
    def _get_cli_path(self) -> Optional[str]:
        """Get the path to the lms CLI."""
        if self._cli_path is not None:
            return self._cli_path
        
        # Check if lms is in PATH
        path = shutil.which('lms')
        if path:
            self._cli_path = path
            return path
        
        # Check common installation locations
        common_paths = [
            '/usr/local/bin/lms',
            '/opt/homebrew/bin/lms',
            '~/.lmstudio/bin/lms',
        ]
        
        import os
        for p in common_paths:
            expanded = os.path.expanduser(p)
            if os.path.isfile(expanded):
                self._cli_path = expanded
                return expanded
        
        return None
    
    def _run_cli(self, args: List[str], timeout: int = 30) -> tuple[bool, str]:
        """
        Run an LM Studio CLI command.
        
        Returns:
            Tuple of (success, output)
        """
        cli_path = self._get_cli_path()
        if not cli_path:
            return False, "LM Studio CLI not found"
        
        try:
            result = subprocess.run(
                [cli_path] + args,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, result.stderr or result.stdout
                
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)
    
    # =========================================================================
    # SERVER CONTROL
    # =========================================================================
    
    def is_server_running(self) -> bool:
        """Check if LM Studio server is running."""
        # Quick HTTP check first (faster than CLI)
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:1234/v1/models")
            with urllib.request.urlopen(req, timeout=2) as response:
                return response.status == 200
        except:
            pass
        
        # Fall back to CLI check
        success, output = self._run_cli(['server', 'status'], timeout=5)
        return success and 'running' in output.lower()
    
    def start_server(self, wait: bool = True, timeout: int = 30) -> bool:
        """
        Start the LM Studio local server.
        
        Args:
            wait: Wait for server to be ready
            timeout: Maximum seconds to wait
            
        Returns:
            True if server started successfully
        """
        if self.is_server_running():
            return True
        
        # Start server in background
        success, output = self._run_cli(['server', 'start'], timeout=10)
        
        if not success:
            print(f"Failed to start server: {output}")
            return False
        
        if wait:
            # Wait for server to be ready
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.is_server_running():
                    return True
                time.sleep(0.5)
            
            return False
        
        return True
    
    def stop_server(self) -> bool:
        """Stop the LM Studio server."""
        success, _ = self._run_cli(['server', 'stop'], timeout=10)
        return success
    
    # =========================================================================
    # MODEL MANAGEMENT
    # =========================================================================
    
    def list_downloaded_models(self, refresh: bool = False) -> List[ModelInfo]:
        """
        Get list of all downloaded models.
        
        Args:
            refresh: Force refresh of cached model list
        """
        if self._cached_models is not None and not refresh:
            return self._cached_models
        
        success, output = self._run_cli(['ls', '--json'], timeout=30)
        
        if not success:
            return []
        
        try:
            data = json.loads(output)
            models = []
            
            for item in data:
                # Handle different possible JSON structures
                if isinstance(item, dict):
                    path = item.get('path', item.get('id', ''))
                    name = item.get('name', path.split('/')[-1] if '/' in path else path)
                    size = item.get('size', item.get('sizeBytes', 0))
                    quant = item.get('quantization', '')
                    arch = item.get('architecture', '')
                    
                    models.append(ModelInfo(
                        path=path,
                        name=name,
                        size_bytes=size,
                        quantization=quant,
                        architecture=arch
                    ))
            
            self._cached_models = models
            return models
            
        except json.JSONDecodeError:
            # Try line-by-line parsing for simpler output
            models = []
            for line in output.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith(('#', '-')):
                    models.append(ModelInfo(
                        path=line,
                        name=line.split('/')[-1] if '/' in line else line,
                        size_bytes=0,
                        quantization='',
                        architecture=''
                    ))
            
            self._cached_models = models
            return models
    
    def list_loaded_models(self) -> List[str]:
        """Get list of currently loaded model identifiers."""
        success, output = self._run_cli(['ps', '--json'], timeout=10)
        
        if not success:
            return []
        
        try:
            data = json.loads(output)
            if isinstance(data, list):
                return [item.get('id', item.get('path', '')) for item in data if isinstance(item, dict)]
            return []
        except json.JSONDecodeError:
            return []
    
    def load_model(
        self,
        model_path: str,
        gpu: str = "auto",
        context_length: Optional[int] = None,
        wait: bool = True,
        timeout: int = 120
    ) -> bool:
        """
        Load a model into memory.
        
        Args:
            model_path: Path to the model (as returned by list_downloaded_models)
            gpu: GPU configuration ("auto", "max", "off", or 0.0-1.0)
            context_length: Optional context length override
            wait: Wait for model to be fully loaded
            timeout: Maximum seconds to wait
            
        Returns:
            True if model loaded successfully
        """
        args = ['load', model_path, f'--gpu={gpu}']
        
        if context_length:
            args.append(f'--context-length={context_length}')
        
        if not wait:
            args.append('--yes')  # Non-interactive mode
        
        success, output = self._run_cli(args, timeout=timeout)
        
        if not success:
            print(f"Failed to load model: {output}")
            return False
        
        return True
    
    def unload_all(self) -> bool:
        """Unload all currently loaded models."""
        success, _ = self._run_cli(['unload', '--all'], timeout=30)
        return success
    
    # =========================================================================
    # HIGH-LEVEL HELPERS
    # =========================================================================
    
    def ensure_ready(self, auto_load_model: bool = True) -> bool:
        """
        Ensure LM Studio is ready for inference.
        
        This will:
        1. Check if CLI is available
        2. Start server if not running
        3. Optionally load a model if none is loaded
        
        Returns:
            True if LM Studio is ready
        """
        if not self.is_cli_available():
            return False
        
        if not self.start_server():
            return False
        
        if auto_load_model:
            loaded = self.list_loaded_models()
            if not loaded:
                # Try to load the first available model
                model = self.get_recommended_model()
                if model:
                    return self.load_model(model.path)
                return False
        
        return True
    
    def get_recommended_model(self) -> Optional[ModelInfo]:
        """
        Get a recommended model to load.
        
        Prefers higher quality quantizations (Q8, Q6) for best results.
        """
        models = self.list_downloaded_models()
        
        if not models:
            return None
        
        # Prefer higher quality quantizations (Q8 > Q6 > Q5 > Q4)
        preferred_quants = ['Q8', 'Q6_K', 'Q5_K_M', 'Q5_K_S', 'Q4_K_M', 'Q4_K_S', '8bit', '4bit']
        
        for quant in preferred_quants:
            for model in models:
                if quant.lower() in str(model.quantization).lower():
                    return model
        
        # Return the first model if no preferred quantization found
        return models[0] if models else None
    
    def get_current_model(self) -> Optional[str]:
        """Get the currently loaded model name, if any."""
        loaded = self.list_loaded_models()
        return loaded[0] if loaded else None


# ============================================================================
# CLI FOR TESTING
# ============================================================================

if __name__ == "__main__":
    manager = LMStudioManager()
    
    print("LM Studio Manager Test")
    print("=" * 50)
    
    # Check CLI
    cli_available = manager.is_cli_available()
    print(f"CLI available: {cli_available}")
    
    if not cli_available:
        print("\nTo install LM Studio CLI:")
        print("  1. Open LM Studio app")
        print("  2. Go to Developer menu")
        print("  3. Click 'Install CLI'")
        exit(1)
    
    # Check server
    server_running = manager.is_server_running()
    print(f"Server running: {server_running}")
    
    # List models
    print("\nDownloaded models:")
    models = manager.list_downloaded_models()
    for model in models[:5]:  # Show first 5
        print(f"  - {model.display_name} ({model.size_gb:.1f} GB)")
    
    if len(models) > 5:
        print(f"  ... and {len(models) - 5} more")
    
    # Show loaded models
    loaded = manager.list_loaded_models()
    if loaded:
        print(f"\nCurrently loaded: {', '.join(loaded)}")
    else:
        print("\nNo models currently loaded")
    
    # Show recommended model
    recommended = manager.get_recommended_model()
    if recommended:
        print(f"\nRecommended model: {recommended.display_name}")
