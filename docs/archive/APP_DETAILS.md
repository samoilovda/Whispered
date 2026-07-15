# Whispered: AI-Powered Privacy-First Transcription & Content Generation

## 🚀 Overview
**Whispered** (formerly Whisper Fedora) is a high-performance desktop application designed to bridge the gap between raw audio/video recordings and publishable written content. It is built for professionals—journalists, podcasters, researchers, and content creators—who need to transform interviews, meetings, or lectures into structured text efficiently and securely.

## 🛡️ The Privacy-First Advantage
Unlike most AI transcription services, **Whispered operates entirely offline**. By leveraging local hardware acceleration and local LLMs, your sensitive data never leaves your machine. 
- **No Cloud Uploads**: Your recordings stay on your drive.
- **No Subscription Fees**: Use your own hardware to process as much as you need.
- **Complete Control**: You choose which models to use and how your data is handled.

## ✨ Key Features
- **State-of-the-Art Transcription**: Powered by `whisper.cpp`, providing near-instant transcription with support for **Apple Metal**, **NVIDIA CUDA**, and **AMD ROCm** acceleration.

### 🔴 AMD GPU (ROCm) Support on Fedora
To enable hardware acceleration for AMD Radeon GPUs, install the ROCm stack before running the setup script:

1. Install ROCm dependencies:
   ```bash
   sudo dnf install rocm-opencl rocm-hip rocm-runtime rocminfo
   ```
2. Add your user to the `render` and `video` groups to access the GPU:
   ```bash
   sudo usermod -aG render,video $USER
   ```
3. Log out and log back in, or restart your computer.
4. Run `./setup.sh`. The script will detect ROCm and compile Whisper with AMD support.
- **Smart Diarization (Speaker ID)**: Automatically identify and label different speakers using `pyannote.audio`, turning a wall of text into a readable script.
- **AI Content Engine**: Integration with **LM Studio** allows you to:
  - ✨ **Clean Text**: Remove fillers ("um", "uh"), fix grammar, and restore natural paragraph flow.
  - 📝 **Generate Articles**: Instantly convert transcripts into Blog Posts, FAQs, Listicles, or Summaries.
  - 📱 **Social Media Snippets**: Create catchy posts for LinkedIn, X (Twitter), or Telegram.
- **Batch Processing**: Queue up multiple files for overnight processing.
- **Multi-Format Export**: Save your work in **Markdown** or **HTML** for easy integration with CMS platforms like WordPress or Ghost.

## 🛠️ Technical Stack
- **Core**: Python 3.10+
- **UI**: PyQt6 with a bespoke, responsive midnight theme.
- **Audio Engine**: FFmpeg for high-fidelity extraction and 16kHz optimization.
- **Inference**: High-performance C++ implementation of OpenAI's Whisper.
- **Local AI**: OpenAI-compatible local API integration (LM Studio/LocalAI).

## 📖 How to Use
1. **Select**: Drag and drop any common media format (MP3, MP4, WAV, MKV, etc.).
2. **Transcribe**: Choose your model (Turbo, Large, Base) and let the GPU do the heavy lifting.
3. **Refine**: Use the AI Panel to clean the text or generate specific content formats.
4. **Publish**: Export the final result as a ready-to-go Markdown file.

---
*Whispered: Your local, secure, and powerful companion for voice-to-content automation.*
