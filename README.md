# Video Analyzer Web

AI-powered video analysis with Google Gemini and OpenRouter.

The app lets you upload a video, run chunked multimodal analysis, optionally extract key frames, optionally cut reusable video clips, and generate model-specific XML prompt templates from a short natural-language description.

## Features

- Dual provider support: Google Gemini and OpenRouter.
- Dynamic model list per provider.
- Runtime API key setup in UI (no restart required).
- 9 built-in analysis prompt types from XML templates.
- Prompt Generation for:
  - Analysis prompt (`<prompt>` structure).
  - Keyframes criteria (`<keyframes_criteria>` structure).
  - Clip extraction criteria (`<clip_segments_criteria>` structure).
- Editable keyframes criteria in UI.
- Editable clip extraction criteria in UI.
- Automatic keyframes JSON format injection during analysis.
- Automatic clip segments JSON format injection during analysis.
- Automatic video chunking for long inputs.
- Optional silence-aware chunk splitting around target boundaries.
- Chunk-relative keyframes and clip segments are normalized to absolute source-video timecodes.
- Streamed upload to disk for large files.
- Upload progress UI and immediate analysis-start feedback in UI.
- Job-based progress polling.
- Markdown analysis result output.
- Keyframe ZIP export from parsed analysis keyframes.
- Clip ZIP export from model-returned clip segments.
- Docker and manual run support.

## Quick Start

### Docker (recommended)

1. Clone and enter project:

```bash
git clone <repository-url>
cd video_analyzer
```

2. Recommended env setup:

```bash
cp .env.example .env
# edit values if needed
```

If you skip `.env`, Docker Compose will still start the app, but its own fallback values apply.

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
7. Optional: enable clip extraction and:
   - Edit default clip criteria.
   - Or generate clip criteria via Prompt Generation block.
8. Start analysis.
9. Review markdown results.
10. Optional: download keyframes ZIP.
11. Optional: download clips ZIP generated from returned clip segments.

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

### `GET /api/clips-criteria-default`
Returns default editable clip extraction criteria XML.

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

`target` can be `analysis`, `keyframes`, or `clips`.

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

Notes:
- Upload is streamed to disk.
- UI shows upload progress and a server-processing phase after the browser reaches `100%`.
- `MAX_UPLOAD_SIZE_MB=0` disables the application-level size limit.

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
- `with_clips` (optional)
- `custom_clips_criteria` (optional)

### `GET /api/job/{job_id}`
Returns job status and result when completed.

Returned payload may also include `artifacts.clips` with parsed clip segments, archive status, and download URL.

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

### `GET /api/job/{job_id}/download-clips`
Downloads a prebuilt ZIP archive with extracted video clips for a completed job.

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
| `MAX_UPLOAD_SIZE_MB` | `0` | Max upload size in MB, `0` disables the limit |
| `UPLOAD_DIRECTORY` | `uploads` | Upload path |
| `OUTPUT_DIRECTORY` | `outputs` | Output path |
| `TEMP_DIRECTORY` | `temporary` | Temp path |
| `PROMPTS_DIRECTORY` | `prompts` | Prompt templates path |
| `GOOGLE_CLOUD_PROJECT_ID` | empty | Reserved config |
| `VERTEX_AI_LOCATION` | `global` | Reserved config |

Runtime API keys set in UI are stored in memory and are reset when the app restarts.

Notes:
- In the application config, `MAX_UPLOAD_SIZE_MB=0` means no application-level upload cap.
- If you run through Docker Compose without a `.env`, Compose may still inject its own fallback values from `docker-compose.yml`.

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
│   ├── structured_outputs.py  # Keyframes/clips JSON parsing and timecode normalization
│   ├── video_processor.py     # Chunking, keyframe extraction, clip cutting (FFmpeg)
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
