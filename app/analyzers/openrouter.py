"""
OpenRouter video analyzer using OpenAI-compatible API.
Supports multiple AI models via OpenRouter.
"""
import os
import base64
import asyncio
from typing import Dict, List, Optional, Any
import httpx

from app.analyzers.base import (
    BaseAnalyzer,
    AnalyzerError,
    RateLimitError,
    AuthenticationError,
    VideoProcessingError
)
from app.config import config_manager


class OpenRouterAnalyzer(BaseAnalyzer):
    """Video analyzer using OpenRouter API."""

    BASE_URL = "https://openrouter.ai/api/v1"

    # Default models known to support video
    DEFAULT_MODELS = [
        {"id": "google/gemini-2.0-flash-exp:free", "name": "Gemini 2.0 Flash (Free)", "description": "Free tier Gemini 2.0"},
        {"id": "google/gemini-flash-1.5", "name": "Gemini 1.5 Flash", "description": "Fast Gemini model"},
        {"id": "google/gemini-pro-1.5", "name": "Gemini 1.5 Pro", "description": "Advanced Gemini model"},
        {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet", "description": "Anthropic's Claude model"},
    ]

    def __init__(self, model_name: str = "google/gemini-2.0-flash-exp:free", api_key: Optional[str] = None):
        super().__init__(model_name, api_key)
        self._http_client = None

    def _get_api_key(self) -> str:
        """Get OpenRouter API key."""
        key = self.api_key or config_manager.get_api_key("openrouter")
        if not key:
            raise AuthenticationError("OpenRouter API key not configured")
        return key

    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=30.0,
                    read=1800.0,  # 30 minutes for long video processing
                    write=300.0,
                    pool=30.0
                )
            )
        return self._http_client

    def _encode_video_to_base64(self, video_path: str) -> str:
        """Encode video file to base64 string."""
        if not os.path.exists(video_path):
            raise VideoProcessingError(f"Video file not found: {video_path}")

        # Check file size (base64 encoding has ~33% overhead)
        file_size = os.path.getsize(video_path)
        max_size = 20 * 1024 * 1024  # 20MB limit for base64

        if file_size > max_size:
            raise VideoProcessingError(
                f"Video file too large for OpenRouter ({file_size / 1024 / 1024:.1f}MB). "
                f"Maximum size is {max_size / 1024 / 1024:.0f}MB."
            )

        with open(video_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _get_mime_type(self, video_path: str) -> str:
        """Get MIME type based on file extension."""
        ext = os.path.splitext(video_path)[1].lower()
        mime_types = {
            ".mp4": "video/mp4",
            ".avi": "video/x-msvideo",
            ".mov": "video/quicktime",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
            ".m4v": "video/x-m4v",
        }
        return mime_types.get(ext, "video/mp4")

    async def analyze_video(
        self,
        video_path: str,
        prompt: str,
        chunk_info: Optional[Dict] = None
    ) -> str:
        """Analyze video using OpenRouter API."""
        try:
            api_key = self._get_api_key()
            client = self._get_http_client()

            # Encode video to base64
            video_base64 = self._encode_video_to_base64(video_path)
            mime_type = self._get_mime_type(video_path)

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
                    pass

            # Build request
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://video-analyzer.local",
                "X-Title": "Video Analyzer"
            }

            payload = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "video_url",
                                "video_url": {
                                    "url": f"data:{mime_type};base64,{video_base64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": formatted_prompt
                            }
                        ]
                    }
                ],
                "max_tokens": 16000
            }

            response = await client.post(
                f"{self.BASE_URL}/chat/completions",
                headers=headers,
                json=payload
            )

            if response.status_code == 429:
                raise RateLimitError("Rate limit exceeded")
            elif response.status_code == 401 or response.status_code == 403:
                raise AuthenticationError("Invalid API key")
            elif response.status_code != 200:
                raise AnalyzerError(f"API error: {response.status_code} - {response.text}")

            data = response.json()

            if "error" in data:
                raise AnalyzerError(f"API error: {data['error']}")

            return data["choices"][0]["message"]["content"]

        except (RateLimitError, AuthenticationError):
            raise
        except httpx.TimeoutException:
            raise AnalyzerError("Request timed out")
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}")
            elif "401" in error_str or "403" in error_str:
                raise AuthenticationError(f"Authentication failed: {e}")
            raise AnalyzerError(f"OpenRouter analysis failed: {e}")

    async def generate_text(self, prompt: str) -> str:
        """Generate text-only output using OpenRouter chat completions."""
        try:
            api_key = self._get_api_key()
            client = self._get_http_client()

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://video-analyzer.local",
                "X-Title": "Video Analyzer"
            }

            payload = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "max_tokens": 16000
            }

            response = await client.post(
                f"{self.BASE_URL}/chat/completions",
                headers=headers,
                json=payload
            )

            if response.status_code == 429:
                raise RateLimitError("Rate limit exceeded")
            elif response.status_code == 401 or response.status_code == 403:
                raise AuthenticationError("Invalid API key")
            elif response.status_code != 200:
                raise AnalyzerError(f"API error: {response.status_code} - {response.text}")

            data = response.json()

            if "error" in data:
                raise AnalyzerError(f"API error: {data['error']}")

            return data["choices"][0]["message"]["content"]

        except (RateLimitError, AuthenticationError):
            raise
        except httpx.TimeoutException:
            raise AnalyzerError("Request timed out")
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}")
            elif "401" in error_str or "403" in error_str:
                raise AuthenticationError(f"Authentication failed: {e}")
            raise AnalyzerError(f"OpenRouter text generation failed: {e}")

    async def get_available_models(self) -> List[Dict[str, Any]]:
        """Get list of available models from OpenRouter that support video input."""
        try:
            api_key = self._get_api_key()
            client = self._get_http_client()

            headers = {
                "Authorization": f"Bearer {api_key}",
            }

            response = await client.get(
                f"{self.BASE_URL}/models",
                headers=headers
            )

            if response.status_code != 200:
                return self.DEFAULT_MODELS

            data = response.json()
            models = []

            for model in data.get("data", []):
                model_id = model.get("id", "")
                name = model.get("name", model_id)

                # Check architecture for video support
                architecture = model.get("architecture", {})
                input_modalities = architecture.get("input_modalities", [])
                modality = architecture.get("modality", "")

                # Primary check: explicit video in input_modalities
                supports_video = "video" in input_modalities

                # Secondary check: modality field contains video indicators
                if not supports_video and modality:
                    modality_lower = modality.lower()
                    if "video" in modality_lower:
                        supports_video = True

                # Tertiary check: known video-capable model patterns
                # Only if no modality info available
                if not supports_video and not input_modalities and not modality:
                    video_capable_ids = [
                        "google/gemini-2.0",
                        "google/gemini-2.5",
                        "google/gemini-1.5-pro",
                        "google/gemini-1.5-flash",
                        "google/gemini-exp",
                    ]
                    for pattern in video_capable_ids:
                        if model_id.startswith(pattern):
                            supports_video = True
                            break

                if supports_video:
                    pricing = model.get("pricing", {})

                    # Format pricing for display
                    prompt_price = pricing.get("prompt", "0")
                    completion_price = pricing.get("completion", "0")

                    # Convert to readable format (per 1M tokens)
                    try:
                        prompt_cost = float(prompt_price) * 1000000 if prompt_price else 0
                        completion_cost = float(completion_price) * 1000000 if completion_price else 0
                        price_str = f"${prompt_cost:.2f}/${completion_cost:.2f} per 1M tokens"
                        if prompt_cost == 0 and completion_cost == 0:
                            price_str = "Free"
                    except (ValueError, TypeError):
                        price_str = ""

                    models.append({
                        "id": model_id,
                        "name": name,
                        "description": model.get("description", ""),
                        "context_length": model.get("context_length"),
                        "input_modalities": input_modalities,
                        "pricing": price_str,
                        "supports_video": True,
                    })

            # Sort: free models first, then by name
            def sort_key(m):
                is_free = ":free" in m["id"] or m.get("pricing") == "Free"
                return (0 if is_free else 1, m["name"].lower())

            models.sort(key=sort_key)

            return models if models else self.DEFAULT_MODELS

        except Exception as e:
            return self.DEFAULT_MODELS

    def validate_api_key(self) -> bool:
        """Validate OpenRouter API key synchronously (deprecated, use async version)."""
        # This is a simple check - just verify the key format
        try:
            api_key = self._get_api_key()
            return api_key.startswith("sk-or-") and len(api_key) > 20
        except Exception:
            return False

    async def validate_api_key_async(self) -> bool:
        """Validate OpenRouter API key by making a test request."""
        try:
            api_key = self._get_api_key()
            client = self._get_http_client()

            headers = {
                "Authorization": f"Bearer {api_key}",
            }

            # Try to fetch models as validation
            response = await client.get(
                f"{self.BASE_URL}/models",
                headers=headers
            )

            if response.status_code == 401 or response.status_code == 403:
                return False

            return response.status_code == 200
        except AuthenticationError:
            return False
        except Exception:
            return False

    async def combine_analyses(self, analyses: List[str], combine_prompt: str) -> str:
        """Combine multiple chunk analyses into one."""
        try:
            api_key = self._get_api_key()
            client = self._get_http_client()

            # Join all analyses
            combined_text = "\n\n---\n\n".join([
                f"=== Chunk {i+1} Analysis ===\n{analysis}"
                for i, analysis in enumerate(analyses)
            ])

            full_prompt = f"{combine_prompt}\n\n{combined_text}"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": full_prompt
                    }
                ],
                "max_tokens": 16000
            }

            response = await client.post(
                f"{self.BASE_URL}/chat/completions",
                headers=headers,
                json=payload
            )

            if response.status_code == 429:
                raise RateLimitError("Rate limit exceeded")

            data = response.json()
            return data["choices"][0]["message"]["content"]

        except RateLimitError:
            raise
        except Exception as e:
            raise AnalyzerError(f"Failed to combine analyses: {e}")

    async def close(self):
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
