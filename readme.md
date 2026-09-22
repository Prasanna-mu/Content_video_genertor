Build a modular, local-first AI agent that converts PowerPoint presentations into narrated MP4 educational videos.

The system must be designed for future extensibility. I want to be able to replace the LLM provider, LLM model, TTS provider, and potentially the video renderer without rewriting the orchestration logic.

## 1. Core requirements

The application should:

1. Read PPT files from a configured PPT input directory.
2. Maintain a JSON file containing the PPT files/jobs that need to be processed.
3. Create a processing session whenever `main.py` is triggered.
4. Store session metadata in SQLite.
5. Parse each PPT slide independently.
6. Extract slide text and relevant slide metadata.
7. Generate narration/content for every slide using a local Ollama LLM.
8. Evaluate the generated content for quality and factual/technical problems.
9. Regenerate content when the quality score is below the configured threshold.
10. Generate TTS audio separately for every slide.
11. Use the actual generated audio duration to construct the video timeline.
12. Combine slides and audio using FFmpeg.
13. Produce the final MP4.
14. Store the final video under a session-specific delivery directory.
15. Allow interrupted sessions to resume without repeating already completed stages.

The application must initially use:

* Provider: Ollama
* Model: Qwen2.5 3B
* Database: SQLite

But the architecture MUST make it easy to replace Ollama with another provider later.

---

# 2. Project architecture

Create a clean modular architecture similar to:

```text
ppt-video-agent/
│
├── main.py
├── .env
├── .env.example
├── requirements.txt
├── README.md
│
├── config/
│   ├── __init__.py
│   └── settings.py
│
├── providers/
│   ├── __init__.py
│   ├── base.py
│   └── ollama.py
│
├── orchestrator/
│   ├── __init__.py
│   ├── agent.py
│   ├── session_manager.py
│   ├── ppt_processor.py
│   ├── content_generator.py
│   ├── quality_checker.py
│   ├── tts_manager.py
│   └── video_renderer.py
│
├── models/
│   ├── __init__.py
│   └── database.py
│
├── utils/
│   ├── __init__.py
│   ├── filesystem.py
│   ├── json_utils.py
│   ├── logging.py
│   └── duration.py
│
├── processing/
│   ├── checklist/
│   ├── parseppt/
│   ├── llmout/
│   └── audio/
│
├── delivery/
│
├── input/
│   └── ppt/
│
├── data/
│   └── jobs.json
│
└── database/
    └── agent.db
```

Do not put the entire implementation inside `main.py`.

`main.py` should only be the CLI entry point and should call the orchestrator.

---

# 3. Environment configuration

Create `.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b

DATABASE_URL=sqlite:///database/agent.db

INPUT_PPT_DIR=input/ppt

PROCESSING_DIR=processing
DELIVERY_DIR=delivery

QUALITY_THRESHOLD=5

DEFAULT_TTS_PROVIDER=local
```

Create `.env.example` containing the same variables but without environment-specific secrets.

The model MUST NOT be hardcoded anywhere in the application.

For example:

```python
model = settings.OLLAMA_MODEL
```

not:

```python
model = "qwen2.5:3b"
```

This should allow me to change:

```env
OLLAMA_MODEL=qwen2.5:14b
```

without modifying Python code.

---

# 4. Provider abstraction

Create an abstract LLM provider interface.

For example:

```python
class LLMProvider(ABC):

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        pass
```

Then implement:

```text
providers/
    base.py
    ollama.py
```

`ollama.py` should implement the provider interface.

The orchestrator MUST depend on the abstract provider interface rather than directly importing Ollama-specific code.

Future providers should be possible:

```text
providers/
    base.py
    ollama.py
    openai.py
    gemini.py
    anthropic.py
```

without changing the main orchestration pipeline.

Create a provider factory:

```python
get_llm_provider()
```

which reads:

```env
LLM_PROVIDER=ollama
```

and returns the appropriate provider.

---

# 5. CLI

When `main.py` is executed:

```bash
python main.py
```

ask for:

```text
Content Quality:
1. Beginner
2. Intermediate
3. Technical

Select:
```

and:

```text
Video Length:
1. Normal
2. Intermediate
3. Long
4. Extreme

Select:
```

Use these duration definitions:

```text
Normal:
approximately 30–60 seconds per slide

Intermediate:
approximately 1–2 minutes per slide

Long:
approximately 2–3 minutes per slide

Extreme:
approximately 3–4 minutes per slide
```

Do not hardcode a single exact duration. Use target ranges.

The selected values must be stored in the session database.

---

# 6. Session creation

Every execution should create a unique session.

Example:

```text
session_20260921_183045_a82f
```

Store:

* session ID
* created_at
* started_at
* completed_at
* quality level
* length level
* target duration configuration
* LLM provider
* LLM model
* status
* total PPT count
* completed PPT count
* failed PPT count

Use SQLite.

Create appropriate database tables.

At minimum:

```text
sessions
ppt_jobs
slides
generation_attempts
audio_assets
video_jobs
```

Use SQLAlchemy or another clean SQLite ORM.

---

# 7. PPT job list

Create:

```text
data/jobs.json
```

Example:

```json
{
  "jobs": [
    {
      "id": "ppt_001",
      "file": "python_basics.pptx",
      "enabled": true
    },
    {
      "id": "ppt_002",
      "file": "django_intro.pptx",
      "enabled": true
    }
  ]
}
```

The agent should inspect this JSON when a session begins.

Only process jobs where:

```json
"enabled": true
```

Verify that the referenced PPT file actually exists.

If it doesn't exist, mark the job as failed and continue with the remaining PPTs.

---

# 8. Checklist artifact

For every session create:

```text
processing/checklist/<session_id>/
```

Create:

```text
checklist.json
```

Example:

```json
{
  "session_id": "session_20260921_183045_a82f",
  "jobs": [
    {
      "ppt_id": "ppt_001",
      "file": "python_basics.pptx",
      "status": "pending",
      "parse_status": "pending",
      "content_status": "pending",
      "quality_status": "pending",
      "tts_status": "pending",
      "video_status": "pending"
    }
  ]
}
```

Update this file after every major stage.

SQLite remains the source of truth; the checklist JSON is a human-readable processing artifact.

---

# 9. PPT parsing

For each PPT:

```text
input/ppt/python_basics.pptx
```

extract:

* PPT filename
* presentation metadata where available
* slide number
* slide title
* text boxes
* bullet points
* notes if available
* tables where possible
* image metadata where possible

Do not send the entire PPT directly to the LLM.

First create a structured representation.

Save:

```text
processing/parseppt/<session_id>/python_basics.json
```

Example:

```json
{
  "ppt": {
    "filename": "python_basics.pptx",
    "slide_count": 5
  },
  "slides": [
    {
      "slide_number": 1,
      "title": "Introduction to Python",
      "text": [
        "What is Python?",
        "Features",
        "Applications"
      ],
      "notes": ""
    }
  ]
}
```

The exact schema can be improved during implementation.

---

# 10. Content generation

After the PPT has been successfully parsed, generate narration for each slide.

Input:

```text
parsed PPT JSON
quality level
length level
target duration
```

The LLM must generate educational narration based on the actual slide content.

Important:

DO NOT simply read the slide text word-for-word.

The narration should explain the concepts represented by the slide.

For example:

Slide:

```text
Python
- Easy to learn
- Interpreted
- Dynamically typed
```

The narration should explain those points naturally instead of simply saying:

"Python. Easy to learn. Interpreted. Dynamically typed."

---

# 11. Duration-aware content generation

The content generator must consider the requested length.

Example:

```text
Normal
≈ 30–60 seconds/slide

Intermediate
≈ 1–2 minutes/slide

Long
≈ 2–3 minutes/slide

Extreme
≈ 3–4 minutes/slide
```

Use an estimated speaking rate to calculate a target word range.

For example:

```text
130–150 words/minute
```

For every slide calculate:

```text
target_min_words
target_max_words
```

Pass that target to the LLM.

However, the LLM output must NOT be assumed to have exactly the requested duration.

The TTS stage must measure the actual generated audio duration.

---

# 12. LLM output schema

Save generated content to:

```text
processing/llmout/<session_id>/<ppt_name>.json
```

Use structured JSON.

Example:

```json
{
  "ppt_name": "python_basics.pptx",
  "generation_config": {
    "quality": "beginner",
    "length": "long"
  },
  "slides": [
    {
      "slide_number": 1,
      "title": "Introduction to Python",
      "narration": "...",
      "estimated_word_count": 300,
      "target_word_range": {
        "min": 260,
        "max": 340
      },
      "quality_score": null,
      "quality_feedback": null,
      "generation_attempt": 1
    }
  ]
}
```

---

# 13. Quality checker

After content generation, run a separate quality evaluation.

Check for:

* factual errors
* hallucinations
* contradictions
* incorrect technical explanations
* invented facts
* misleading statements
* malformed narration
* irrelevant content
* missing important slide concepts
* repetition
* poor educational clarity
* obvious grammatical problems
* mismatch with the original PPT

Return:

```json
{
  "score": 8,
  "issues": [],
  "summary": "Content is technically consistent with the slide."
}
```

Score from:

```text
0–10
```

Use:

```env
QUALITY_THRESHOLD=5
```

If score is below 5:

```text
Generate again
→ evaluate again
→ repeat until acceptable
```

But add a maximum retry limit.

For example:

```env
MAX_CONTENT_RETRIES=3
```

Never create an infinite regeneration loop.

If the maximum retries are reached, mark the slide as requiring review and continue according to a configurable failure policy.

---

# 14. Important quality-checking rule

The quality checker must distinguish between:

1. Information explicitly present in the PPT.
2. Reasonable explanatory information.
3. Claims that cannot be supported by the provided material.

Do not automatically consider every additional explanation a hallucination.

The goal is to detect genuinely incorrect, misleading, or unsupported claims.

For technical content, prioritize correctness.

---

# 15. TTS architecture

Do NOT tightly couple TTS to the LLM provider.

Create:

```text
tts/
    base.py
    local.py
    factory.py
```

Use a TTS provider abstraction similar to the LLM provider.

For example:

```python
class TTSProvider(ABC):

    @abstractmethod
    def synthesize(self, text: str, output_path: str):
        pass
```

This will allow future providers such as:

```text
local TTS
Kokoro
Piper
Coqui
cloud TTS
etc.
```

The exact initial local TTS implementation can be selected based on what works reliably on the development machine.

---

# 16. Audio storage

Generate one audio file per slide.

Structure:

```text
processing/audio/<session_id>/<ppt_name>/
    slide_001.wav
    slide_002.wav
    slide_003.wav
    slide_004.wav
    slide_005.wav
```

For every audio file store:

* path
* duration
* format
* slide number
* TTS provider
* generation timestamp

in SQLite.

Also optionally create:

```text
audio_manifest.json
```

---

# 17. Video generation

The video should NOT use fixed slide durations.

Use the actual TTS audio duration.

For example:

```text
slide_001.png → 72.4 sec
slide_002.png → 65.2 sec
slide_003.png → 83.7 sec
slide_004.png → 58.3 sec
slide_005.png → 71.1 sec
```

Then construct the final timeline based on these durations.

Use FFmpeg.

The pipeline should:

1. Convert/render PPT slides into images if necessary.
2. Associate each slide image with its corresponding audio.
3. Create a video segment for every slide.
4. Combine the segments.
5. Combine the final video with the audio.
6. Encode into MP4 using a broadly compatible codec.

Final output:

```text
delivery/<session_id>/<ppt_name>.mp4
```

---

# 18. Video transitions

Initially keep the renderer simple and reliable.

Use:

```text
slide
→ audio
→ slide
→ audio
```

Do not introduce complex AI animation initially.

Design the renderer so transitions can be added later.

Possible future features:

* fade transitions
* zoom
* pan
* subtitles
* background music
* animated highlights
* slide element animations
* AI avatar
* generated illustrations

These should not be required for version 1.

---

# 19. Resumability

This is extremely important.

If processing stops here:

```text
PPT
 ↓
parse ✓
 ↓
LLM ✓
 ↓
TTS slide 1 ✓
TTS slide 2 ✓
TTS slide 3 ✗
```

When the application starts again, it should NOT repeat everything.

It should detect:

```text
parse = completed
LLM = completed
slide 1 = completed
slide 2 = completed
slide 3 = failed
```

and continue from slide 3.

Every processing stage must be idempotent where practical.

---

# 20. Logging

Create structured logs.

Example:

```text
[SESSION] Created session_20260921_183045_a82f
[PPT] Processing python_basics.pptx
[PARSER] 5 slides detected
[LLM] Generating narration for slide 1
[QUALITY] Slide 1 score = 8/10
[TTS] Generating slide 1 audio
[TTS] Duration = 67.4 seconds
[VIDEO] Building timeline
[VIDEO] Completed
```

Errors must contain enough information to debug the failed stage.

---

# 21. Error handling

Handle:

* Ollama unavailable
* model unavailable
* malformed LLM JSON
* PPT missing
* corrupted PPT
* TTS failure
* FFmpeg missing
* FFmpeg failure
* invalid audio
* invalid generated content
* database failure
* filesystem failure

Do not silently swallow exceptions.

Mark the appropriate job/stage as failed.

---

# 22. Main orchestrator

Create a high-level agent/orchestrator similar to:

```python
class VideoGenerationAgent:

    def run(self, quality, length):

        session = self.session_manager.create_session(
            quality=quality,
            length=length
        )

        jobs = self.load_jobs()

        for job in jobs:

            self.process_ppt(
                session=session,
                job=job
            )

        self.session_manager.complete_session(session)
```

The actual implementation should break this into smaller methods/classes.

The orchestrator should NOT contain provider-specific Ollama code.

---

# 23. Main.py responsibility

`main.py` should remain lightweight.

Conceptually:

```python
def main():
    quality = ask_quality()
    length = ask_length()

    agent = VideoGenerationAgent()

    agent.run(
        quality=quality,
        length=length
    )


if __name__ == "__main__":
    main()
```

Do not put PPT parsing, LLM prompts, TTS implementation, FFmpeg commands, or database logic inside `main.py`.

---

# 24. Database design

Create SQLite tables that can represent processing state.

At minimum:

### sessions

```text
id
created_at
started_at
completed_at
quality
length
llm_provider
llm_model
status
```

### ppt_jobs

```text
id
session_id
ppt_filename
status
started_at
completed_at
error
```

### slides

```text
id
ppt_job_id
slide_number
status
parsed
content_generated
quality_score
quality_feedback
```

### generation_attempts

```text
id
slide_id
attempt_number
prompt
response
quality_score
created_at
```

### audio_assets

```text
id
slide_id
provider
file_path
duration
status
created_at
```

### video_jobs

```text
id
ppt_job_id
output_path
duration
status
created_at
completed_at
```

Use proper foreign keys and indexes.

---

# 25. JSON should not replace SQLite

Use SQLite as the authoritative state database.

Use JSON files as processing artifacts.

Therefore:

```text
SQLite
    ↓
source of truth for status

JSON
    ↓
human-readable/generated artifacts
```

Do not attempt to use JSON as the primary database.

---

# 26. CLI progress

Show progress such as:

```text
Session: session_20260921_183045_a82f

PPT 1/3: Python Basics

[✓] Parse PPT
[✓] Generate slide content
[✓] Quality validation
[✓] Generate TTS
[▶] Build video

Slide 4/5
```

Use a CLI library such as Rich if appropriate.

---

# 27. Future extensibility

The architecture must make these changes easy:

```text
Qwen2.5 3B
        ↓
Qwen2.5 14B
```

by changing only:

```env
OLLAMA_MODEL=qwen2.5:14b
```

Also:

```text
Ollama
   ↓
OpenAI
```

by changing:

```env
LLM_PROVIDER=openai
```

and adding a provider implementation.

Similarly:

```text
TTS_PROVIDER=local
```

can later become:

```text
TTS_PROVIDER=kokoro
```

without changing the orchestrator.

---

# 28. Development approach

Do NOT implement everything blindly in one step.

Build and test in phases:

### Phase 1

Implement:

* project structure
* configuration
* `.env`
* SQLite
* session manager
* jobs.json
* main.py
* provider abstraction
* Ollama provider

Test:

```bash
python main.py
```

and verify session creation.

### Phase 2

Implement PPT parser.

Test:

```text
PPT → parseppt/session/ppt.json
```

without calling the LLM.

### Phase 3

Implement content generation.

Test:

```text
PPT JSON
→ Ollama
→ llmout/session/ppt.json
```

### Phase 4

Implement quality evaluation and regeneration.

Test retry behavior independently.

### Phase 5

Implement TTS.

Test:

```text
narration
→ slide audio
→ duration
```

### Phase 6

Implement FFmpeg video rendering.

Test one PPT with a few slides.

### Phase 7

Implement resumability and complete pipeline.

---

# 29. Testing requirements

Create tests for:

* session creation
* jobs.json loading
* missing PPT handling
* PPT parsing
* LLM provider factory
* malformed LLM response
* quality threshold
* regeneration limit
* TTS generation
* audio duration calculation
* video timeline generation
* resume behavior
* failed-stage recovery

Mock Ollama/TTS during unit tests so tests don't require the actual models.

---

# 30. Important implementation constraint

The system is initially intended to run locally.

Do not introduce unnecessary cloud APIs.

The initial architecture should work with:

```text
Local machine
+
Ollama
+
Qwen2.5:3B
+
Local TTS
+
FFmpeg
+
SQLite
```

The application should be able to operate without an internet connection once the required dependencies and models are installed.

---

# 31. Final expected workflow

The complete workflow should be:

```text
python main.py
        ↓
CLI asks:
Quality
Length
        ↓
Create session
        ↓
Read jobs.json
        ↓
Create checklist
        ↓
For each PPT
        ↓
Parse PPT
        ↓
Save parsed JSON
        ↓
Generate narration
        ↓
Quality check
        ↓
If score < threshold
        ↓
Regenerate
        ↓
Save final narration JSON
        ↓
TTS per slide
        ↓
Measure actual audio duration
        ↓
Build video timeline
        ↓
Render MP4 with FFmpeg
        ↓
Save to:

delivery/<session_id>/<ppt_name>.mp4

        ↓
Update SQLite
        ↓
Mark PPT completed
        ↓
Process next PPT
        ↓
Complete session
```

---

# 32. First task

Before writing large amounts of code:

1. Inspect the current project directory.
2. Determine whether an existing implementation already exists.
3. Do not overwrite existing working code without checking it.
4. Propose the final directory structure.
5. Identify reusable existing components.
6. Then implement Phase 1.
7. Run tests.
8. Report what was implemented and what remains.

Do not implement future phases until the current phase works correctly.

The final implementation should prioritize:

* modularity
* maintainability
* resumability
* local execution
* clear provider abstraction
* structured JSON
* SQLite state tracking
* deterministic video rendering
* easy model/provider replacement
* good error handling
* testability
