import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from services.task_result import TaskResult


class FfmpegService:
    def __init__(
        self,
        ffmpeg_exe: Path,
        log_fn: Callable,
        subp_flags: int = 0,
        debug_log_fn: Callable | None = None,
    ):
        self.ffmpeg_exe = ffmpeg_exe
        self.log = log_fn
        self.subp_flags = subp_flags
        self.dlog = debug_log_fn if debug_log_fn is not None else lambda msg: None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: list[str], label: str) -> subprocess.CompletedProcess:
        """Run an FFmpeg command, capturing stderr so errors are never silently lost."""
        safe_parts = []
        for x in cmd:
            if " " in x or "=" in x or "," in x:
                safe_parts.append(f'"{x}"')
            else:
                safe_parts.append(x)
        self.dlog(f"[{label}-CMD] " + " ".join(safe_parts))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=self.subp_flags,
        )
        for line in result.stderr.splitlines():
            if line.strip():
                self.dlog(f"[{label}-FF ] {line}")
        self.dlog(f"[{label}-RC ] {result.returncode}")
        return result

    def _write_audio_metadata_file(self, titles: list[str]) -> str | None:
        """Write audio stream titles to a UTF-8 ffmetadata file; returns temp path or None."""
        try:
            fd, path = tempfile.mkstemp(suffix=".txt", prefix="ffmeta_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(";FFMETADATA1\n")
            return path
        except Exception as e:
            self.dlog(f"[META-FILE] failed to create temp file: {e}")
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_audio(self, video_path: str, output_mp3: str) -> TaskResult:
        cmd = [
            str(self.ffmpeg_exe), "-y", "-i", str(video_path),
            "-vn", "-acodec", "libmp3lame", "-ab", "320k", str(output_mp3),
        ]
        result = self._run(cmd, "AUDIO")
        if result.returncode != 0:
            err = result.stderr.strip()[-200:]
            return TaskResult(success=False, error=err or f"exit {result.returncode}")
        return TaskResult(success=True, path=str(output_mp3))

    def merge_stems(self, stems: list[str], output: str) -> TaskResult:
        n = len(stems)
        inputs: list[str] = []
        for f in stems:
            inputs.extend(["-i", str(f)])
        filter_str = "".join(f"[{i}:a]" for i in range(n))
        filter_str += f"amix=inputs={n}:duration=first[out]"
        cmd = [str(self.ffmpeg_exe), "-y"] + inputs + [
            "-filter_complex", filter_str, "-map", "[out]", "-b:a", "320k", str(output),
        ]
        result = self._run(cmd, "MERGE")
        if result.returncode != 0:
            err = result.stderr.strip()[-200:]
            return TaskResult(success=False, error=err or f"exit {result.returncode}")
        return TaskResult(success=True, path=str(output))

    def build_ktv_video(
        self,
        video: str,
        vocal: str,
        instrumental: str,
        subtitle: str | None,
        output: str,
        fmt: str,
        track_mode: str,
        vocal_mix: float,
        force_1080p: bool,
    ) -> TaskResult:
        vfmt = fmt.upper()
        instrumental_mix = 1.0 - vocal_mix
        vocal_pct = int(round(vocal_mix * 100))
        inst_pct = int(round(instrumental_mix * 100))

        self.log(f"🎬 正在合成 {vfmt} 伴唱帶（音軌模式：{'雙音軌' if track_mode == 'dual' else '左伴唱/右人聲'}）...")
        if track_mode == "dual":
            self.log(f"🎚️ 導唱混合比例：人聲 {vocal_pct}% / 伴奏 {inst_pct}%")
        else:
            self.log("🎚️ 目前為左伴唱／右人聲模式，混合比例設定不套用於此模式。")
        if force_1080p:
            self.log("🖼️ 已啟用強制等比輸出 1080p，必要時會補黑邊。")

        # Validate subtitle path
        if subtitle:
            if os.path.exists(subtitle):
                self.log(f"💬 將自動封裝 YouTube CC 字幕：{os.path.basename(subtitle)}")
                self.dlog(f"[KTV-SUB ] path={subtitle}  size={os.path.getsize(subtitle)}")
            else:
                self.dlog(f"[KTV-SUB ] subtitle path NOT FOUND, skipping: {subtitle}")
                self.log(f"  ⚠️ 字幕檔不存在，跳過封裝：{subtitle}")
                subtitle = None
        else:
            self.dlog("[KTV-SUB ] no subtitle (None)")

        missing = []
        if not os.path.exists(video):
            missing.append(f"影片檔: {os.path.basename(video)}")
        if not os.path.exists(vocal):
            missing.append(f"人聲檔: {os.path.basename(vocal)}")
        if not os.path.exists(instrumental):
            missing.append(f"伴奏檔: {os.path.basename(instrumental)}")
        if missing:
            self.log("  ❌ 合成失敗，缺少必要檔案：\n    - " + "\n    - ".join(missing))
            return TaskResult(success=False, error="missing_files")

        cmd = [str(self.ffmpeg_exe), "-y", "-i", str(video)]

        if track_mode == "lr":
            cmd += ["-i", str(instrumental), "-i", str(vocal)]
            audio_filter = (
                "[1:a]pan=mono|c0=c0[inst_mono];"
                "[2:a]pan=mono|c0=c0[voc_mono];"
                "[inst_mono][voc_mono]amerge=inputs=2[lr]"
            )
            audio_maps = [
                "-map", "0:v",
                "-map", "[lr]",
                "-metadata:s:a:0", "title=左伴唱／右人聲",
                "-ac", "2",
            ]
        else:
            cmd += ["-i", str(vocal), "-i", str(instrumental)]
            audio_filter = (
                f"[1:a][2:a]amix=inputs=2:duration=first:"
                f"weights='{vocal_mix:.2f} {instrumental_mix:.2f}'[mix]"
            )
            audio_maps = [
                "-map", "0:v",
                "-map", "[mix]",
                "-map", "2:a",
                "-metadata:s:a:0", f"title=導唱 (人聲{vocal_pct}% + 伴奏{inst_pct}%)",
                "-metadata:s:a:1", "title=伴唱 (純伴奏)",
            ]

        # Count -i flags now to get correct subtitle input index
        subtitle_input_index = sum(1 for x in cmd if x == "-i")
        self.dlog(f"[KTV-SUB ] subtitle_input_index={subtitle_input_index}  has_subtitle={bool(subtitle)}")

        if subtitle:
            cmd += ["-i", str(subtitle)]

        cmd += ["-filter_complex", audio_filter, *audio_maps, "-c:a", "aac", "-b:a", "320k"]

        if subtitle:
            cmd += [
                "-map", f"{subtitle_input_index}:0",
                "-disposition:s:0", "default",
                "-metadata:s:s:0", "title=YouTube CC",
            ]
            if vfmt == "MP4":
                cmd += ["-c:s", "mov_text"]
            else:
                cmd += ["-c:s", "srt"]

        if force_1080p:
            cmd += [
                "-vf", (
                    "scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
                ),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            ]
        else:
            cmd += ["-c:v", "copy"]

        if vfmt == "MP4":
            cmd += ["-movflags", "+faststart"]

        cmd += [str(output)]

        result = self._run(cmd, "KTV")
        if result.returncode != 0:
            # Surface the last meaningful FFmpeg error line
            err_lines = [l for l in result.stderr.splitlines() if l.strip()]
            err_msg = err_lines[-1] if err_lines else f"exit code {result.returncode}"
            self.log(f"  ❌ {vfmt} 合成出錯: {err_msg[:200]}")
            return TaskResult(success=False, error=err_msg)

        self.log(f"  ✅ {vfmt} 合成完成: {os.path.basename(output)}")
        return TaskResult(success=True, path=str(output))
