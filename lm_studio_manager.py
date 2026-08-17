"""
Whispered - LM Studio Manager
Control LM Studio server and models via CLI
"""

import subprocess
import shutil
import json
import time
import threading
from dataclasses import dataclass
from typing import Optional, List

from core.logger import get_logger


logger = get_logger(__name__)


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
# HELPERS
# ============================================================================

def _parse_model_entry(item: object) -> "ModelInfo | None":
    """Parse a single model entry from the LM Studio JSON output.

    Returns ``None`` (and logs a warning) if the entry is not a dict or has
    unexpected field types so the caller can filter bad entries out instead
    of crashing on attribute access later.
    """
    if not isinstance(item, dict):
        logger.warning("lm_studio: unexpected model entry type %s, skipping", type(item).__name__)
        return None

    raw_path = item.get('path', item.get('id', ''))
    if not isinstance(raw_path, str):
        logger.warning("lm_studio: model entry 'path' is not a string (%r), skipping", raw_path)
        return None

    raw_name = item.get('name', raw_path.split('/')[-1] if '/' in raw_path else raw_path)
    if not isinstance(raw_name, str):
        logger.warning("lm_studio: model entry 'name' is not a string (%r), using path", raw_name)
        raw_name = raw_path.split('/')[-1] if '/' in raw_path else raw_path

    raw_size = item.get('size', item.get('sizeBytes', 0))
    if not isinstance(raw_size, int):
        try:
            raw_size = int(raw_size) if raw_size is not None else 0
        except (TypeError, ValueError):
            logger.warning("lm_studio: model 'size_bytes' is not int (%r), using 0", raw_size)
            raw_size = 0

    return ModelInfo(
        path=raw_path,
        name=raw_name,
        size_bytes=raw_size,
        quantization=str(item.get('quantization', '') or ''),
        architecture=str(item.get('architecture', '') or ''),
    )


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

    def _run_cli(
        self,
        args: List[str],
        timeout: int = 30,
        cancel_event: Optional[threading.Event] = None,
    ) -> tuple[bool, str]:
        """
        Run an LM Studio CLI command.

        Returns:
            Tuple of (success, output)
        """
        cli_path = self._get_cli_path()
        if not cli_path:
            return False, "LM Studio CLI not found"

        try:
            process = subprocess.Popen(
                [cli_path] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + timeout
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self._stop_process(process)
                    return False, "Cancelled"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stop_process(process)
                    return False, "Command timed out"
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                    output = stderr or stdout
                    return process.returncode == 0, output
                except subprocess.TimeoutExpired:
                    continue
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        """Terminate a CLI child promptly, escalating only when necessary."""
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    # =========================================================================
    # SERVER CONTROL
    # =========================================================================

    def is_server_running(
        self, cancel_event: Optional[threading.Event] = None
    ) -> bool:
        """Check if LM Studio server is running."""
        # Quick HTTP check first (faster than CLI)
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:1234/v1/models")
            with urllib.request.urlopen(req, timeout=1) as response:
                return response.status == 200
        except Exception:
            pass

        # Fall back to CLI check
        if cancel_event is not None and cancel_event.is_set():
            return False
        success, output = self._run_cli(
            ['server', 'status'], timeout=5, cancel_event=cancel_event
        )
        return success and 'running' in output.lower()

    def start_server(
        self,
        wait: bool = True,
        timeout: int = 30,
        cancel_event: Optional[threading.Event] = None,
    ) -> bool:
        """
        Start the LM Studio local server.

        Args:
            wait: Wait for server to be ready
            timeout: Maximum seconds to wait

        Returns:
            True if server started successfully
        """
        if self.is_server_running(cancel_event=cancel_event):
            return True

        # Start server in background
        success, output = self._run_cli(
            ['server', 'start'], timeout=10, cancel_event=cancel_event
        )

        if not success:
            if output != "Cancelled":
                logger.warning("Failed to start LM Studio server: %s", output)
            return False

        if wait:
            # Wait for server to be ready
            start_time = time.time()
            while time.time() - start_time < timeout:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                if self.is_server_running(cancel_event=cancel_event):
                    return True
                if cancel_event is not None:
                    cancel_event.wait(0.5)
                else:
                    time.sleep(0.5)

            return False

        return True


    # =========================================================================
    # MODEL MANAGEMENT
    # =========================================================================

    def list_downloaded_models(
        self,
        refresh: bool = False,
        cancel_event: Optional[threading.Event] = None,
    ) -> List[ModelInfo]:
        """
        Get list of all downloaded models.

        Args:
            refresh: Force refresh of cached model list
        """
        if self._cached_models is not None and not refresh:
            return self._cached_models

        success, output = self._run_cli(
            ['ls', '--json'], timeout=30, cancel_event=cancel_event
        )

        if not success:
            return []

        try:
            data = json.loads(output)
            models: list[ModelInfo] = []

            # Accept either a bare list or a dict wrapping the list under a
            # common key (e.g. {"data": [...]} / {"models": [...]}).
            if isinstance(data, dict):
                for key in ('data', 'models', 'items'):
                    if isinstance(data.get(key), list):
                        data = data[key]
                        break
                else:
                    data = []

            models.extend(filter(None, (_parse_model_entry(item) for item in data)))

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

    def list_loaded_models(
        self, cancel_event: Optional[threading.Event] = None
    ) -> List[str]:
        """Get list of currently loaded model identifiers."""
        success, output = self._run_cli(
            ['ps', '--json'], timeout=10, cancel_event=cancel_event
        )

        if not success:
            return []

        try:
            data = json.loads(output)
            if isinstance(data, list):
                return [
                    str(item.get('id', item.get('path', '')))
                    for item in data
                    if isinstance(item, dict)
                ]
            return []
        except json.JSONDecodeError:
            return []

    def load_model(
        self,
        model_path: str,
        gpu: str = "auto",
        context_length: Optional[int] = None,
        wait: bool = True,
        timeout: int = 120,
        cancel_event: Optional[threading.Event] = None,
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

        # Never let a GUI-launched CLI command open an interactive picker.
        args.append('--yes')

        success, output = self._run_cli(
            args, timeout=timeout, cancel_event=cancel_event
        )

        if not success:
            if output != "Cancelled":
                logger.warning("Failed to load LM Studio model: %s", output)
            return False

        return True


    # =========================================================================
    # HIGH-LEVEL HELPERS
    # =========================================================================


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

    def get_current_model(
        self, cancel_event: Optional[threading.Event] = None
    ) -> Optional[str]:
        """Get the currently loaded model name, if any."""
        loaded = self.list_loaded_models(cancel_event=cancel_event)
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
