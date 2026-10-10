"""
Whispered UI - Model Downloader
Dynamic downloader for Whisper models and Pyannote models
"""

import os
import time
from pathlib import Path
from typing import Optional

import requests
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from utils import get_models_dir
from core.base_worker import BaseWorker
from core.i18n import tr
from core.worker_registry import WorkerRegistry


class DownloadWorker(BaseWorker):
    """Worker thread for downloading files with progress tracking."""

    progress = pyqtSignal(int, int)  # (bytes_read, total_bytes)
    finished = pyqtSignal(bool, str) # (success, error_or_path)

    def _disconnect_business_signals(self) -> None:
        """WorkerRegistry hook (see core/worker_registry.py) — ``finished``
        here shadows QThread's own lifecycle signal with a business one, so
        the registry's generic by-name sweep skips it; disconnect
        explicitly so a cancelled download can't still call
        ``_on_download_finished`` after the dialog has moved on."""
        for signal in (self.progress, self.finished):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def __init__(self, url: str, target_path: str, parent=None,
                 manifest_key: Optional[str] = None):
        super().__init__(parent)
        self.url = url
        self.target_path = target_path
        # A model in core/model_manifest.py downloads through ModelRepository:
        # pinned URL, size and sha256 checked, atomic replace.
        self.manifest_key = manifest_key

    def _execute(self) -> None:
        if self.manifest_key is not None:
            self._download_verified(self.manifest_key)
            return

        # Create a temporary file path
        temp_path = self.target_path + ".download"

        # Start download
        try:
            response = requests.get(self.url, stream=True, timeout=10)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # Check if 404
            if e.response.status_code == 404:
                self.finished.emit(False, tr("download_error_not_found"))
            else:
                self.finished.emit(False, tr("download_error_http", error=str(e)))
            return

        total_size = int(response.headers.get('content-length', 0))
        bytes_read = 0

        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if self.is_cancelled():
                    f.close()
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    self.finished.emit(False, "Cancelled")
                    return

                if chunk:
                    f.write(chunk)
                    bytes_read += len(chunk)
                    self.progress.emit(bytes_read, total_size)

        # Rename temp file to target path
        if os.path.exists(self.target_path):
            os.remove(self.target_path)
        os.rename(temp_path, self.target_path)

        self.finished.emit(True, self.target_path)

    def _download_verified(self, key: str) -> None:
        from core.model_repository import Cancelled, IntegrityError, ModelRepository

        repo = ModelRepository(models_dir=Path(self.target_path).parent)
        try:
            path = repo.ensure(
                key,
                progress=lambda done, total: self.progress.emit(done, total),
                cancel=self.is_cancelled,
            )
        except Cancelled:
            self.finished.emit(False, "Cancelled")
            return
        except IntegrityError:
            self.finished.emit(False, tr("download_error_integrity"))
            return
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            self.finished.emit(False, tr("download_error_not_found") if status == 404
                               else tr("download_error_http", error=str(e)))
            return
        self.finished.emit(True, str(path))

    def _on_error(self, msg: str) -> None:
        self.finished.emit(False, tr("download_error_generic", error=msg))


class DiarizationCacheWorker(BaseWorker):
    """Worker thread for initializing pyannote to force model caching."""

    finished = pyqtSignal(bool, str)

    def _disconnect_business_signals(self) -> None:
        """WorkerRegistry hook — see DownloadWorker._disconnect_business_signals."""
        try:
            self.finished.disconnect()
        except (RuntimeError, TypeError):
            pass

    def __init__(self, hf_token: str, parent=None):
        super().__init__(parent)
        self.hf_token = hf_token

    def _execute(self) -> None:
        try:
            # Importing here to prevent main thread blocking and missing dependencies at startup
            import torch  # noqa: F401
            from pyannote.audio import Pipeline
        except ImportError:
            self.finished.emit(False, tr("download_error_diarization_missing"))
            return

        # Loading the pipeline will trigger huggingface_hub to download all required models
        # to the local ~/.cache/huggingface/hub directory if they don't exist
        Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=self.hf_token
        )
        self.finished.emit(True, "Success")

    def _on_error(self, msg: str) -> None:
        self.finished.emit(False, tr("download_error_diarization", error=msg))


class ModelDownloaderDialog(QDialog):
    """Dialog showing download progress for missing models."""

    def __init__(self, model_name: str, is_diarization: bool = False,
                 hf_token: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.model_name = model_name
        self.is_diarization = is_diarization
        self.hf_token = hf_token
        self.download_successful = False

        # Whisper details
        self.target_filename = f"ggml-{model_name}.bin"
        self.target_path = os.path.join(get_models_dir(), self.target_filename)
        # Using huggingface resolve URL for whisper.cpp models
        self.url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{self.target_filename}"

        self.worker = None
        self._registry = WorkerRegistry(parent=self)
        self.start_time = 0

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(tr("download_model_title"))
        self.setFixedSize(450, 180)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Title
        title_text = (
            tr("download_pyannote_title")
            if self.is_diarization
            else tr("download_model_name", model=self.model_name)
        )
        self.title_label = QLabel(title_text)
        self.title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(self.title_label)

        # Info
        info_text = (
            tr("download_pyannote_info")
            if self.is_diarization
            else tr("download_model_info")
        )
        self.info_label = QLabel(info_text)
        self.info_label.setProperty("role", "muted")
        self.info_label.setStyleSheet("font-size: 12px;")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        if self.is_diarization:
            self.progress_bar.setRange(0, 0) # Indeterminate mode for pyannote
        layout.addWidget(self.progress_bar)

        # Stats
        self.stats_label = QLabel(tr("download_starting"))
        self.stats_label.setProperty("role", "muted")
        self.stats_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.stats_label)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton(tr("btn_cancel"))
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)

    def start_download(self):
        """Start the background download."""
        self.start_time = time.time()

        if self.is_diarization:
            # Pyannote caching
            self.worker = DiarizationCacheWorker(self.hf_token, parent=self)
            self.worker.finished.connect(self._on_download_finished)
            self._registry.register(self.worker, name="model_download")
            self.worker.start()
        else:
            # Whisper downloading
            from core.model_manifest import whisper_entry

            entry = whisper_entry(self.model_name)
            self.worker = DownloadWorker(
                self.url, self.target_path, parent=self,
                manifest_key=entry.key if entry is not None else None,
            )
            self.worker.progress.connect(self._on_progress)
            self.worker.finished.connect(self._on_download_finished)
            self._registry.register(self.worker, name="model_download")
            self.worker.start()

    def _on_progress(self, bytes_read: int, total_bytes: int):
        """Update progress bar and stats."""
        if total_bytes > 0:
            percent = int((bytes_read / total_bytes) * 100)
            self.progress_bar.setValue(percent)

            # Calculate speed
            elapsed = time.time() - self.start_time
            if elapsed > 0:
                speed_bps = bytes_read / elapsed
                speed_mbps = speed_bps / (1024 * 1024)

                downloaded_mb = bytes_read / (1024 * 1024)
                total_mb = total_bytes / (1024 * 1024)

                self.stats_label.setText(
                    tr(
                        "download_progress",
                        downloaded=f"{downloaded_mb:.1f}",
                        total=f"{total_mb:.1f}",
                        speed=f"{speed_mbps:.1f}",
                    )
                )
        else:
            # Unknown total size
            downloaded_mb = bytes_read / (1024 * 1024)
            self.stats_label.setText(
                tr("download_progress_unknown", downloaded=f"{downloaded_mb:.1f}")
            )

    def _on_download_finished(self, success: bool, message: str):
        """Handle download completion."""
        if success:
            self.download_successful = True
            self.accept()
        else:
            if message != "Cancelled":
                QMessageBox.critical(
                    self,
                    tr("download_failed_title"),
                    tr("download_failed_message", error=message),
                )
            self.reject()

    def _on_cancel(self):
        """Cancel download.

        Only DownloadWorker's ``_execute`` loop actually polls
        ``is_cancelled()``; DiarizationCacheWorker's pyannote pipeline load
        has no cancellation point once started (a real limitation of
        huggingface_hub's blocking download, not something a flag flip can
        fix), so cancel() is called for both cases but only DownloadWorker
        can react to it before its own natural completion. Either way, the
        dialog must not close (and Python must not drop the worker's last
        reference) while it's still running — that combination is what Qt
        aborts the process for. Wait for the QThread to actually finish
        before rejecting.
        """
        if not self.worker or not self.worker.isRunning():
            self.reject()
            return
        self.worker.cancel()
        self.cancel_btn.setEnabled(False)
        self.stats_label.setText(tr("progress_cancelling"))
        self._registry.retire(self.worker)
        QThread.finished.__get__(self.worker, type(self.worker)).connect(self.reject)
