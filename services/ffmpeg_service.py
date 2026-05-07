import os
import subprocess
from pathlib import Path
from typing import Callable

from services.task_result import TaskResult


class FfmpegService:
    def __init__(self, ffmpeg_exe: Path, log_fn: Callable, subp_flags: int = 0):
        self.ffmpeg_exe = ffmpeg_exe
        self.log = log_fn
        self.subp_flags = subp_flags

    def extract_audio(self, video_path: str, output_mp3: str) -> TaskResult:
        cmd = [
            str(self.ffmpeg_exe), "-y", "-i", str(video_path),
            "-vn", "-acodec", "libmp3lame", "-ab", "320k", str(output_mp3),
        ]
        try:
            subprocess.run(cmd, check=True, creationflags=self.subp_flags)
            return TaskResult(success=True, path=str(output_mp3))
        except Exception as e:
            return TaskResult(success=False, error=str(e))

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
        try:
            subprocess.run(cmd, check=True, creationflags=self.subp_flags)
            return TaskResult(success=True, path=str(output))
        except Exception as e:
            return TaskResult(success=False, error=str(e))

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
        if subtitle and os.path.exists(subtitle):
            self.log(f"💬 將自動封裝 YouTube CC 字幕：{os.path.basename(subtitle)}")
        else:
            subtitle = None

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

        # Dynamic subtitle index: count -i flags already in cmd
        subtitle_input_index = sum(1 for x in cmd if x == "-i")

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

        try:
            subprocess.run(cmd, check=True, creationflags=self.subp_flags)
            self.log(f"  ✅ {vfmt} 合成完成: {os.path.basename(output)}")
            return TaskResult(success=True, path=str(output))
        except Exception as e:
            self.log(f"  ❌ {vfmt} 合成出錯: {str(e)}")
            return TaskResult(success=False, error=str(e))
