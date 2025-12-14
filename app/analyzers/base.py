"""
Base analyzer interface and common utilities.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import time
import random


class BaseAnalyzer(ABC):
    """Abstract base class for video analyzers."""

    def __init__(self, model_name: str, api_key: Optional[str] = None):
        self.model_name = model_name
        self.api_key = api_key
        self.max_retries = 5
        self.base_delay = 2  # seconds

    @abstractmethod
    async def analyze_video(
        self,
        video_path: str,
        prompt: str,
        chunk_info: Optional[Dict] = None
    ) -> str:
        """
        Analyze a video file with the given prompt.
        Returns the analysis text.
        """
        pass

    @abstractmethod
    async def get_available_models(self) -> List[Dict[str, Any]]:
        """
        Get list of available models from the provider.
        Returns list of model info dictionaries.
        """
        pass

    @abstractmethod
    def validate_api_key(self) -> bool:
        """Validate that the API key is working."""
        pass

    async def analyze_with_retry(
        self,
        video_path: str,
        prompt: str,
        chunk_info: Optional[Dict] = None
    ) -> str:
        """
        Analyze with exponential backoff retry logic.
        """
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                return await self.analyze_video(video_path, prompt, chunk_info)
            except RateLimitError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self._calculate_backoff(attempt)
                    await self._async_sleep(delay)
            except AnalyzerError:
                raise  # Don't retry on non-rate-limit errors

        raise last_exception or AnalyzerError("Max retries exceeded")

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter."""
        delay = self.base_delay * (2 ** attempt)
        jitter = random.uniform(0, delay * 0.1)
        return delay + jitter

    async def _async_sleep(self, seconds: float):
        """Async sleep helper."""
        import asyncio
        await asyncio.sleep(seconds)


class AnalyzerError(Exception):
    """Base exception for analyzer errors."""
    pass


class RateLimitError(AnalyzerError):
    """Rate limit exceeded error."""
    pass


class AuthenticationError(AnalyzerError):
    """Authentication/API key error."""
    pass


class VideoProcessingError(AnalyzerError):
    """Error processing video file."""
    pass
