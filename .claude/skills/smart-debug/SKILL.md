---
description: Analyze and debug complex issues with structured root cause analysis. Use when there are errors, exceptions, FFmpeg failures, yt-dlp failures, or unexpected behavior.
when_to_use: Use when the user reports a bug, error message, stack trace, crash, or something not working as expected in the application.
model: claude-sonnet-4-6
---

# Smart Debug — 智能問題診斷

Debug complex issues using structured analysis. Provide the error message, stack trace, or symptom description.

## Debugging Approach

### 1. Error Analysis
- Analyze the error message and stack trace
- Identify code paths leading to the issue
- Reproduce the problem systematically
- Isolate the root cause

### 2. Investigation Steps
1. Read relevant source files around the error location
2. Trace the call chain backwards from the failure point
3. Check for `None`/type/encoding issues (common in this project)
4. Examine recent git changes that may have introduced the regression

### 3. FFmpeg / subprocess errors
- Check `[KTV-RC]` and `[KTV-FF]` lines in debug log
- Verify all input file paths exist before the command runs
- Check FFmpeg exit code and last stderr line

### 4. yt-dlp / download errors
- Check if yt-dlp is current (`py -m pip show yt-dlp`)
- Verify `--remote-components ejs:github` flag is present
- Examine `[DL-CMD]` lines in debug log

### 5. UI / tkinter errors
- Check if widget exists before configure (`AttributeError`)
- Verify `_content_frame` parent is used (not `self.root`)
- CTk widgets need explicit `font=` parameter

## Output Structure

**Root Cause**: precise identification of the bug source

**Solution Options**:
1. Quick Fix — minimal change, immediate relief
2. Proper Fix — best long-term solution
3. Preventive — avoid similar issues

**Implementation**: specific code changes with [file:line](file) references

---
Issue to debug: $ARGUMENTS
