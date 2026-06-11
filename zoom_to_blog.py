#!/usr/bin/env python3
"""
Zoom to Blog Workflow Automation

Automates the pipeline:
  Zoom recordings → Whisper transcription → LM Studio topic extraction → Blog-ready content

Usage:
  python zoom_to_blog.py /path/to/zoom_recording.mp4 [options]

Requirements:
  - ffmpeg (for audio extraction)
  - openai-whisper (brew install openai-whisper)
  - LM Studio running locally with API server enabled on port 1234
"""

import os
import sys
import json
import argparse
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error


# ============================================================================
# CONFIGURATION
# ============================================================================

# Whisper.cpp paths
WHISPER_CPP_BIN = os.path.expanduser("~/whisper.cpp/build/bin/whisper-cli")
WHISPER_CPP_MODELS_DIR = os.path.expanduser("~/whisper.cpp/models")

# Model mapping: friendly name -> ggml filename
WHISPER_MODELS = {
    "turbo": "ggml-large-v3-turbo.bin",
    "turbo-q5": "ggml-large-v3-turbo-q5_0.bin",
    "base.en": "ggml-base.en.bin",
}

DEFAULT_WHISPER_MODEL = "turbo"
DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
DEFAULT_OUTPUT_DIR = "./output"
DEFAULT_LANGUAGE = "auto"


# Blog generation prompts
TOPIC_EXTRACTION_PROMPT = """Analyze this transcription from a recorded session and extract:
1. Main topics discussed (list 3-7 key topics)
2. Key insights or takeaways
3. Notable quotes or statements

Transcription:
{transcription}

Respond in JSON format:
{{
  "topics": ["topic1", "topic2", ...],
  "insights": ["insight1", "insight2", ...],
  "quotes": ["quote1", "quote2", ...]
}}"""

BLOG_GENERATION_PROMPT = """Based on this session transcription and extracted topics, create a blog post draft.

Topics: {topics}
Key Insights: {insights}

Full Transcription:
{transcription}

Create a well-structured blog post with:
1. An engaging title
2. Introduction paragraph
3. Main content sections based on the topics
4. Key takeaways section
5. Conclusion

Write in a conversational but informative style. Use markdown formatting."""

SOCIAL_SNIPPETS_PROMPT = """Based on this blog content, create 3 short social media snippets (tweets/posts) that:
1. Highlight key insights
2. Are engaging and shareable
3. Are under 280 characters each

Blog content:
{blog_content}

Format as a numbered list."""


# ============================================================================
# AUDIO EXTRACTION
# ============================================================================

def extract_audio(input_file: str, output_wav: str) -> bool:
    """Extract audio from video file and convert to 16kHz WAV."""
    print(f"📼 Extracting audio from: {input_file}")
    
    cmd = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-vn",  # No video
        "-acodec", "pcm_s16le",  # 16-bit PCM
        "-ar", "16000",  # 16kHz sample rate
        "-ac", "1",  # Mono
        output_wav
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"✅ Audio extracted to: {output_wav}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to extract audio: {e.stderr}")
        return False
    except FileNotFoundError:
        print("❌ ffmpeg not found. Please install it: brew install ffmpeg")
        return False


# ============================================================================
# WHISPER TRANSCRIPTION (whisper.cpp)
# ============================================================================

def get_model_path(model_name: str) -> str:
    """Get full path to whisper model file."""
    if model_name in WHISPER_MODELS:
        return os.path.join(WHISPER_CPP_MODELS_DIR, WHISPER_MODELS[model_name])
    # Allow direct model filename
    return os.path.join(WHISPER_CPP_MODELS_DIR, model_name)


def transcribe_audio(
    audio_file: str,
    output_dir: str,
    model: str = DEFAULT_WHISPER_MODEL,
    language: str = DEFAULT_LANGUAGE
) -> Optional[str]:
    """Transcribe audio using whisper.cpp (fast, with Metal GPU acceleration)."""
    
    model_path = get_model_path(model)
    
    if not os.path.exists(WHISPER_CPP_BIN):
        print(f"❌ whisper.cpp not found at: {WHISPER_CPP_BIN}")
        print("   Build it with: cd ~/whisper.cpp && make")
        return None
    
    if not os.path.exists(model_path):
        print(f"❌ Model not found: {model_path}")
        print(f"   Available models in {WHISPER_CPP_MODELS_DIR}:")
        for name, filename in WHISPER_MODELS.items():
            full_path = os.path.join(WHISPER_CPP_MODELS_DIR, filename)
            status = "✓" if os.path.exists(full_path) else "✗"
            print(f"     {status} {name} -> {filename}")
        return None
    
    print(f"🎤 Transcribing with whisper.cpp (model: {model})...")
    print(f"   Using Metal GPU acceleration")
    
    # Build output file path (whisper.cpp adds extension automatically)
    output_base = os.path.join(output_dir, "transcription")
    
    cmd = [
        WHISPER_CPP_BIN,
        "-m", model_path,
        "-f", audio_file,
        "-of", output_base,  # Output file (without extension)
        "-otxt",             # Output as .txt
        "-np",               # No prints (cleaner output)
    ]
    
    if language != "auto":
        cmd.extend(["-l", language])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        # Read the output txt file
        txt_file = f"{output_base}.txt"
        
        if os.path.exists(txt_file):
            with open(txt_file, 'r', encoding='utf-8') as f:
                transcription = f.read().strip()
            print(f"✅ Transcription complete: {len(transcription)} characters")
            return transcription
        else:
            print(f"❌ Transcription file not found: {txt_file}")
            # Show any output for debugging
            if result.stdout:
                print(f"   stdout: {result.stdout[:500]}")
            if result.stderr:
                print(f"   stderr: {result.stderr[:500]}")
            return None
            
    except subprocess.CalledProcessError as e:
        print(f"❌ Transcription failed:")
        if e.stderr:
            print(f"   {e.stderr[:500]}")
        return None
    except Exception as e:
        print(f"❌ Error running whisper.cpp: {e}")
        return None



# ============================================================================
# LM STUDIO API
# ============================================================================

def call_lm_studio(
    prompt: str,
    lm_studio_url: str = DEFAULT_LM_STUDIO_URL,
    max_tokens: int = 4096
) -> Optional[str]:
    """Call LM Studio's OpenAI-compatible API."""
    
    endpoint = f"{lm_studio_url}/chat/completions"
    
    payload = {
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": False
    }
    
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                'Content-Type': 'application/json'
            }
        )
        
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result['choices'][0]['message']['content']
            
    except urllib.error.URLError as e:
        print(f"❌ LM Studio connection failed: {e}")
        print("   Make sure LM Studio is running with the local server enabled on port 1234")
        return None
    except Exception as e:
        print(f"❌ LM Studio API error: {e}")
        return None


def check_lm_studio_connection(lm_studio_url: str = DEFAULT_LM_STUDIO_URL) -> bool:
    """Check if LM Studio server is running."""
    try:
        req = urllib.request.Request(f"{lm_studio_url}/models")
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


# ============================================================================
# CONTENT GENERATION
# ============================================================================

def extract_topics(transcription: str, lm_studio_url: str) -> Optional[dict]:
    """Extract topics from transcription using LM Studio."""
    print("🔍 Extracting topics...")
    
    prompt = TOPIC_EXTRACTION_PROMPT.format(transcription=transcription)  # Process full text
    response = call_lm_studio(prompt, lm_studio_url)
    
    if response:
        try:
            # Try to parse JSON from response
            # Handle case where response might have markdown code blocks
            clean_response = response.strip()
            if clean_response.startswith("```"):
                clean_response = clean_response.split("```")[1]
                if clean_response.startswith("json"):
                    clean_response = clean_response[4:]
            
            topics = json.loads(clean_response)
            print(f"✅ Extracted {len(topics.get('topics', []))} topics")
            return topics
        except json.JSONDecodeError:
            # Return raw response if not valid JSON
            return {"raw": response, "topics": [], "insights": [], "quotes": []}
    
    return None


def generate_blog_post(transcription: str, topics: dict, lm_studio_url: str) -> Optional[str]:
    """Generate blog post draft using LM Studio."""
    print("📝 Generating blog post...")
    
    prompt = BLOG_GENERATION_PROMPT.format(
        topics=", ".join(topics.get('topics', [])),
        insights="\n".join(f"- {i}" for i in topics.get('insights', [])),
        transcription=transcription  # Process full text
    )
    
    response = call_lm_studio(prompt, lm_studio_url)
    if response:
        print("✅ Blog post generated")
    return response


def generate_social_snippets(blog_content: str, lm_studio_url: str) -> Optional[str]:
    """Generate social media snippets from blog content."""
    print("📱 Generating social snippets...")
    
    prompt = SOCIAL_SNIPPETS_PROMPT.format(blog_content=blog_content[:4000])
    response = call_lm_studio(prompt, lm_studio_url)
    if response:
        print("✅ Social snippets generated")
    return response


# ============================================================================
# MAIN WORKFLOW
# ============================================================================

def run_workflow(
    input_file: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    language: str = DEFAULT_LANGUAGE,
    lm_studio_url: str = DEFAULT_LM_STUDIO_URL,
    skip_lm: bool = False
) -> bool:
    """Run the complete workflow."""
    
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"❌ Input file not found: {input_file}")
        return False
    
    # Create output directory
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    session_name = f"{input_path.stem}_{timestamp}"
    session_dir = Path(output_dir) / session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*60}")
    print(f"🚀 ZOOM TO BLOG WORKFLOW")
    print(f"{'='*60}")
    print(f"📁 Input: {input_file}")
    print(f"📂 Output: {session_dir}")
    print(f"{'='*60}\n")
    
    # Step 1: Extract audio
    audio_file = session_dir / "audio.wav"
    if input_path.suffix.lower() in ['.wav']:
        # Already WAV, just copy/link
        audio_file = input_path
    elif input_path.suffix.lower() in ['.mp3', '.m4a', '.ogg', '.flac']:
        # Audio file, convert to WAV
        if not extract_audio(str(input_path), str(audio_file)):
            return False
    else:
        # Video file, extract audio
        if not extract_audio(str(input_path), str(audio_file)):
            return False
    
    # Step 2: Transcribe
    transcription = transcribe_audio(
        str(audio_file),
        str(session_dir),
        model=whisper_model,
        language=language
    )
    
    if not transcription:
        print("❌ Transcription failed")
        return False
    
    # Save transcription
    transcription_file = session_dir / "transcription.txt"
    with open(transcription_file, 'w', encoding='utf-8') as f:
        f.write(transcription)
    
    if skip_lm:
        print("\n✅ Workflow complete (LM Studio skipped)")
        print(f"📄 Transcription saved to: {transcription_file}")
        return True
    
    # Step 3: Check LM Studio connection
    print("\n🔌 Checking LM Studio connection...")
    if not check_lm_studio_connection(lm_studio_url):
        print("⚠️  LM Studio not available. Saving transcription only.")
        print("   To process with AI, start LM Studio and run again with --continue flag")
        return True
    print("✅ LM Studio connected")
    
    # Step 4: Extract topics
    topics = extract_topics(transcription, lm_studio_url)
    if topics:
        topics_file = session_dir / "topics.json"
        with open(topics_file, 'w', encoding='utf-8') as f:
            json.dump(topics, f, ensure_ascii=False, indent=2)
    else:
        topics = {"topics": [], "insights": [], "quotes": []}
    
    # Step 5: Generate blog post
    blog_content = generate_blog_post(transcription, topics, lm_studio_url)
    if blog_content:
        blog_file = session_dir / "blog_draft.md"
        with open(blog_file, 'w', encoding='utf-8') as f:
            f.write(blog_content)
    
    # Step 6: Generate social snippets
    if blog_content:
        snippets = generate_social_snippets(blog_content, lm_studio_url)
        if snippets:
            snippets_file = session_dir / "social_snippets.txt"
            with open(snippets_file, 'w', encoding='utf-8') as f:
                f.write(snippets)
    
    print(f"\n{'='*60}")
    print("✅ WORKFLOW COMPLETE!")
    print(f"{'='*60}")
    print(f"📂 Output directory: {session_dir}")
    print(f"\nGenerated files:")
    for f in session_dir.iterdir():
        print(f"  • {f.name}")
    print(f"{'='*60}\n")
    
    return True


# ============================================================================
# CLI
# ============================================================================

def main():
    # Get available models
    available_models = list(WHISPER_MODELS.keys())
    
    parser = argparse.ArgumentParser(
        description="Zoom to Blog Workflow Automation (using whisper.cpp)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  %(prog)s recording.mp4
  %(prog)s recording.mp4 --whisper-model turbo
  %(prog)s recording.mp4 --language ru
  %(prog)s recording.mp4 --skip-lm  (transcription only)

Available models: {', '.join(available_models)}
        """
    )
    
    parser.add_argument(
        "input",
        help="Input video/audio file (mp4, m4a, mp3, wav, etc.)"
    )
    parser.add_argument(
        "--whisper-model", "-m",
        default=DEFAULT_WHISPER_MODEL,
        choices=available_models,
        help=f"Whisper model (default: {DEFAULT_WHISPER_MODEL})"
    )
    parser.add_argument(
        "--language", "-l",
        default=DEFAULT_LANGUAGE,
        help=f"Transcription language code or 'auto' (default: {DEFAULT_LANGUAGE})"
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--lm-studio-url",
        default=DEFAULT_LM_STUDIO_URL,
        help=f"LM Studio API URL (default: {DEFAULT_LM_STUDIO_URL})"
    )
    parser.add_argument(
        "--skip-lm",
        action="store_true",
        help="Skip LM Studio processing (transcription only)"
    )
    
    args = parser.parse_args()
    
    success = run_workflow(
        input_file=args.input,
        output_dir=args.output_dir,
        whisper_model=args.whisper_model,
        language=args.language,
        lm_studio_url=args.lm_studio_url,
        skip_lm=args.skip_lm
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
