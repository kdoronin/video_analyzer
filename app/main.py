"""
FastAPI main application for Video Analyzer Web.
"""
import os
import uuid
import json
import asyncio
from datetime import datetime
from typing import Optional, Dict, List
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.config import config_manager
from app.prompts import prompt_manager, VIDEO_TYPES
from app.video_processor import VideoProcessor, seconds_to_timecode
from app.analyzers import GeminiAnalyzer, OpenRouterAnalyzer, AnalyzerError, AuthenticationError


# Initialize FastAPI app
app = FastAPI(
    title="Video Analyzer",
    description="AI-powered video analysis with Gemini and OpenRouter",
    version="1.0.0"
)

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


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending, processing, completed, failed
    progress: int
    current_step: str
    result: Optional[str] = None
    error: Optional[str] = None


# ============== Job Storage ==============

# In-memory job storage (in production, use Redis or database)
jobs: Dict[str, Dict] = {}


# ============== API Endpoints ==============

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render main page."""
    return templates.TemplateResponse("index.html", {"request": request})


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
async def get_prompt(video_type: str, with_keyframes: bool = False):
    """Get prompt template for a video type."""
    try:
        prompt = prompt_manager.load_prompt(video_type, with_keyframes)
        return {
            "video_type": video_type,
            "prompt": prompt,
            "type_info": VIDEO_TYPES.get(video_type, {})
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


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
        content = await file.read()
        max_size = config_manager.settings.max_upload_size_mb * 1024 * 1024

        if len(content) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size: {config_manager.settings.max_upload_size_mb}MB"
            )

        with open(file_path, "wb") as f:
            f.write(content)

        # Get video info
        processor = VideoProcessor()
        video_info = processor.get_video_info(str(file_path))

        return {
            "file_id": file_id,
            "filename": safe_filename,
            "original_name": file.filename,
            "size_bytes": len(content),
            "video_info": video_info
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")


@app.post("/api/analyze")
async def start_analysis(
    background_tasks: BackgroundTasks,
    file_id: str = Form(...),
    filename: str = Form(...),
    video_type: str = Form(...),
    provider: str = Form(...),
    model: str = Form(...),
    custom_prompt: Optional[str] = Form(None),
    with_keyframes: bool = Form(False)
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
        with_keyframes
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
        error=job.get("error")
    )


# ============== Background Processing ==============

async def process_video_job(
    job_id: str,
    file_path: str,
    video_type: str,
    provider: str,
    model: str,
    custom_prompt: Optional[str],
    with_keyframes: bool
):
    """Process video analysis in background."""
    try:
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["current_step"] = "Loading prompt template..."
        jobs[job_id]["progress"] = 5

        # Load prompt
        if custom_prompt and custom_prompt.strip():
            prompt = custom_prompt
        else:
            prompt = prompt_manager.load_prompt(video_type, with_keyframes)

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
            except Exception as e:
                jobs[job_id]["error"] = f"Failed on chunk {i+1}: {str(e)}"
                jobs[job_id]["status"] = "failed"
                return

        # Combine analyses if multiple chunks
        jobs[job_id]["progress"] = 85
        jobs[job_id]["current_step"] = "Combining analyses..."

        if len(analyses) > 1:
            combine_prompt = prompt_manager.load_combine_prompt()
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
