# Story 09.03 — Audio/video transcription with duration cap and binary dedup

## Objective
Add local `faster-whisper` transcription for audio/voice/video; cap duration to limit CPU; short-circuit re-uploads of byte-identical files.

## Scope

### In Scope
- Extend `services/api/app/operator_uploads/extractors.py`:
  - `_probe_duration(path) -> float` via `ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1` subprocess.
  - `async extract_audio(path, *, transcriber)` — duration check; raise `ExtractionError("audio_too_long")` if over `settings.operator_upload_max_audio_seconds`. Otherwise `transcriber.transcribe(path, language="ru")`.
  - `async extract_video(path, *, transcriber)` — duration check, then `subprocess.run(["ffmpeg","-y","-i",str(path),"-vn","-acodec","libmp3lame","-q:a","5",str(tmpfile)], check=True)` and delegate to `extract_audio`.
- `WhisperTranscriber` thin wrapper around `faster_whisper.WhisperModel(settings.faster_whisper_model_size, device="cpu", compute_type=settings.faster_whisper_compute_type, download_root=settings.faster_whisper_cache_dir)`. Lazy `_load()` method marked `# pragma: no cover`.
- Binary-SHA256 dedup helper `binary_sha256(path: Path) -> str` (streamed 64 KiB chunks).

### Out of Scope
- Persistence of `binary_sha256` to the candidate row (story 09.04); the helper is just utility code here.

## Implementation Notes
- New dep: `faster-whisper`.
- `ffmpeg` apt install in story 09.05.
- Whisper-cache volume mounted in story 09.05.

## Test Plan

### Unit
- Extend `tests/test_operator_uploads_extractors.py`: inject `FakeTranscriber` returning known Russian text; patch `_probe_duration` to return a known value; `audio_too_long` path asserts the transcriber was never called.
- `tests/test_binary_sha256.py` verifies streamed hashing of a known fixture.

### Integration
None until 09.04.

## Automated E2E verification
Deferred to 09.05.

## Manual Verification
Run `WhisperTranscriber` on a short Russian `.ogg` voice note in a dev shell; confirm Russian transcript. (Manual only — not in CI.)

## Done Criteria
- All branches covered with fakes.
- Coverage stays at 100%.
- ffmpeg/ffprobe calls patched in tests; real subprocesses not invoked from the test suite.
- `ruff check .` passes.
