# Video Analyzer Web

AI-powered video analysis with Google Gemini and OpenRouter.

The app lets you upload a video, run chunked multimodal analysis, optionally extract key frames, and generate model-specific prompt templates from a short natural-language description.

## Features

- Dual provider support: Google Gemini and OpenRouter.
- Dynamic model list per provider.
- Runtime API key setup in UI (no restart required).
- 9 built-in analysis prompt types from XML templates.
- Prompt Generation for:
  - Analysis prompt (`<prompt>` structure).
  - Keyframes criteria (`<keyframes_criteria>` structure).
- Editable keyframes criteria in UI.
- Automatic keyframes JSON format injection during analysis.
- Automatic video chunking for long inputs.
- Optional silence-aware chunk splitting around target boundaries.
- Job-based progress polling.
- Markdown analysis result output.
- Keyframe ZIP export from parsed analysis keyframes.
- Docker and manual run support.

## Quick Start

### Docker (recommended)

1. Clone and enter project:

```bash
git clone <repository-url>
cd video_analyzer
```

2. Optional env setup:

```bash
cp .env.example .env
# edit values if needed
```

3. Start app:

```bash
docker compose up -d --build
```

4. Open `http://localhost:8000`.

### Manual run

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
```

3. Optional env setup:

```bash
cp .env.example .env
```

4. Run server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Usage Flow

1. Select provider and model.
2. Configure API key for selected provider (if missing).
3. Upload video.
4. Select analysis type.
5. Optional: generate analysis prompt via Prompt Generation block.
6. Optional: enable keyframes and:
   - Edit default criteria.
   - Or generate criteria via Prompt Generation block.
7. Start analysis.
8. Review markdown results.
9. Optional: download keyframes ZIP.

## Built-in Video Types

- General Analysis
- Lecture / Educational
- Tutorial / How-to
- Marketing / Product Demo
- Presentation / Pitch
- Meeting / Standup
- Interview Evaluation
- Language Lesson
- Voiceover / Sound Design

## API Reference

### `GET /api/config`
Returns runtime status and defaults.

### `POST /api/set-api-key`
Sets provider API key at runtime.

Request:

```json
{
  "provider": "gemini",
  "api_key": "..."
}
```

### `POST /api/reset-api-key`
Resets one provider key.

Request:

```json
{
  "provider": "gemini"
}
```

### `POST /api/reset-all`
Resets all runtime API keys.

### `GET /api/video-types`
Returns available analysis types and prompt availability.

### `GET /api/prompt/{video_type}`
Returns built-in prompt template for selected type (without keyframes criteria).

### `GET /api/keyframes-criteria-default`
Returns default editable keyframes criteria XML.

### `POST /api/generate-prompt`
Generates model-aware prompt from user description.

Request:

```json
{
  "provider": "gemini",
  "model": "gemini-2.5-flash",
  "target": "analysis",
  "description": "...",
  "video_type": "marketing"
}
```

`target` can be `analysis` or `keyframes`.

Response:

```json
{
  "target": "analysis",
  "prompt": "<?xml ...>..."
}
```

Notes:
- Prompt extraction is lenient (structure-first).
- If model output is unusable, server returns a deterministic fallback template.

### `GET /api/models/{provider}`
Returns video-capable models for `gemini` or `openrouter`.

### `POST /api/upload`
Uploads video file (`multipart/form-data`, field `file`).

### `POST /api/analyze`
Starts async analysis job (`multipart/form-data`).

Fields:
- `file_id`
- `filename`
- `video_type`
- `provider`
- `model`
- `custom_prompt` (optional)
- `with_keyframes` (optional)
- `custom_keyframes_criteria` (optional)

### `GET /api/job/{job_id}`
Returns job status and result when completed.

### `POST /api/extract-keyframes`
Extracts frames to ZIP from parsed keyframes.

Request:

```json
{
  "filename": "uploaded_video.mp4",
  "keyframes": [
    {
      "timecode": "00:01:30",
      "title": "Important moment",
      "frame_description": "Optional"
    }
  ]
}
```

Response: ZIP file stream.

## Configuration

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `ANALYZER_TYPE` | `gemini` | Default provider |
| `GEMINI_API_KEY` | empty | Gemini API key |
| `GEMINI_MODEL_NAME` | `gemini-2.5-flash` | Default Gemini model |
| `OPENROUTER_API_KEY` | empty | OpenRouter API key |
| `OPENROUTER_MODEL_NAME` | `google/gemini-2.0-flash-exp:free` | Default OpenRouter model |
| `CHUNK_DURATION_MINUTES` | `10` | Max chunk duration |
| `CHUNK_SPLIT_MODE` | `fixed` | `fixed` or `silence_aware` |
| `SILENCE_WINDOW_SECONDS` | `120` | Search window around target split point (seconds) |
| `SILENCE_MIN_DURATION_SECONDS` | `3.0` | Minimum silence duration for split candidate |
| `SILENCE_NOISE_DB` | `-35.0` | Silence threshold for `silencedetect` |
| `MAX_UPLOAD_SIZE_MB` | `500` | Max upload size |
| `UPLOAD_DIRECTORY` | `uploads` | Upload path |
| `OUTPUT_DIRECTORY` | `outputs` | Output path |
| `TEMP_DIRECTORY` | `temporary` | Temp path |
| `PROMPTS_DIRECTORY` | `prompts` | Prompt templates path |
| `GOOGLE_CLOUD_PROJECT_ID` | empty | Reserved config |
| `VERTEX_AI_LOCATION` | `global` | Reserved config |

Runtime API keys set in UI are stored in memory and are reset when the app restarts.

Chunk split behavior:
- `fixed`: strict `CHUNK_DURATION_MINUTES` boundaries.
- `silence_aware`: each target boundary is moved to the nearest detected silence within `±SILENCE_WINDOW_SECONDS`; if no suitable silence is found, the target boundary is kept.

## Project Structure

```text
video_analyzer/
├── app/
│   ├── main.py                # FastAPI app and endpoints
│   ├── config.py              # Runtime/env config manager
│   ├── prompts.py             # Built-in prompt loading/composition
│   ├── prompt_generation.py   # Prompt generation/extraction/fallback logic
│   ├── video_processor.py     # Chunking and keyframe extraction (FFmpeg)
│   └── analyzers/
│       ├── base.py
│       ├── gemini.py
│       └── openrouter.py
├── templates/
│   └── index.html             # UI markup
├── static/
│   ├── css/style.css
│   └── js/app.js              # UI logic and API calls
├── prompts/                   # XML prompt templates
├── uploads/
├── outputs/
├── temporary/
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## API Key Links

- Gemini: https://aistudio.google.com/apikey
- OpenRouter: https://openrouter.ai/keys

## License

MIT
