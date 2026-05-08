import html as _html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from services.task_result import TaskResult


class DownloadService:
    def __init__(
        self,
        py_dir: Path,
        lib_dir: Path,
        bin_dir: Path,
        log_fn: Callable,
        progress_fn: Callable,
        cancel_event,
        subp_flags: int,
        cookie_browser_fn: Callable[[], str],
        debug_log_fn: Callable | None = None,
    ):
        self.py_dir = py_dir
        self.lib_dir = lib_dir
        self.bin_dir = bin_dir
        self.log = log_fn
        self.dlog = debug_log_fn or (lambda msg: None)
        self._progress = progress_fn
        self.cancel_event = cancel_event
        self.subp_flags = subp_flags
        self._cookie_browser_fn = cookie_browser_fn
        self._yt_js_runtime_cache: list | None = None
        self._yt_js_runtime_notice_shown = False
        self._current_process = None
        self.last_downloaded_subtitle: str | None = None

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_youtube_video_id(url: str) -> str | None:
        match = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([0-9A-Za-z_-]{11})", url)
        return match.group(1) if match else None

    @staticmethod
    def sanitize_filename(title: str, max_len: int = 80) -> str:
        illegal = r'\/:*?"<>|'
        for ch in illegal:
            title = title.replace(ch, "_")
        title = "".join(char for char in title if char.isprintable())
        title = re.sub(r"[\s_]+", "_", title).strip("_")
        if len(title) > max_len:
            title = title[:max_len]
        while len(title.encode("utf-8", errors="replace")) > 180:
            title = title[:-1]
        return title.strip() or "video"

    # ------------------------------------------------------------------
    # yt-dlp command helpers
    # ------------------------------------------------------------------

    def _get_ytdlp_command_base(self) -> list[str]:
        ytdlp_exe = self.py_dir / "Scripts" / "yt-dlp.exe"
        if ytdlp_exe.exists():
            return [str(ytdlp_exe)]
        ytdlp_main = self.lib_dir / "yt_dlp" / "__main__.py"
        if ytdlp_main.exists():
            from pathlib import Path as _P
            local_python = self.py_dir / "python.exe"
            return [str(local_python), str(ytdlp_main)]
        local_python = self.py_dir / "python.exe"
        return [str(local_python), "-m", "yt_dlp"]

    def _get_cookie_opts(self) -> list[str]:
        browser = self._cookie_browser_fn()
        if browser == "none":
            return []
        return ["--cookies-from-browser", browser]

    def _get_ytdlp_js_runtime_opts(self) -> list[str]:
        if self._yt_js_runtime_cache is not None:
            return list(self._yt_js_runtime_cache)

        # EJS remote challenge solver — required by yt-dlp for YouTube JS challenges
        remote_opts = ["--remote-components", "ejs:github"]

        runtime_candidates = [
            ("deno", shutil.which("deno")),
            ("node", shutil.which("node")),
            ("bun", shutil.which("bun")),
        ]
        quickjs_path = shutil.which("quickjs") or shutil.which("qjs")
        if quickjs_path:
            runtime_candidates.append(("quickjs", quickjs_path))

        for runtime_name, runtime_path in runtime_candidates:
            if runtime_path:
                self._yt_js_runtime_cache = [
                    "--js-runtimes", f"{runtime_name}:{runtime_path}",
                ] + remote_opts
                if not self._yt_js_runtime_notice_shown:
                    self.log(f"  ℹ️ 已啟用 yt-dlp JavaScript runtime：{runtime_name} + EJS challenge solver")
                    self._yt_js_runtime_notice_shown = True
                return list(self._yt_js_runtime_cache)

        self._yt_js_runtime_cache = remote_opts
        if not self._yt_js_runtime_notice_shown:
            self.log("  ⚠️ 未偵測到本地 JS runtime (deno/node/bun)，改用 EJS remote challenge solver。")
            self._yt_js_runtime_notice_shown = True
        return list(self._yt_js_runtime_cache)

    # ------------------------------------------------------------------
    # Subtitle helpers
    # ------------------------------------------------------------------

    def _clean_srt_file(self, path: str) -> None:
        """Strip YouTube VTT artifacts that survive --convert-subs srt."""
        try:
            raw = Path(path).read_text(encoding="utf-8", errors="replace")
            size_before = len(raw)

            # --- debug: log raw head before cleaning ---
            sample_raw = raw[:600].replace("\n", "↵")
            self.dlog(f"[SRT-RAW ] {Path(path).name}  ({size_before} chars)")
            self.dlog(f"[SRT-RAW ] head600: {sample_raw}")

            # count artifact types for the report
            timing_tags = re.findall(r"<\d{2}:\d{2}:\d{2}[.,]\d+>", raw)
            html_tags   = re.findall(r"<[^>]+>", raw)
            entities    = re.findall(r"&(?:[a-zA-Z]+|#\d+);", raw)

            # strip inline word-timing timestamps: <00:00:01.840>
            cleaned = re.sub(r"<\d{2}:\d{2}:\d{2}[.,]\d+>", "", raw)
            # strip all HTML/XML tags: <c>, </c>, <i>, <b>, <font ...>, etc.
            cleaned = re.sub(r"<[^>]+>", "", cleaned)
            # decode HTML entities: &amp; &#39; &lt; etc.
            cleaned = _html.unescape(cleaned)
            # collapse runs of 3+ blank lines
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

            size_after = len(cleaned)
            sample_clean = cleaned.strip()[:600].replace("\n", "↵")
            self.dlog(
                f"[SRT-CLEAN] timing×{len(timing_tags)} html×{len(html_tags)} "
                f"entity×{len(entities)}  {size_before}→{size_after} chars"
            )
            self.dlog(f"[SRT-CLEAN] head600: {sample_clean}")

            if timing_tags or html_tags or entities:
                self.log(
                    f"  🧹 字幕清洗：timing×{len(timing_tags)} html-tag×{len(html_tags)} "
                    f"entity×{len(entities)}，{size_before}→{size_after} 字元"
                )

            Path(path).write_text(cleaned.strip() + "\n", encoding="utf-8-sig")
        except Exception as e:
            self.dlog(f"[SRT-CLEAN] 失敗: {e}")

    def normalize_subtitle_filename(self, subtitle_file: str, desired_stem: str) -> str:
        try:
            subtitle_path = Path(subtitle_file)
            if not subtitle_path.exists():
                return subtitle_file
            desired_path = subtitle_path.parent / f"{desired_stem}.srt"
            if subtitle_path.resolve() == desired_path.resolve():
                return str(subtitle_path)
            if desired_path.exists():
                try:
                    desired_path.unlink()
                except Exception:
                    pass
            shutil.move(str(subtitle_path), str(desired_path))
            return str(desired_path)
        except Exception:
            return subtitle_file

    def align_subtitle_filename(self, subtitle_file: str, target_media_file: str) -> str:
        try:
            subtitle_path = Path(subtitle_file)
            target_path = Path(target_media_file)
            if not subtitle_path.exists() or not target_path.exists():
                return subtitle_file
            desired_path = target_path.parent / f"{target_path.stem}.srt"
            if subtitle_path.resolve() == desired_path.resolve():
                return str(subtitle_path)
            if desired_path.exists():
                try:
                    desired_path.unlink()
                except Exception:
                    pass
            shutil.move(str(subtitle_path), str(desired_path))
            self.log(f"  📝 字幕檔已對齊命名：{desired_path.name}")
            return str(desired_path)
        except Exception as e:
            self.log(f"  ⚠️ 字幕檔重新命名失敗，保留原檔名：{str(e)}")
            return subtitle_file

    def download_youtube_subtitle(self, url: str, output_dir: str, video_id: str) -> str | None:
        self.log("  > 正在檢查 YouTube CC 字幕...")

        ytdlp_cmd_base = self._get_ytdlp_command_base()
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)
        ytdlp_env["PYTHONIOENCODING"] = "utf-8"
        js_runtime_opts = self._get_ytdlp_js_runtime_opts()
        cookie_opts = self._get_cookie_opts()

        subtitle_out = os.path.join(output_dir, f"{video_id}.%(ext)s")
        subtitle_patterns = [
            f"{video_id}*.srt",
            f"{video_id}*.vtt",
            f"{video_id}*.ass",
            f"{video_id}*.srv3",
        ]

        def clear_old_subtitles():
            for pattern in subtitle_patterns:
                for old_file in Path(output_dir).glob(pattern):
                    try:
                        old_file.unlink()
                    except Exception:
                        pass

        def collect_subtitle_candidates():
            subtitle_candidates = []
            for pattern in subtitle_patterns:
                subtitle_candidates.extend(Path(output_dir).glob(pattern))
            return sorted(
                {str(path): path for path in subtitle_candidates}.values(),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

        def get_preferred_langs_from_metadata():
            preferred_groups = [
                ["zh-TW", "zh-Hant", "zh-HK", "cmn-Hant"],
                ["zh-CN", "zh-Hans", "cmn-Hans"],
                ["zh"],
                ["en"],
                ["ja-orig", "ja"],
            ]

            metadata_cmd = ytdlp_cmd_base + [
                "--skip-download",
                "--no-playlist",
                "--dump-single-json",
            ] + js_runtime_opts + cookie_opts + [url]

            self.dlog(f"[META-CMD] {' '.join(metadata_cmd)}")

            try:
                result = subprocess.run(
                    metadata_cmd,
                    capture_output=True,
                    text=True,
                    creationflags=self.subp_flags,
                    timeout=60,
                    encoding="utf-8",
                    errors="replace",
                    env=ytdlp_env,
                )
                self.dlog(f"[META-RC ] returncode={result.returncode}")
                if result.returncode != 0 or not result.stdout.strip():
                    err_preview = (result.stderr or result.stdout or "").strip()
                    if err_preview:
                        self.dlog(f"[META-ERR] {err_preview[:400]}")
                        self.log(f"  ⚠️ 讀取字幕語言清單失敗（代碼 {result.returncode}）：{err_preview[:180]}")
                    return []

                data = json.loads(result.stdout)
                manual = {k for k in (data.get("subtitles") or {}).keys() if k and k != "live_chat"}
                auto = {k for k in (data.get("automatic_captions") or {}).keys() if k and k != "live_chat"}
                self.dlog(f"[META-SUB] manual={sorted(manual)}  auto={sorted(auto)}")
                ordered: list[str] = []

                def append_lang(lang):
                    if lang and lang not in ordered:
                        ordered.append(lang)

                def find_exact_or_prefix(available, target):
                    lower_map = {code.lower(): code for code in available}
                    exact = lower_map.get(target.lower())
                    if exact:
                        return exact
                    for code in available:
                        lower_code = code.lower()
                        lower_target = target.lower()
                        if lower_code.startswith(lower_target + "-") or lower_code.startswith(lower_target + "_"):
                            return code
                    return None

                for available_set in (manual, auto):
                    for group in preferred_groups:
                        for lang in group:
                            found = find_exact_or_prefix(available_set, lang)
                            if found:
                                append_lang(found)
                                break

                original_lang = data.get("language")
                if original_lang:
                    append_lang(original_lang)

                for lang in sorted(manual):
                    append_lang(lang)
                for lang in sorted(auto):
                    append_lang(lang)

                return ordered[:8]
            except Exception as e:
                self.log(f"  ⚠️ 讀取字幕語言清單失敗，改用精簡策略重試：{str(e)}")
                return []

        candidate_langs = get_preferred_langs_from_metadata()
        if candidate_langs:
            self.log(f"  ℹ️ 已挑選優先字幕語言：{', '.join(candidate_langs[:5])}")
        else:
            candidate_langs = ["zh-TW", "zh-Hant", "zh-HK", "zh-CN", "zh-Hans", "en", "ja-orig", "ja"]
            self.log("  ℹ️ 無法讀取完整字幕清單，改用精簡語言順序嘗試下載。")

        last_return_code = 0
        for lang in candidate_langs:
            clear_old_subtitles()
            self.log(f"  > 嘗試下載字幕語言：{lang}")

            cmd = ytdlp_cmd_base + [
                "--skip-download",
                "--no-playlist",
                "--ffmpeg-location", str(self.bin_dir),
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs", lang,
                "--sub-format", "srt/best",
                "--convert-subs", "srt",
                "-o", subtitle_out,
            ] + js_runtime_opts + cookie_opts + [url]

            self.dlog(f"[SUB-CMD ] lang={lang}  cmd: {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
                creationflags=self.subp_flags, encoding="utf-8", errors="replace",
                env=ytdlp_env,
            )
            self._current_process = process

            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    self.log("🛑 字幕下載已取消。")
                    self._current_process = None
                    return None
                line = line.strip()
                if not line:
                    continue
                self.dlog(f"[SUB-OUT ] {line}")
                if any(token in line for token in ["subtitle", "Subtitles", "Writing video subtitles", "Deleting original file"]):
                    self.log(f"    {line}")
                elif "WARNING" in line.upper():
                    self.log(f"  ⚠️ {line}")
                elif "ERROR" in line.upper():
                    self.log(f"  ❌ {line}")

            process.wait()
            self._current_process = None
            last_return_code = process.returncode
            self.dlog(f"[SUB-RC  ] lang={lang}  returncode={last_return_code}")
            subtitle_candidates = collect_subtitle_candidates()

            if subtitle_candidates:
                if process.returncode != 0:
                    self.log("  ⚠️ 字幕下載程序部分失敗，但已找到可用字幕檔，將直接採用。")
                selected = subtitle_candidates[0]
                self._clean_srt_file(str(selected))
                self.log(f"  ✅ 已找到字幕檔：{selected.name}")
                return str(selected)

            if process.returncode == 0:
                self.log(f"  ℹ️ 語言 {lang} 沒有可下載字幕，改試下一個語言。")
            else:
                self.log(f"  ⚠️ 語言 {lang} 下載失敗，改試下一個語言。")

        if last_return_code != 0:
            self.log("  ⚠️ 字幕下載程序未成功完成，將略過字幕。")
            return None

        subtitle_candidates = collect_subtitle_candidates()
        if not subtitle_candidates:
            self.log("  ℹ️ 這支影片沒有可用的 YouTube CC 字幕。")
            return None

        prefer_keywords = [
            "zh-tw", "zh-hant", "zh-hk", "zh-cn", "zh-hans", ".zh", "_zh", "-zh",
            "cmn-hant", "cmn-hans", "en", "english", "ja-orig", "ja",
        ]
        selected = None
        lower_map = [(path, path.name.lower()) for path in subtitle_candidates]
        for keyword in prefer_keywords:
            for path, lower_name in lower_map:
                if keyword in lower_name:
                    selected = path
                    break
            if selected:
                break
        if not selected:
            selected = subtitle_candidates[0]

        self._clean_srt_file(str(selected))
        self.log(f"  ✅ 已找到字幕檔：{selected.name}")
        return str(selected)

    # ------------------------------------------------------------------
    # Main download methods
    # ------------------------------------------------------------------

    def pure_download_file(
        self, url: str, output_dir: str, file_type: str, quality: str = "1080", video_id: str | None = None
    ) -> TaskResult:
        ytdlp_cmd_base = self._get_ytdlp_command_base()
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)

        common_opts = [
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress",
        ] + self._get_cookie_opts()

        if video_id:
            out_template = os.path.join(output_dir, f"%(title).80s_{video_id}.%(ext)s")
        else:
            out_template = os.path.join(output_dir, "%(title).100s.%(ext)s")

        if file_type == "mp3":
            cmd = ytdlp_cmd_base + common_opts + [
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "320K",
                "-o", out_template,
                url,
            ]
        else:
            if quality == "best":
                fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            else:
                fmt = (
                    f"bestvideo[ext=mp4][height<={quality}]"
                    f"+bestaudio[ext=m4a]"
                    f"/best[ext=mp4][height<={quality}]"
                    f"/best[ext=mp4]/best"
                )
            cmd = ytdlp_cmd_base + common_opts + [
                "-f", fmt,
                "--merge-output-format", "mp4",
                "-o", out_template,
                url,
            ]

        self.log(f"    執行: {file_type.upper()} 下載中...")
        ext = "mp3" if file_type == "mp3" else "mp4"
        files_before = set(Path(output_dir).glob(f"*.{ext}"))

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, universal_newlines=True,
            creationflags=self.subp_flags, encoding="utf-8", errors="replace",
            env=ytdlp_env,
        )
        self._current_process = process
        last_percent = -1

        for line in process.stdout:
            if self.cancel_event.is_set():
                process.terminate()
                self.log("🛑 下載已取消。")
                return TaskResult(success=False, error="cancelled")
            line = line.strip()
            if not line:
                continue
            if "[download]" in line and "%" in line:
                m = re.search(r"(\d+\.\d+)%", line)
                if m:
                    pct = float(m.group(1))
                    if int(pct) > last_percent:
                        self.log(f"    {line}")
                        last_percent = int(pct)
                        self._progress(int(pct), f"下載 {file_type.upper()}")
            else:
                prefix = "  ❌ " if "ERROR" in line.upper() else "    "
                self.log(f"{prefix}{line}")

        process.wait()
        self._current_process = None

        files_after = set(Path(output_dir).glob(f"*.{ext}"))
        new_files = files_after - files_before
        if new_files:
            newest = max(new_files, key=lambda f: f.stat().st_mtime)
            for nf in new_files:
                self.log(f"  ✅ {file_type.upper()} 下載完成：{nf.name}")
            return TaskResult(success=True, path=str(newest))
        elif process.returncode == 0:
            if video_id:
                matching_files = sorted(
                    Path(output_dir).glob(f"*_{video_id}.{ext}"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if matching_files:
                    self.log(f"  ✅ {file_type.upper()} 檔案已存在，直接使用：{matching_files[0].name}")
                    return TaskResult(success=True, path=str(matching_files[0]))
            self.log(f"  ⚠️ yt-dlp 回報成功但在輸出目錄找不到新的 {ext.upper()} 檔案。")
            self.log(f"     可能原因：ffmpeg 未安裝／路徑錯誤、格式合併失敗，請查看上方日誌。")
            return TaskResult(success=False, error="yt-dlp 回報成功但找不到輸出檔")
        else:
            self.log(f"  ❌ {file_type.upper()} 下載失敗（代碼: {process.returncode}）。")
            return TaskResult(success=False, error=f"exit code {process.returncode}")

    def download_youtube(
        self, url: str, output_dir: str, mode: str = "both", download_subtitles: bool = False
    ):
        """下載 YouTube 影片/音訊。
        mode="both" → (video_file, audio_file)
        mode="mp4"  → video_file
        mode="mp3"  → audio_file
        """
        self.log("🚀 正在下載 YouTube 內容...")
        self.last_downloaded_subtitle = None

        video_id = self.extract_youtube_video_id(url) or "temp_id"
        ytdlp_cmd_base = self._get_ytdlp_command_base()
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)

        common_opts = [
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress",
        ] + self._get_ytdlp_js_runtime_opts() + self._get_cookie_opts()

        def run_ytdlp_with_logging(cmd, step_name):
            self.log(f"  > 正在下載 {step_name}...")
            self.dlog(f"[DL-CMD  ] {step_name}: {' '.join(cmd)}")
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
                creationflags=self.subp_flags,
                encoding="utf-8", errors="replace", env=ytdlp_env,
            )
            self._current_process = process
            last_percent = -1
            recent_errors: list[str] = []
            recent_lines: list[str] = []
            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    return False, ["使用者取消"]
                line = line.strip()
                if not line:
                    continue
                self.dlog(f"[DL-OUT  ] {line}")
                recent_lines.append(line)
                if len(recent_lines) > 12:
                    recent_lines.pop(0)
                if "[download]" in line and "%" in line:
                    match = re.search(r"(\d+\.\d+)%", line)
                    if match:
                        percent = float(match.group(1))
                        if int(percent) > last_percent:
                            self.log(f"    {line}")
                            last_percent = int(percent)
                elif any(x in line for x in ["[ffmpeg]", "Merging", "Extracting", "Destination"]):
                    self.log(f"    {line}")
                elif "ERROR" in line.upper():
                    self.log(f"  ❌ {line}")
                    recent_errors.append(line)
                    if len(recent_errors) > 6:
                        recent_errors.pop(0)
            process.wait()
            self._current_process = None
            self.dlog(f"[DL-RC   ] {step_name}: returncode={process.returncode}")
            if process.returncode != 0 and recent_errors:
                self.log(f"  ⚠️ {step_name} 失敗摘要：{recent_errors[-1][:220]}")
            elif process.returncode != 0 and recent_lines:
                self.log(f"  ⚠️ {step_name} 最後輸出：{recent_lines[-1][:220]}")
            return process.returncode == 0, (recent_errors or recent_lines)

        def find_downloaded_file(pattern):
            files = list(Path(output_dir).glob(pattern))
            if files:
                files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                return str(files[0])
            return None

        video_file = None
        audio_file = None

        # 取得安全短檔名
        safe_name = video_id
        self.dlog(f"[DL-START] url={url}  video_id={video_id}  mode={mode}  subs={download_subtitles}")
        try:
            ytdlp_env_info = ytdlp_env.copy()
            ytdlp_env_info["PYTHONIOENCODING"] = "utf-8"
            title_cmd = ytdlp_cmd_base + ["--no-playlist"] + self._get_ytdlp_js_runtime_opts() + ["--print", "%(title)s", url]
            self.dlog(f"[TITLE-CMD] {' '.join(title_cmd)}")
            title_result = subprocess.run(
                title_cmd,
                capture_output=True, text=True, creationflags=self.subp_flags,
                timeout=30, encoding="utf-8", errors="replace", env=ytdlp_env_info,
            )
            self.dlog(f"[TITLE-RC ] returncode={title_result.returncode}")
            raw_title = title_result.stdout.strip().splitlines()[0] if title_result.stdout.strip() else ""
            if raw_title:
                raw_title = raw_title.replace("[", "(").replace("]", ")")
                safe_name = self.sanitize_filename(raw_title, max_len=80)
                self.dlog(f"[TITLE   ] raw={raw_title!r}  safe={safe_name!r}")
                self.log(f"  📝 影片標題: {raw_title}")
                self.log(f"  📝 安全檔名: {safe_name}")
        except Exception as e:
            self.dlog(f"[TITLE-ERR] {e}")
            self.log(f"  ⚠️ 取得標題失敗，使用影片 ID 作為檔名: {str(e)}")

        if download_subtitles and mode in ["both", "mp4"]:
            self.last_downloaded_subtitle = self.download_youtube_subtitle(url, output_dir, video_id)

        # 下載 MP4
        if mode in ["both", "mp4"]:
            mp4_out = os.path.join(output_dir, f"{safe_name}_{video_id}.mp4")
            video_format = "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best"

            mp4_attempts = [
                (
                    "MP4 影片",
                    ytdlp_cmd_base + common_opts + [
                        "-f", video_format,
                        "--merge-output-format", "mp4",
                        "-o", mp4_out,
                        url,
                    ],
                ),
                (
                    "MP4 相容模式",
                    ytdlp_cmd_base + common_opts + [
                        "-f", "bv*+ba/b",
                        "--recode-video", "mp4",
                        "-o", mp4_out,
                        url,
                    ],
                ),
            ]

            mp4_ok = False
            last_mp4_errors: list[str] = []
            for idx, (attempt_name, mp4_cmd) in enumerate(mp4_attempts, start=1):
                if idx > 1:
                    self.log("  ℹ️ 主要 MP4 格式失敗，改用相容模式重試...")
                success, err_lines = run_ytdlp_with_logging(mp4_cmd, attempt_name)
                last_mp4_errors = err_lines
                if not success:
                    continue
                if os.path.exists(mp4_out):
                    video_file = mp4_out
                    mp4_ok = True
                    self.log(f"  ✅ MP4 下載完成: {os.path.basename(video_file)}")
                    if self.last_downloaded_subtitle:
                        self.last_downloaded_subtitle = self.align_subtitle_filename(
                            self.last_downloaded_subtitle, video_file
                        )
                    break
                else:
                    video_file = find_downloaded_file("*.mp4")
                    if video_file:
                        mp4_ok = True
                        self.log(f"  ✅ MP4 下載完成: {os.path.basename(video_file)}")
                        if self.last_downloaded_subtitle:
                            self.last_downloaded_subtitle = self.align_subtitle_filename(
                                self.last_downloaded_subtitle, video_file
                            )
                        break

            if not mp4_ok:
                if last_mp4_errors:
                    self.log(f"  ❌ MP4 下載失敗摘要：{last_mp4_errors[-1][:220]}")
                self.log("  ❌ MP4 下載過程出錯")
                if mode == "both":
                    return None, None
                else:
                    return None
            elif not video_file:
                self.log("  ❌ MP4 下載失敗: 找不到下載後的檔案")
                if mode == "both":
                    return None, None
                else:
                    return None

        # 下載 MP3
        if mode in ["both", "mp3"]:
            self.log("  > 正在準備 MP3 音訊...")
            mp3_out = os.path.join(output_dir, f"{safe_name}_{video_id}_audio.mp3")
            mp3_cmd = ytdlp_cmd_base + common_opts + [
                "-x", "--audio-format", "mp3",
                "--audio-quality", "320K",
                "-o", mp3_out,
                url,
            ]
            mp3_ok, _mp3_errs = run_ytdlp_with_logging(mp3_cmd, "MP3 音訊")
            if mp3_ok:
                if os.path.exists(mp3_out):
                    audio_file = mp3_out
                    self.log(f"  ✅ MP3 下載完成: {os.path.basename(audio_file)}")
                else:
                    audio_file = find_downloaded_file("*.mp3")
                    if audio_file:
                        self.log(f"  ✅ MP3 下載完成: {os.path.basename(audio_file)}")
                    else:
                        self.log("  ❌ MP3 下載失敗: 找不到下載後的檔案")
                        if mode == "both":
                            return video_file, None
                        else:
                            return None
            else:
                self.log("  ❌ MP3 下載過程出錯")
                if mode == "both":
                    return video_file, None
                else:
                    return None

        if mode == "both":
            return video_file, audio_file
        else:
            return video_file if mode == "mp4" else audio_file
