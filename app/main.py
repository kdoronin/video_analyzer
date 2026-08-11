"""
FastAPI main application for Video Analyzer Web.
"""
import os
import uuid
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, List
from pathlib import Path

import aiofiles
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field

from app.config import config_manager
from app.prompts import prompt_manager, VIDEO_TYPES
from app.prompt_generation import prompt_generation_service, PromptGenerationError
from app.structured_outputs import structured_output_parser
from app.video_processor import VideoProcessor, seconds_to_timecode
from app.analyzers import GeminiAnalyzer, OpenRouterAnalyzer, AnalyzerError, AuthenticationError

logger = logging.getLogger(__name__)


# Initialize FastAPI app
app = FastAPI(
    title="Video Analyzer",
    description="AI-powered video analysis with Gemini and OpenRouter",
    version="1.0.0"
)


@app.middleware("http")
async def prevent_app_js_cache(request: Request, call_next):
    """Always serve the current UI application code after a page reload."""
    response = await call_next(request)
    if request.url.path == "/static/js/app.js":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# Setup static files and templates
BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Ensure directories exist
for dir_name in ["uploads", "outputs", "temporary"]:
    os.makedirs(BASE_DIR / dir_name, exist_ok=True)


# ============== Models ==============

class APIKeyRequest(BaseModel):
    provider: str
    api_key: str


class ResetKeyRequest(BaseModel):
    provider: str


class AnalysisRequest(BaseModel):
    video_type: str
    provider: str
    model: str
    custom_prompt: Optional[str] = None
    with_keyframes: bool = False


class KeyframeItem(BaseModel):
    timecode: str
    title: str
    frame_description: Optional[str] = None


class ExtractKeyframesRequest(BaseModel):
    filename: str
    keyframes: List[KeyframeItem]


class PromptGenerationRequest(BaseModel):
    provider: str
    model: str
    target: str  # "analysis", "keyframes", or "clips"
    description: str
    video_type: Optional[str] = None


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending, processing, completed, failed
    progress: int
    current_step: str
    result: Optional[str] = None
    error: Optional[str] = None
    artifacts: Optional[Dict] = None
    warnings: List[str] = Field(default_factory=list)


# ============== Job Storage ==============

# In-memory job storage (in production, use Redis or database)
jobs: Dict[str, Dict] = {}


def build_public_artifacts(job_id: str, job: Dict) -> Dict:
    """Return client-safe artifact metadata for a job."""
    artifacts = job.get("artifacts") or {}
    public_artifacts: Dict[str, Dict] = {}

    clips = artifacts.get("clips")
    if clips:
        public_artifacts["clips"] = {
            "requested": bool(clips.get("requested")),
            "count": int(clips.get("count", 0) or 0),
            "segments": clips.get("segments") or [],
            "archive_ready": bool(clips.get("archive_ready")),
            "filename": clips.get("filename"),
            "download_url": f"/api/job/{job_id}/download-clips" if clips.get("archive_ready") else None,
            "error": clips.get("error"),
        }

    return public_artifacts


def append_prompt_section(prompt: str, section: Optional[str]) -> str:
    """Append a prompt section once, preserving existing custom prompt edits."""
    section_text = (section or "").strip()
    if not section_text:
        return prompt

    base_prompt = (prompt or "").strip()
    if section_text in base_prompt:
        return base_prompt

    if not base_prompt:
        return section_text

    return f"{base_prompt}\n\n{section_text}"


# ============== API Endpoints ==============

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render main page."""
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.get("/api/config")
async def get_config():
    """Get current configuration status."""
    settings = config_manager.settings
    return {
        "gemini_configured": config_manager.has_valid_api_key("gemini"),
        "openrouter_configured": config_manager.has_valid_api_key("openrouter"),
        "default_provider": settings.analyzer_type,
        "default_gemini_model": settings.gemini_model_name,
        "default_openrouter_model": settings.openrouter_model_name,
        "max_upload_size_mb": settings.max_upload_size_mb,
        "chunk_duration_minutes": settings.chunk_duration_minutes,
        "chunk_split_mode": settings.chunk_split_mode,
        "silence_window_seconds": settings.silence_window_seconds,
        "silence_min_duration_seconds": settings.silence_min_duration_seconds,
        "silence_noise_db": settings.silence_noise_db,
    }


@app.post("/api/set-api-key")
async def set_api_key(request: APIKeyRequest):
    """Set API key for a provider at runtime."""
    if request.provider not in ["gemini", "openrouter"]:
        raise HTTPException(status_code=400, detail="Invalid provider")

    config_manager.set_runtime_api_key(request.provider, request.api_key)

    # Validate the key
    try:
        if request.provider == "gemini":
            analyzer = GeminiAnalyzer(api_key=request.api_key)
            is_valid = await analyzer.validate_api_key_async()
        else:
            analyzer = OpenRouterAnalyzer(api_key=request.api_key)
            is_valid = await analyzer.validate_api_key_async()

        if not is_valid:
            # Clear the invalid key
            config_manager.set_runtime_api_key(request.provider, "")
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "API key validation failed"}
            )

        return {"success": True, "message": f"{request.provider} API key configured"}

    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(e)}
        )


@app.post("/api/reset-api-key")
async def reset_api_key(request: ResetKeyRequest):
    """Reset API key for a provider."""
    if request.provider not in ["gemini", "openrouter"]:
        raise HTTPException(status_code=400, detail="Invalid provider")

    config_manager.set_runtime_api_key(request.provider, "")
    return {"success": True, "message": f"{request.provider} API key has been reset"}


@app.post("/api/reset-all")
async def reset_all_config():
    """Reset all runtime configuration."""
    config_manager.set_runtime_api_key("gemini", "")
    config_manager.set_runtime_api_key("openrouter", "")
    return {"success": True, "message": "All API keys have been reset"}


@app.get("/api/video-types")
async def get_video_types():
    """Get available video types for analysis."""
    return prompt_manager.get_available_types()


@app.get("/api/prompt/{video_type}")
async def get_prompt(video_type: str):
    """Get prompt template for a video type (without keyframes - they are shown separately)."""
    try:
        # Always load without keyframes for UI display
        # Keyframes criteria are shown in a separate textarea
        prompt = prompt_manager.load_prompt(video_type, with_keyframes=False)
        return {
            "video_type": video_type,
            "prompt": prompt,
            "type_info": VIDEO_TYPES.get(video_type, {})
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/keyframes-criteria-default")
async def get_keyframes_criteria_default():
    """Get default keyframes criteria description (editable by user)."""
    criteria = prompt_manager.get_keyframes_criteria_default()
    return {"criteria": criteria}


@app.get("/api/clips-criteria-default")
async def get_clips_criteria_default():
    """Get default clip extraction criteria description (editable by user)."""
    criteria = prompt_manager.get_clips_criteria_default()
    return {"criteria": criteria}


@app.post("/api/generate-prompt")
async def generate_prompt(request: PromptGenerationRequest):
    """Generate model-aware XML prompt for analysis or keyframes criteria."""
    if request.provider not in ["gemini", "openrouter"]:
        raise HTTPException(status_code=400, detail="Invalid provider")

    if request.target not in ["analysis", "keyframes", "clips"]:
        raise HTTPException(status_code=400, detail="Invalid target")

    if not request.description or not request.description.strip():
        raise HTTPException(status_code=400, detail="Description is required")

    if not request.model or not request.model.strip():
        raise HTTPException(status_code=400, detail="Model is required")

    if not config_manager.has_valid_api_key(request.provider):
        raise HTTPException(
            status_code=400,
            detail=f"API key for {request.provider} not configured. Please set it first."
        )

    analyzer = None
    try:
        api_key = config_manager.get_api_key(request.provider)
        if request.provider == "gemini":
            analyzer = GeminiAnalyzer(model_name=request.model, api_key=api_key)
        else:
            analyzer = OpenRouterAnalyzer(model_name=request.model, api_key=api_key)

        generation_instruction = prompt_generation_service.build_generation_instruction(
            target=request.target,
            description=request.description,
            provider=request.provider,
            model=request.model,
            video_type=request.video_type
        )

        generated_raw = await analyzer.generate_text(generation_instruction)
        try:
            generated_xml = prompt_generation_service.extract_xml(
                generated_raw,
                request.target,
                strict=False
            )
        except PromptGenerationError as parse_error:
            # Local fallback only: avoid extra model call to keep latency low.
            logger.warning(
                "Prompt generation parse failed | provider=%s model=%s target=%s error=%s raw_head=%s",
                request.provider,
                request.model,
                request.target,
                str(parse_error),
                str(generated_raw).replace("\n", "\\n")[:800]
            )
            generated_xml = prompt_generation_service.build_deterministic_fallback(
                target=request.target,
                description=request.description,
                provider=request.provider,
                model=request.model,
                video_type=request.video_type
            )
            logger.warning(
                "Prompt generation fallback template used | provider=%s model=%s target=%s",
                request.provider,
                request.model,
                request.target
            )

        return {
            "target": request.target,
            "prompt": generated_xml
        }
    except PromptGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AnalyzerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate prompt: {e}")
    finally:
        if analyzer and hasattr(analyzer, "close"):
            await analyzer.close()


@app.get("/api/models/{provider}")
async def get_models(provider: str):
    """Get available models for a provider."""
    try:
        if provider == "gemini":
            api_key = config_manager.get_api_key("gemini")
            if not api_key:
                # Return default models if no API key
                return {"models": GeminiAnalyzer.DEFAULT_MODELS, "from_api": False}
            analyzer = GeminiAnalyzer(api_key=api_key)
        elif provider == "openrouter":
            api_key = config_manager.get_api_key("openrouter")
            if not api_key:
                return {"models": OpenRouterAnalyzer.DEFAULT_MODELS, "from_api": False}
            analyzer = OpenRouterAnalyzer(api_key=api_key)
        else:
            raise HTTPException(status_code=400, detail="Invalid provider")

        models = await analyzer.get_available_models()
        return {"models": models, "from_api": True}

    except Exception as e:
        # Return default models on error
        if provider == "gemini":
            return {"models": GeminiAnalyzer.DEFAULT_MODELS, "from_api": False, "error": str(e)}
        else:
            return {"models": OpenRouterAnalyzer.DEFAULT_MODELS, "from_api": False, "error": str(e)}


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """Upload a video file."""
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    allowed_extensions = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".wmv", ".flv"}
    ext = Path(file.filename).suffix.lower()

    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type not supported. Allowed: {', '.join(allowed_extensions)}"
        )

    # Generate unique filename
    file_id = str(uuid.uuid4())[:8]
    safe_filename = f"{file_id}_{Path(file.filename).stem}{ext}"
    file_path = BASE_DIR / "uploads" / safe_filename

    # Save file
    try:
        max_upload_size_mb = int(config_manager.settings.max_upload_size_mb or 0)
        max_size = max_upload_size_mb * 1024 * 1024 if max_upload_size_mb > 0 else None
        total_size = 0

        async with aiofiles.open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break

                total_size += len(chunk)
                if max_size is not None and total_size > max_size:
                    await f.close()
                    if file_path.exists():
                        file_path.unlink()
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum size: {max_upload_size_mb}MB"
                    )

                await f.write(chunk)

        # Get video info
        processor = VideoProcessor()
        video_info = processor.get_video_info(str(file_path))

        return {
            "file_id": file_id,
            "filename": safe_filename,
            "original_name": file.filename,
            "size_bytes": total_size,
            "video_info": video_info
        }

    except HTTPException:
        raise
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")
    finally:
        await file.close()


@app.post("/api/analyze")
async def start_analysis(
    background_tasks: BackgroundTasks,
    file_id: str = Form(...),
    filename: str = Form(...),
    video_type: str = Form(...),
    provider: str = Form(...),
    model: str = Form(...),
    custom_prompt: Optional[str] = Form(None),
    with_keyframes: bool = Form(False),
    custom_keyframes_criteria: Optional[str] = Form(None),
    with_clips: bool = Form(False),
    custom_clips_criteria: Optional[str] = Form(None)
):
    """Start video analysis job."""
    # Validate inputs
    file_path = BASE_DIR / "uploads" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")

    if not config_manager.has_valid_api_key(provider):
        raise HTTPException(
            status_code=400,
            detail=f"API key for {provider} not configured. Please set it first."
        )

    # Create job
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "pending",
        "progress": 0,
        "current_step": "Initializing...",
        "result": None,
        "error": None,
        "created_at": datetime.now().isoformat(),
        "file_path": str(file_path),
        "video_type": video_type,
        "provider": provider,
        "model": model,
        "warnings": [],
        "artifacts": {
            "clips": {
                "requested": with_clips,
                "count": 0,
                "segments": [],
                "archive_ready": False,
                "filename": None,
                "archive_path": None,
                "error": None,
            }
        } if with_clips else {},
    }

    # Start background processing
    background_tasks.add_task(
        process_video_job,
        job_id,
        str(file_path),
        video_type,
        provider,
        model,
        custom_prompt,
        with_keyframes,
        custom_keyframes_criteria,
        with_clips,
        custom_clips_criteria
    )

    return {"job_id": job_id, "status": "pending"}


@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
    """Get status of an analysis job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return JobStatus(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        current_step=job["current_step"],
        result=job.get("result"),
        error=job.get("error"),
        artifacts=build_public_artifacts(job_id, job),
        warnings=job.get("warnings", []),
    )


@app.get("/api/job/{job_id}/download-clips")
async def download_clips_archive(job_id: str):
    """Download prebuilt clips archive for a completed job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    clips = (job.get("artifacts") or {}).get("clips") or {}
    archive_path = clips.get("archive_path")

    if not clips.get("archive_ready") or not archive_path:
        raise HTTPException(status_code=404, detail="Clip archive is not available for this job")

    if not os.path.exists(archive_path):
        raise HTTPException(status_code=404, detail="Clip archive file is missing")

    return FileResponse(
        path=archive_path,
        filename=clips.get("filename") or f"{job_id}_clips.zip",
        media_type="application/zip",
    )


@app.post("/api/extract-keyframes")
async def extract_keyframes(request: ExtractKeyframesRequest):
    """Extract keyframes from video and return ZIP archive."""
    # Validate video file exists
    file_path = BASE_DIR / "uploads" / request.filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")

    if not request.keyframes:
        raise HTTPException(status_code=400, detail="No keyframes provided")

    # Create unique job id for this extraction
    extraction_id = str(uuid.uuid4())[:8]

    # Prepare output path
    video_name = Path(request.filename).stem
    output_dir = BASE_DIR / "outputs" / video_name
    os.makedirs(output_dir, exist_ok=True)

    zip_filename = f"{video_name}_keyframes_{extraction_id}.zip"
    zip_path = output_dir / zip_filename

    try:
        processor = VideoProcessor(temp_dir=str(BASE_DIR / "temporary"))

        # Convert pydantic models to dicts
        keyframes_list = [kf.model_dump() for kf in request.keyframes]

        result = processor.extract_keyframes_to_zip(
            video_path=str(file_path),
            keyframes=keyframes_list,
            output_zip_path=str(zip_path),
            job_id=extraction_id
        )

        if not result["success"] or result["extracted_count"] == 0:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to extract keyframes. Extracted: {result['extracted_count']}, Failed: {result['failed_count']}"
            )

        return FileResponse(
            path=str(zip_path),
            filename=zip_filename,
            media_type="application/zip"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract keyframes: {str(e)}")


# ============== Background Processing ==============

async def process_video_job(
    job_id: str,
    file_path: str,
    video_type: str,
    provider: str,
    model: str,
    custom_prompt: Optional[str],
    with_keyframes: bool,
    custom_keyframes_criteria: Optional[str] = None,
    with_clips: bool = False,
    custom_clips_criteria: Optional[str] = None
):
    """Process video analysis in background."""
    try:
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["current_step"] = "Loading prompt template..."
        jobs[job_id]["progress"] = 5

        # Load prompt
        if custom_prompt and custom_prompt.strip():
            prompt = custom_prompt.strip()
        else:
            prompt = prompt_manager.load_prompt(
                video_type,
                with_keyframes=False,
                with_clips=False,
            )

        # Criteria blocks are appended separately so they are preserved even with custom prompt edits.
        if with_keyframes:
            keyframes_criteria = (
                custom_keyframes_criteria.strip()
                if custom_keyframes_criteria and custom_keyframes_criteria.strip()
                else prompt_manager.get_keyframes_criteria_default()
            )
            prompt = append_prompt_section(prompt, keyframes_criteria)

            keyframes_format = prompt_manager.get_keyframes_format()
            prompt = append_prompt_section(prompt, keyframes_format)

        if with_clips:
            clips_criteria = (
                custom_clips_criteria.strip()
                if custom_clips_criteria and custom_clips_criteria.strip()
                else prompt_manager.get_clips_criteria_default()
            )
            prompt = append_prompt_section(prompt, clips_criteria)

            clips_format = prompt_manager.get_clips_format()
            prompt = append_prompt_section(prompt, clips_format)

        jobs[job_id]["current_step"] = "Analyzing video duration..."
        jobs[job_id]["progress"] = 10

        # Initialize processor and analyzer
        processor = VideoProcessor(temp_dir=str(BASE_DIR / "temporary"))

        api_key = config_manager.get_api_key(provider)
        if provider == "gemini":
            analyzer = GeminiAnalyzer(model_name=model, api_key=api_key)
        else:
            analyzer = OpenRouterAnalyzer(model_name=model, api_key=api_key)

        # Split video into chunks
        jobs[job_id]["current_step"] = "Splitting video into chunks..."
        jobs[job_id]["progress"] = 15

        chunks = processor.split_video(file_path, job_id)
        total_chunks = len(chunks)

        jobs[job_id]["current_step"] = f"Processing {total_chunks} chunk(s)..."
        jobs[job_id]["progress"] = 20

        # Analyze each chunk
        analyses = []
        all_clip_segments: List[Dict] = []
        for i, chunk in enumerate(chunks):
            chunk_progress = 20 + int((i / total_chunks) * 60)
            jobs[job_id]["progress"] = chunk_progress
            jobs[job_id]["current_step"] = f"Analyzing chunk {i+1}/{total_chunks}..."

            try:
                analysis = await analyzer.analyze_with_retry(
                    chunk["path"],
                    prompt,
                    chunk
                )
                analyses.append(analysis)

                if with_clips:
                    parsed_segments = structured_output_parser.parse_clip_segments(analysis, chunk)
                    if parsed_segments:
                        all_clip_segments.extend(parsed_segments)
            except Exception as e:
                jobs[job_id]["error"] = f"Failed on chunk {i+1}: {str(e)}"
                jobs[job_id]["status"] = "failed"
                return

        # Combine analyses if multiple chunks
        jobs[job_id]["progress"] = 85
        jobs[job_id]["current_step"] = "Combining analyses..."

        if len(analyses) > 1:
            combine_prompt = prompt_manager.load_combine_prompt(video_type)
            final_analysis = await analyzer.combine_analyses(analyses, combine_prompt)
        else:
            final_analysis = analyses[0]

        # Save result
        jobs[job_id]["progress"] = 95
        jobs[job_id]["current_step"] = "Saving results..."

        video_name = Path(file_path).stem
        output_dir = BASE_DIR / "outputs" / video_name
        os.makedirs(output_dir, exist_ok=True)

        output_file = output_dir / f"{video_name}_analysis.md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# Video Analysis: {video_name}\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"**Provider:** {provider}\n")
            f.write(f"**Model:** {model}\n")
            f.write(f"**Video Type:** {VIDEO_TYPES.get(video_type, {}).get('name', video_type)}\n\n")
            f.write("---\n\n")
            f.write(final_analysis)

        if with_clips:
            jobs[job_id]["progress"] = 98
            jobs[job_id]["current_step"] = "Preparing clip archive..."
            clip_artifact = (jobs[job_id].get("artifacts") or {}).setdefault("clips", {})
            unique_segments = sorted(
                structured_output_parser.parse_clip_segments(
                    json.dumps({"clip_segments": all_clip_segments}, ensure_ascii=False)
                ),
                key=lambda item: item.get("start_timecode", ""),
            )
            clip_artifact["segments"] = unique_segments
            clip_artifact["count"] = len(unique_segments)

            if unique_segments:
                clip_suffix = job_id[:8]
                clip_zip_filename = f"{video_name}_clips_{clip_suffix}.zip"
                clip_zip_path = output_dir / clip_zip_filename
                try:
                    clip_result = processor.extract_clips_to_zip(
                        video_path=file_path,
                        clip_segments=unique_segments,
                        output_zip_path=str(clip_zip_path),
                        job_id=job_id,
                    )
                except Exception as exc:
                    clip_result = None
                    warning = f"Не удалось собрать архив клипов: {str(exc)}"
                    jobs[job_id]["warnings"].append(warning)
                    clip_artifact["error"] = warning
                else:
                    if clip_result["success"] and clip_result["extracted_count"] > 0:
                        clip_artifact["archive_ready"] = True
                        clip_artifact["filename"] = clip_zip_filename
                        clip_artifact["archive_path"] = str(clip_zip_path)
                        clip_artifact["count"] = clip_result["extracted_count"]
                        clip_artifact["segments"] = clip_result["extracted"]
                        if clip_result["failed_count"] > 0:
                            warning = (
                                f"Часть клипов не удалось нарезать: {clip_result['failed_count']} из "
                                f"{clip_result['failed_count'] + clip_result['extracted_count']}."
                            )
                            jobs[job_id]["warnings"].append(warning)
                            clip_artifact["error"] = warning
                    else:
                        warning = "Модель вернула сегменты, но архив клипов собрать не удалось."
                        jobs[job_id]["warnings"].append(warning)
                        clip_artifact["error"] = warning
            else:
                warning = "Модель не вернула ни одного сегмента для нарезки."
                jobs[job_id]["warnings"].append(warning)
                clip_artifact["error"] = warning

        # Cleanup temp files
        processor.cleanup_job(job_id)

        # Complete
        jobs[job_id]["progress"] = 100
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["current_step"] = "Analysis complete!"
        jobs[job_id]["result"] = final_analysis

    except AuthenticationError as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = f"Authentication error: {str(e)}"
    except AnalyzerError as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = f"Analysis error: {str(e)}"
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = f"Unexpected error: {str(e)}"


# ============== Startup/Shutdown ==============

@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    # Ensure prompt files exist
    prompts_dir = BASE_DIR / "prompts"
    if not prompts_dir.exists() or not any(prompts_dir.iterdir()):
        print("Warning: Prompts directory is empty. Please copy prompt files.")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    # Clean up any remaining temp files
    import shutil
    temp_dir = BASE_DIR / "temporary"
    if temp_dir.exists():
        for item in temp_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
