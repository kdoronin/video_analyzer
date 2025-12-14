# Video Analyzer Web

AI-powered video analysis with Google Gemini and OpenRouter. Analyze videos using various specialized prompts for different content types.

## Features

- **Dual Provider Support**: Choose between Google Gemini (direct API) or OpenRouter (multiple models)
- **Dynamic Model Selection**: Fetches available models from providers in real-time
- **Web Interface**: Modern, responsive UI for easy video analysis
- **9 Specialized Prompts**: Different analysis types for various video content
- **Runtime API Key Configuration**: Enter API keys via web interface (no restart needed)
- **Video Chunking**: Automatically splits long videos for processing
- **Key Frame Extraction**: Optional extraction of important video frames
- **Docker Support**: Easy deployment with Docker

## Quick Start

### Using Docker (Recommended)

1. Clone the repository:
```bash
git clone <repository-url>
cd video_analyzer_web
```

2. (Optional) Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your API keys (or enter them via web interface)
```

3. Build and run:
```bash
docker-compose up -d
```

4. Open http://localhost:8000 in your browser

### Manual Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Install FFmpeg:
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Windows
# Download from https://ffmpeg.org/download.html
```

3. Configure environment:
```bash
cp .env.example .env
# Edit .env with your settings
```

4. Run the application:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Usage

### 1. Select AI Provider

Choose between:
- **Google Gemini**: Direct access to Google's Gemini models
- **OpenRouter**: Access to multiple AI providers through one API

If the API key is not configured, you'll be prompted to enter it in the web interface.

### 2. Upload Video

- Drag and drop your video file or click to browse
- Supported formats: MP4, AVI, MOV, MKV, WebM, M4V, WMV, FLV
- Maximum file size: 500MB (configurable)

### 3. Select Analysis Type

Choose from 9 specialized analysis types:

| Type | Best For |
|------|----------|
| General Analysis | Any video content |
| Lecture / Educational | Online courses, educational content |
| Tutorial / How-to | Step-by-step guides |
| Marketing / Product Demo | Advertisements, product videos |
| Presentation / Pitch | Business presentations |
| Meeting / Standup | Work meetings, team calls |
| Interview Evaluation | Job interviews (detailed scoring) |
| Language Lesson | Language instruction (full transcript) |
| Voiceover / Sound Design | Generate AI music/SFX prompts |

### 4. Customize Prompt (Optional)

- View the default prompt for your selected type
- Override with a custom prompt if needed
- Enable key frame extraction for timestamp-marked frames

### 5. Start Analysis

Click "Start Analysis" and wait for the results. Long videos are automatically split into chunks.

## API Reference

### GET /api/config
Get current configuration status.

### POST /api/set-api-key
Set API key for a provider.
```json
{
  "provider": "gemini",
  "api_key": "your-api-key"
}
```

### GET /api/video-types
Get available video analysis types.

### GET /api/prompt/{video_type}
Get prompt template for a video type.

### GET /api/models/{provider}
Get available models for a provider (gemini or openrouter).

### POST /api/upload
Upload a video file. Returns file info.

### POST /api/analyze
Start video analysis job.

### GET /api/job/{job_id}
Get status of an analysis job.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANALYZER_TYPE` | `gemini` | Default provider (gemini or openrouter) |
| `GEMINI_API_KEY` | - | Google Gemini API key |
| `GEMINI_MODEL_NAME` | `gemini-2.0-flash` | Default Gemini model |
| `OPENROUTER_API_KEY` | - | OpenRouter API key |
| `OPENROUTER_MODEL_NAME` | `google/gemini-2.0-flash-exp:free` | Default OpenRouter model |
| `CHUNK_DURATION_MINUTES` | `10` | Max chunk duration |
| `MAX_UPLOAD_SIZE_MB` | `500` | Max upload size |

### Custom Prompts

To add custom prompts, mount a volume to `/app/prompts`:

```yaml
volumes:
  - ./my_prompts:/app/prompts:ro
```

## Architecture

```
video_analyzer_web/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application
│   ├── config.py        # Configuration management
│   ├── prompts.py       # Prompt template management
│   ├── video_processor.py # FFmpeg-based video processing
│   └── analyzers/
│       ├── __init__.py
│       ├── base.py      # Base analyzer interface
│       ├── gemini.py    # Google Gemini analyzer
│       └── openrouter.py # OpenRouter analyzer
├── static/
│   ├── css/style.css
│   └── js/app.js
├── templates/
│   └── index.html
├── prompts/             # XML prompt templates
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Getting API Keys

### Google Gemini
1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click "Create API Key"
4. Copy the key

### OpenRouter
1. Go to [OpenRouter](https://openrouter.ai/keys)
2. Create an account or sign in
3. Create a new API key
4. Copy the key

## License

MIT License
