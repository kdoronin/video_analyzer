"""
Google Gemini video analyzer using the Generative AI SDK.
Supports both direct API access and Vertex AI.
"""
import os
import asyncio
from typing import Dict, List, Optional, Any

from app.analyzers.base import (
    BaseAnalyzer,
    AnalyzerError,
    RateLimitError,
    AuthenticationError,
    VideoProcessingError
)
from app.config import config_manager


class GeminiAnalyzer(BaseAnalyzer):
    """Video analyzer using Google Gemini API."""

    # Known Gemini models that support video
    DEFAULT_MODELS = [
        {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "description": "Fast, efficient model"},
        {"id": "gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash Lite", "description": "Lightweight version"},
        {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "description": "Previous generation fast model"},
        {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "description": "Previous generation pro model"},
    ]

    def __init__(self, model_name: str = "gemini-2.0-flash", api_key: Optional[str] = None):
        super().__init__(model_name, api_key)
        self._client = None
        self._model = None

    def _get_client(self):
        """Get or create Gemini client."""
        if self._client is None:
            try:
                import google.generativeai as genai

                api_key = self.api_key or config_manager.get_api_key("gemini")
                if not api_key:
                    raise AuthenticationError("Gemini API key not configured")

                genai.configure(api_key=api_key)
                self._client = genai
                self._model = genai.GenerativeModel(self.model_name)

            except ImportError:
                raise AnalyzerError("google-generativeai package not installed")
            except Exception as e:
                raise AuthenticationError(f"Failed to initialize Gemini client: {e}")

        return self._client, self._model

    async def analyze_video(
        self,
        video_path: str,
        prompt: str,
        chunk_info: Optional[Dict] = None
    ) -> str:
        """Analyze video using Gemini API."""
        try:
            client, model = self._get_client()

            # Upload video file
            if not os.path.exists(video_path):
                raise VideoProcessingError(f"Video file not found: {video_path}")

            # Upload the video file
            video_file = client.upload_file(video_path)

            # Wait for file to be processed
            while video_file.state.name == "PROCESSING":
                await asyncio.sleep(2)
                video_file = client.get_file(video_file.name)

            if video_file.state.name == "FAILED":
                raise VideoProcessingError("Video processing failed on Gemini side")

            # Format prompt with chunk info if provided
            formatted_prompt = prompt
            if chunk_info:
                try:
                    formatted_prompt = prompt.format(
                        chunk_number=chunk_info.get("chunk_number", 1),
                        total_chunks=chunk_info.get("total_chunks", 1),
                        start_time_minutes=round(chunk_info.get("start_time", 0) / 60, 1),
                        end_time_minutes=round(chunk_info.get("end_time", 0) / 60, 1),
                        duration_minutes=round(chunk_info.get("duration", 0) / 60, 1)
                    )
                except KeyError:
                    # If prompt doesn't have placeholders, use as-is
                    pass

            # Generate content
            response = await asyncio.to_thread(
                model.generate_content,
                [video_file, formatted_prompt]
            )

            # Clean up uploaded file
            try:
                client.delete_file(video_file.name)
            except Exception:
                pass  # Ignore cleanup errors

            return response.text

        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate" in error_str or "quota" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}")
            elif "401" in error_str or "403" in error_str or "api key" in error_str:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise AnalyzerError(f"Gemini analysis failed: {e}")

    async def get_available_models(self) -> List[Dict[str, Any]]:
        """Get list of available Gemini models that support video input."""
        try:
            client, _ = self._get_client()

            models = []
            for model in client.list_models():
                # Must support generateContent method
                if "generateContent" not in model.supported_generation_methods:
                    continue

                model_id = model.name.replace("models/", "")

                # Only include gemini models
                if not model_id.startswith("gemini"):
                    continue

                # Check if model supports video input
                # Gemini models with video support have specific indicators
                supports_video = False

                # Check input token limit - models with video support typically have large context
                input_limit = getattr(model, "input_token_limit", 0)

                # Check supported modalities if available
                # Models like gemini-1.5-pro, gemini-1.5-flash, gemini-2.0-* support video
                video_capable_patterns = [
                    "gemini-1.5-pro",
                    "gemini-1.5-flash",
                    "gemini-2.0",
                    "gemini-2.5",
                    "gemini-exp",
                ]

                for pattern in video_capable_patterns:
                    if pattern in model_id:
                        supports_video = True
                        break

                # Also check description for video/multimodal mentions
                description = (model.description or "").lower()
                if "video" in description or "multimodal" in description:
                    supports_video = True

                # Large context models (1M+ tokens) typically support video
                if input_limit and input_limit >= 1000000:
                    supports_video = True

                if supports_video:
                    models.append({
                        "id": model_id,
                        "name": model.display_name,
                        "description": model.description or "",
                        "input_token_limit": input_limit,
                        "output_token_limit": getattr(model, "output_token_limit", None),
                        "supports_video": True,
                    })

            # Sort by version (newer first), then by name
            def sort_key(m):
                model_id = m["id"]
                # Extract version numbers for sorting
                if "2.5" in model_id:
                    return (0, model_id)
                elif "2.0" in model_id:
                    return (1, model_id)
                elif "1.5-pro" in model_id:
                    return (2, model_id)
                elif "1.5-flash" in model_id:
                    return (3, model_id)
                else:
                    return (9, model_id)

            models.sort(key=sort_key)

            return models if models else self.DEFAULT_MODELS

        except Exception as e:
            # Return default models on error
            return self.DEFAULT_MODELS

    def validate_api_key(self) -> bool:
        """Validate Gemini API key by checking format."""
        try:
            api_key = self.api_key or config_manager.get_api_key("gemini")
            if not api_key:
                return False
            # Gemini API keys start with "AIza" and are ~39 characters
            return api_key.startswith("AIza") and len(api_key) >= 35
        except Exception:
            return False

    async def validate_api_key_async(self) -> bool:
        """Validate Gemini API key - check format only, real validation on first use."""
        try:
            api_key = self.api_key or config_manager.get_api_key("gemini")
            if not api_key:
                return False

            # Gemini API keys start with "AIza" and are ~39 characters
            # We only check format here - real validation happens on first API call
            if api_key.startswith("AIza") and len(api_key) >= 35:
                return True

            return False
        except Exception:
            return False

    async def combine_analyses(self, analyses: List[str], combine_prompt: str) -> str:
        """Combine multiple chunk analyses into one."""
        try:
            _, model = self._get_client()

            # Join all analyses
            combined_text = "\n\n---\n\n".join([
                f"=== Chunk {i+1} Analysis ===\n{analysis}"
                for i, analysis in enumerate(analyses)
            ])

            full_prompt = f"{combine_prompt}\n\n{combined_text}"

            response = await asyncio.to_thread(
                model.generate_content,
                full_prompt
            )

            return response.text

        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}")
            raise AnalyzerError(f"Failed to combine analyses: {e}")
