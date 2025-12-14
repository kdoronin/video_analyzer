"""
Video processing utilities using FFmpeg.
Handles video chunking and metadata extraction.
"""
import os
import subprocess
import json
from typing import List, Dict, Optional
from pathlib import Path

from app.config import config_manager


class VideoProcessor:
    """Processes videos: extracts metadata, splits into chunks."""

    def __init__(self, temp_dir: Optional[str] = None):
        self.temp_dir = temp_dir or config_manager.settings.temp_directory
        self.chunk_duration = config_manager.settings.chunk_duration_minutes * 60  # seconds
        os.makedirs(self.temp_dir, exist_ok=True)

    def get_video_duration(self, video_path: str) -> float:
        """Get video duration in seconds using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Failed to get video duration: {e}")

    def get_video_info(self, video_path: str) -> Dict:
        """Get comprehensive video information."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)

            video_stream = next(
                (s for s in data.get("streams", []) if s["codec_type"] == "video"),
                {}
            )

            return {
                "duration": float(data["format"].get("duration", 0)),
                "size_bytes": int(data["format"].get("size", 0)),
                "format": data["format"].get("format_name", "unknown"),
                "width": video_stream.get("width", 0),
                "height": video_stream.get("height", 0),
                "codec": video_stream.get("codec_name", "unknown"),
                "fps": self._parse_fps(video_stream.get("r_frame_rate", "0/1")),
            }
        except Exception as e:
            raise RuntimeError(f"Failed to get video info: {e}")

    def _parse_fps(self, fps_string: str) -> float:
        """Parse FPS from ffprobe format (e.g., '30000/1001')."""
        try:
            if "/" in fps_string:
                num, den = fps_string.split("/")
                return round(float(num) / float(den), 2)
            return float(fps_string)
        except (ValueError, ZeroDivisionError):
            return 0.0

    def split_video(self, video_path: str, job_id: str) -> List[Dict]:
        """
        Split video into chunks if longer than chunk_duration.
        Returns list of chunk info dictionaries.
        """
        duration = self.get_video_duration(video_path)
        video_name = Path(video_path).stem

        # If video is short enough, no need to split
        if duration <= self.chunk_duration:
            return [{
                "path": video_path,
                "chunk_number": 1,
                "total_chunks": 1,
                "start_time": 0,
                "end_time": duration,
                "duration": duration,
                "is_original": True
            }]

        # Calculate number of chunks
        num_chunks = int(duration // self.chunk_duration)
        if duration % self.chunk_duration > 0:
            num_chunks += 1

        chunks = []
        job_temp_dir = os.path.join(self.temp_dir, job_id)
        os.makedirs(job_temp_dir, exist_ok=True)

        for i in range(num_chunks):
            start_time = i * self.chunk_duration
            end_time = min((i + 1) * self.chunk_duration, duration)
            chunk_duration = end_time - start_time

            chunk_filename = f"{video_name}_chunk_{i+1:03d}.mp4"
            chunk_path = os.path.join(job_temp_dir, chunk_filename)

            # Split using ffmpeg with stream copy (fast, no re-encoding)
            cmd = [
                "ffmpeg",
                "-y",  # Overwrite output
                "-ss", str(start_time),  # Start time
                "-i", video_path,
                "-t", str(chunk_duration),  # Duration
                "-c", "copy",  # Copy streams without re-encoding
                "-avoid_negative_ts", "make_zero",
                chunk_path
            ]

            try:
                subprocess.run(cmd, capture_output=True, check=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to split video at chunk {i+1}: {e.stderr.decode()}")

            chunks.append({
                "path": chunk_path,
                "chunk_number": i + 1,
                "total_chunks": num_chunks,
                "start_time": start_time,
                "end_time": end_time,
                "duration": chunk_duration,
                "is_original": False
            })

        return chunks

    def extract_frame(self, video_path: str, timecode: str, output_path: str) -> bool:
        """Extract a single frame at specified timecode."""
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", timecode,
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",  # High quality JPEG
            output_path
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def cleanup_job(self, job_id: str):
        """Clean up temporary files for a job."""
        import shutil
        job_temp_dir = os.path.join(self.temp_dir, job_id)
        if os.path.exists(job_temp_dir):
            shutil.rmtree(job_temp_dir)


def seconds_to_timecode(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def timecode_to_seconds(timecode: str) -> float:
    """Convert HH:MM:SS or MM:SS format to seconds."""
    parts = timecode.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    else:
        return float(timecode)
