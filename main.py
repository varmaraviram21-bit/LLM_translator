import sys
import time
import json
import shutil
import tempfile
import re
from pathlib import Path

from google.genai import errors as genai_errors

from app.gemini_client import client, MODEL


# ============================================================
# LLMOps Telugu Translation Agent
# V3
#
# V2 improvements retained:
#   - Gemini 503 retry
#   - Exponential backoff
#   - Safe temporary ASCII video filename
#   - UTF-8 Windows support
#   - Video processing polling
#
# V3 improvements:
#   - Original SRT is the MASTER timeline
#   - Gemini cannot modify SRT timestamps/text
#   - Python merges Gemini analysis with original SRT
#   - Final output is structurally validated
#   - Exact segment count/order/timestamps/text are verified
# ============================================================


# ============================================================
# 1. RETRY WRAPPER
# ============================================================

def generate_content_with_retry(
    *,
    model,
    contents,
    max_retries=5,
    initial_delay=10
):
    """
    Retry Gemini generation when transient server/rate-limit
    errors occur.

    Examples:
        503 UNAVAILABLE
        429 RESOURCE_EXHAUSTED
    """

    delay = initial_delay

    for attempt in range(1, max_retries + 1):

        try:

            return client.models.generate_content(
                model=model,
                contents=contents
            )

        except genai_errors.ServerError as e:

            if attempt == max_retries:
                raise

            print(
                f"\nGemini server error ({e}). "
                f"Retrying in {delay}s... "
                f"(attempt {attempt}/{max_retries})"
            )

            time.sleep(delay)

            delay *= 2

    raise RuntimeError(
        "Gemini request failed after all retries."
    )


# ============================================================
# 2. WINDOWS UTF-8 SUPPORT
# ============================================================

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ============================================================
# 3. INPUT FILES
# ============================================================

VIDEO_PATH = Path(
    r"D:\Learning\LLMOps\input\Videos\01 - What is LLMOps ｜ LLMOPS Masters ｜ Euron.mp4"
)

SUBTITLE_PATH = Path(
    r"D:\Learning\LLMOps\input\Subtitles\01 - What is LLMOps ｜ LLMOPS Masters ｜ Euron.en.srt"
)


# ============================================================
# 4. OUTPUT
# ============================================================

OUTPUT_DIR = Path("output")

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

ANALYSIS_OUTPUT = (
    OUTPUT_DIR /
    "01_llmops_analysis.json"
)


# ============================================================
# 5. SRT PARSER
# ============================================================

def parse_srt(srt_path: Path):

    print("\n" + "=" * 60)
    print("Reading original SRT")
    print("=" * 60)

    text = srt_path.read_text(
        encoding="utf-8"
    )

    # Normalize line endings
    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    blocks = re.split(
        r"\n\s*\n",
        text.strip()
    )

    segments = []

    for block in blocks:

        lines = block.splitlines()

        if len(lines) < 3:
            continue

        index_text = lines[0].strip()

        # Skip malformed blocks
        if not index_text.isdigit():
            continue

        index = int(index_text)

        timestamp_line = lines[1].strip()

        if " --> " not in timestamp_line:
            continue

        start, end = timestamp_line.split(
            " --> ",
            1
        )

        # Preserve subtitle content as one string.
        # Do NOT allow Gemini to recreate this later.
        subtitle_text = " ".join(
            line.strip()
            for line in lines[2:]
        )

        segments.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "english": subtitle_text
            }
        )

    return segments


# ============================================================
# 6. VALIDATE ORIGINAL SRT
# ============================================================

def validate_original_srt(segments):

    if not segments:

        raise RuntimeError(
            "No subtitle segments were found."
        )

    indices = [
        segment["index"]
        for segment in segments
    ]

    # Check duplicate indices
    if len(indices) != len(set(indices)):

        raise RuntimeError(
            "Duplicate subtitle indices found "
            "in original SRT."
        )

    # Check expected sequential numbering
    expected_indices = list(
        range(
            1,
            len(segments) + 1
        )
    )

    if indices != expected_indices:

        raise RuntimeError(
            "Original SRT indices are not sequential.\n"
            f"Expected: 1 -> {len(segments)}\n"
            f"Found: {indices[:10]} ..."
        )

    print(
        f"\nOriginal SRT contains "
        f"{len(segments)} segments."
    )

    print(
        f"First segment: {segments[0]['index']}"
    )

    print(
        f"Last segment: {segments[-1]['index']}"
    )


# ============================================================
# 7. CREATE GEMINI SUBTITLE REFERENCE
# ============================================================

def build_srt_reference(segments):

    """
    Build a numbered subtitle reference for Gemini.

    IMPORTANT:
    Gemini uses this only for analysis.

    Python remains the authoritative source for:
        index
        start
        end
        english
    """

    lines = []

    for segment in segments:

        lines.append(
            f"[SEGMENT {segment['index']}]\n"
            f"{segment['english']}"
        )

    return "\n\n".join(lines)


# ============================================================
# 8. UPLOAD VIDEO
# ============================================================

def upload_video(video_path: Path):

    print("\n" + "=" * 60)
    print("Preparing video")
    print("=" * 60)

    # Gemini can have problems when filenames contain
    # Unicode characters. Use a safe temporary filename.
    safe_video_path = (
        Path(tempfile.gettempdir())
        / "llmops_01.mp4"
    )

    shutil.copyfile(
        video_path,
        safe_video_path
    )

    print(
        "Temporary safe video path:"
    )

    print(
        safe_video_path
    )

    print("\n" + "=" * 60)
    print("Uploading video to Gemini")
    print("=" * 60)

    video_file = client.files.upload(
        file=safe_video_path,
        config={
            "display_name": "llmops_01.mp4"
        }
    )

    print(
        "\nVideo uploaded successfully:"
    )

    print(
        video_file.name
    )

    return video_file


# ============================================================
# 9. WAIT FOR VIDEO PROCESSING
# ============================================================

def wait_for_video_processing(video_file):

    print("\n" + "=" * 60)
    print("Waiting for Gemini video processing")
    print("=" * 60)

    while video_file.state.name == "PROCESSING":

        print(
            "Gemini is processing the video..."
        )

        time.sleep(3)

        video_file = client.files.get(
            name=video_file.name
        )

    print(
        "\nVideo processing completed."
    )

    print(
        "State:",
        video_file.state.name
    )

    if video_file.state.name != "ACTIVE":

        raise RuntimeError(
            "Video processing failed.\n"
            f"Current state: {video_file.state.name}"
        )

    return video_file


# ============================================================
# 10. GEMINI ANALYSIS
# ============================================================

def analyze_video(
    video_file,
    segments
):

    srt_reference = build_srt_reference(
        segments
    )

    segment_count = len(segments)

    prompt = f"""
You are the visual-grounding and content-analysis
agent for an English LLMOps course.

Your task is NOT to translate the course yet.

You have two sources:

SOURCE 1:
The uploaded course video.

SOURCE 2:
The original English SRT subtitle file.

==================================================
SOURCE PRIORITY
==================================================

For spoken content:

Use the SRT subtitles as the PRIMARY source.

For visual information:

Use the video as the PRIMARY source.

Use the video to understand:

- slides
- whiteboard writing
- diagrams
- architecture
- code
- UI
- tables
- technical terms shown on screen
- demonstrations

The SRT timestamps are the authoritative timeline.

==================================================
CRITICAL V3 RULE
==================================================

The ORIGINAL SRT is the MASTER SOURCE.

You MUST NOT recreate or modify the SRT structure.

DO NOT:

- change segment numbers
- merge segments
- split segments
- create segments
- remove segments
- change timestamps
- change subtitle text
- create new timestamps

Python will later restore the original:

- index
- start
- end
- english

from the original SRT.

Your job is ONLY to provide analysis for each segment.

==================================================
MAIN OBJECTIVE
==================================================

For EVERY SRT segment:

1. Identify its segment index.

2. Understand what the instructor is saying.

3. Identify relevant visual information.

4. Identify technical terms.

5. Identify possible speech-to-text errors.

6. Identify content type.

7. Provide useful notes.

Possible content types:

- speech
- code
- command
- technical_term
- diagram
- example
- introduction
- transition
- slide
- demonstration
- mixed
- other

==================================================
SPEECH-TO-TEXT ERRORS
==================================================

The SRT may contain speech-to-text mistakes.

If a technical word appears incorrect and the
video clearly confirms the correction:

put the correction under:

"possible_correction"

Example:

"possible_correction": "Gemini"

If the video does NOT clearly confirm the correction:

DO NOT guess.

Use:

"possible_correction": null

Confidence values:

- HIGH
- MEDIUM
- LOW

==================================================
TECHNICAL TERMINOLOGY
==================================================

Preserve technical terminology.

Examples include:

LLMOps
MLOps
LLM
RAG
LoRA
QLoRA
Fine-tuning
Inference
Deployment
Embedding
Vector Database
API
GPU
FastAPI
LangGraph
MLflow
Hugging Face
AWS Bedrock
Vertex AI
Azure OpenAI
OpenAI API
Gemini
Foundation Model

Do not unnecessarily translate technical terms.

==================================================
CODE
==================================================

If code is visible:

- identify it
- describe its purpose if clear
- preserve it conceptually
- do not modify it
- do not invent missing code

The actual code will NOT be translated in this step.

==================================================
NO HALLUCINATION
==================================================

Never invent:

- instructor statements
- code
- diagrams
- technical concepts
- timestamps
- names
- examples

If something cannot be determined:

use "UNKNOWN".

==================================================
OUTPUT RULES
==================================================

Return ONLY valid JSON.

Return exactly:

{segment_count}

objects.

Every original segment index from:

1

through:

{segment_count}

must appear exactly once.

Do NOT skip any index.

Do NOT duplicate any index.

Do NOT return timestamps.

Do NOT return English subtitle text.

Do NOT return a recreated SRT.

For every segment return:

{{
    "index": 1,
    "visual_context": "",
    "technical_terms": [],
    "content_type": "speech",
    "possible_correction": null,
    "confidence": "HIGH",
    "notes": []
}}

The index must correspond to the provided
[SEGMENT X].

==================================================
ORIGINAL SRT SEGMENTS
==================================================

{srt_reference}

==================================================
FINAL REMINDER
==================================================

Analyze every SRT segment.

Do not translate.

Do not summarize the entire video.

Do not modify the SRT.

The original SRT will be merged with your analysis
by Python after this response.
"""

    print("\n" + "=" * 60)
    print("Sending VIDEO + SRT to Gemini")
    print("=" * 60)

    response = generate_content_with_retry(
        model=MODEL,
        contents=[
            video_file,
            prompt
        ]
    )

    return response.text


# ============================================================
# 11. CLEAN GEMINI JSON
# ============================================================

def clean_json_response(
    response_text
):

    clean_json = response_text.strip()

    # Remove ```json
    if clean_json.startswith(
        "```json"
    ):

        clean_json = clean_json[
            len("```json"):
        ].strip()

    # Remove ```
    if clean_json.startswith(
        "```"
    ):

        clean_json = clean_json[
            len("```"):
        ].strip()

    if clean_json.endswith(
        "```"
    ):

        clean_json = clean_json[
            :-len("```")
        ].strip()

    return clean_json


# ============================================================
# 12. PARSE GEMINI JSON
# ============================================================

def parse_gemini_json(
    response_text
):

    print("\n" + "=" * 60)
    print("Validating Gemini JSON")
    print("=" * 60)

    clean_json = clean_json_response(
        response_text
    )

    try:

        data = json.loads(
            clean_json
        )

    except json.JSONDecodeError as e:

        print(
            "\nGemini returned invalid JSON."
        )

        print(
            "\nRaw response:"
        )

        print(
            response_text
        )

        raise RuntimeError(
            f"JSON validation failed: {e}"
        )

    if not isinstance(data, list):

        raise RuntimeError(
            "Gemini response must be a JSON list."
        )

    print(
        "Gemini JSON syntax is valid."
    )

    return data


# ============================================================
# 13. VALIDATE GEMINI ANALYSIS
# ============================================================

def validate_gemini_analysis(
    gemini_data,
    expected_count
):

    print(
        "\nChecking Gemini segment mapping..."
    )

    indices = []

    for item in gemini_data:

        if not isinstance(
            item,
            dict
        ):

            raise RuntimeError(
                "Gemini returned a non-object segment."
            )

        if "index" not in item:

            raise RuntimeError(
                "Gemini returned a segment without index."
            )

        indices.append(
            item["index"]
        )

    # Duplicate detection
    duplicates = sorted(
        {
            index
            for index in indices
            if indices.count(index) > 1
        }
    )

    if duplicates:

        raise RuntimeError(
            "Duplicate Gemini indices found:\n"
            f"{duplicates}"
        )

    expected_indices = set(
        range(
            1,
            expected_count + 1
        )
    )

    actual_indices = set(
        indices
    )

    missing = sorted(
        expected_indices -
        actual_indices
    )

    extra = sorted(
        actual_indices -
        expected_indices
    )

    if missing:

        raise RuntimeError(
            "Gemini missed subtitle segments:\n"
            f"{missing}"
        )

    if extra:

        raise RuntimeError(
            "Gemini returned invalid segment indices:\n"
            f"{extra}"
        )

    # Exact order
    if indices != list(
        range(
            1,
            expected_count + 1
        )
    ):

        raise RuntimeError(
            "Gemini segment order is incorrect."
        )

    print(
        f"✓ Gemini returned exactly "
        f"{expected_count} segments."
    )

    print(
        "✓ No duplicate indices."
    )

    print(
        "✓ No missing indices."
    )

    print(
        "✓ No extra indices."
    )

    print(
        "✓ Segment order is correct."
    )


# ============================================================
# 14. MERGE ORIGINAL SRT + GEMINI ANALYSIS
# ============================================================

def merge_srt_and_analysis(
    original_segments,
    gemini_data
):

    print("\n" + "=" * 60)
    print("Merging original SRT + Gemini analysis")
    print("=" * 60)

    analysis_by_index = {
        item["index"]: item
        for item in gemini_data
    }

    final_segments = []

    for srt_segment in original_segments:

        index = srt_segment["index"]

        analysis = analysis_by_index.get(
            index
        )

        if analysis is None:

            raise RuntimeError(
                f"No Gemini analysis found "
                f"for segment {index}"
            )

        # ----------------------------------------------------
        # CRITICAL V3 DESIGN
        #
        # These four values ALWAYS come from ORIGINAL SRT.
        #
        # Gemini cannot change them.
        # ----------------------------------------------------

        final_segment = {

            "index":
                srt_segment["index"],

            "start":
                srt_segment["start"],

            "end":
                srt_segment["end"],

            "english":
                srt_segment["english"],

            # Gemini analysis
            "visual_context":
                analysis.get(
                    "visual_context",
                    ""
                ),

            "technical_terms":
                analysis.get(
                    "technical_terms",
                    []
                ),

            "content_type":
                analysis.get(
                    "content_type",
                    "speech"
                ),

            "possible_correction":
                analysis.get(
                    "possible_correction"
                ),

            "confidence":
                analysis.get(
                    "confidence",
                    "LOW"
                ),

            "notes":
                analysis.get(
                    "notes",
                    []
                )
        }

        final_segments.append(
            final_segment
        )

    return final_segments


# ============================================================
# 15. FINAL STRUCTURAL VALIDATION
# ============================================================

def validate_final_output(
    original_segments,
    final_segments
):

    print("\n" + "=" * 60)
    print("FINAL OUTPUT VALIDATION")
    print("=" * 60)

    # --------------------------------------------------------
    # Count
    # --------------------------------------------------------

    original_count = len(
        original_segments
    )

    final_count = len(
        final_segments
    )

    print(
        f"Original SRT segments : {original_count}"
    )

    print(
        f"Final JSON segments   : {final_count}"
    )

    if original_count != final_count:

        raise RuntimeError(
            "Segment count mismatch!"
        )

    print(
        "✓ Segment count matches."
    )

    # --------------------------------------------------------
    # Index
    # --------------------------------------------------------

    for original, final in zip(
        original_segments,
        final_segments
    ):

        if (
            original["index"]
            !=
            final["index"]
        ):

            raise RuntimeError(
                f"Index changed for segment "
                f"{original['index']}"
            )

    print(
        "✓ Segment indices match."
    )

    # --------------------------------------------------------
    # Start timestamp
    # --------------------------------------------------------

    for original, final in zip(
        original_segments,
        final_segments
    ):

        if (
            original["start"]
            !=
            final["start"]
        ):

            raise RuntimeError(
                f"Start timestamp changed "
                f"for segment "
                f"{original['index']}"
            )

    print(
        "✓ Start timestamps unchanged."
    )

    # --------------------------------------------------------
    # End timestamp
    # --------------------------------------------------------

    for original, final in zip(
        original_segments,
        final_segments
    ):

        if (
            original["end"]
            !=
            final["end"]
        ):

            raise RuntimeError(
                f"End timestamp changed "
                f"for segment "
                f"{original['index']}"
            )

    print(
        "✓ End timestamps unchanged."
    )

    # --------------------------------------------------------
    # English subtitle
    # --------------------------------------------------------

    for original, final in zip(
        original_segments,
        final_segments
    ):

        if (
            original["english"]
            !=
            final["english"]
        ):

            raise RuntimeError(
                f"English subtitle changed "
                f"for segment "
                f"{original['index']}"
            )

    print(
        "✓ English subtitle text unchanged."
    )

    # --------------------------------------------------------
    # Final sequence
    # --------------------------------------------------------

    final_indices = [
        segment["index"]
        for segment in final_segments
    ]

    expected_indices = list(
        range(
            1,
            original_count + 1
        )
    )

    if final_indices != expected_indices:

        raise RuntimeError(
            "Final segment sequence is invalid."
        )

    print(
        "✓ Final sequence is correct."
    )

    print("\n" + "=" * 60)
    print("FINAL OUTPUT IS STRUCTURALLY SAFE")
    print("=" * 60)


# ============================================================
# 16. SAVE FINAL ANALYSIS
# ============================================================

def save_final_output(
    final_segments
):

    final_data = {

        "course_title":
            "What is LLMOps",

        "total_segments":
            len(final_segments),

        "segments":
            final_segments
    }

    with open(
        ANALYSIS_OUTPUT,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            final_data,
            file,
            ensure_ascii=False,
            indent=4
        )

    print(
        "\nAnalysis saved:"
    )

    print(
        ANALYSIS_OUTPUT
    )


# ============================================================
# 17. MAIN
# ============================================================

def main():

    print("=" * 60)
    print("LLMOps Telugu Translation Agent")
    print("VERSION 3")
    print("=" * 60)

    # --------------------------------------------------------
    # Validate input files
    # --------------------------------------------------------

    print("\nChecking input files...")

    if not VIDEO_PATH.exists():

        raise FileNotFoundError(
            f"Video file not found:\n"
            f"{VIDEO_PATH}"
        )

    if not SUBTITLE_PATH.exists():

        raise FileNotFoundError(
            f"Subtitle file not found:\n"
            f"{SUBTITLE_PATH}"
        )

    print(
        "\nVideo found:"
    )

    print(
        VIDEO_PATH
    )

    print(
        "\nSubtitle found:"
    )

    print(
        SUBTITLE_PATH
    )

    # --------------------------------------------------------
    # Read and validate original SRT
    # --------------------------------------------------------

    original_segments = parse_srt(
        SUBTITLE_PATH
    )

    validate_original_srt(
        original_segments
    )

    # --------------------------------------------------------
    # Upload video
    # --------------------------------------------------------

    video_file = upload_video(
        VIDEO_PATH
    )

    # --------------------------------------------------------
    # Wait for processing
    # --------------------------------------------------------

    video_file = wait_for_video_processing(
        video_file
    )

    # --------------------------------------------------------
    # Gemini analysis
    # --------------------------------------------------------

    response_text = analyze_video(
        video_file,
        original_segments
    )

    print("\n" + "=" * 60)
    print("Gemini analysis received")
    print("=" * 60)

    print(
        response_text
    )

    # --------------------------------------------------------
    # Parse Gemini JSON
    # --------------------------------------------------------

    gemini_data = parse_gemini_json(
        response_text
    )

    # --------------------------------------------------------
    # Validate Gemini mapping
    # --------------------------------------------------------

    validate_gemini_analysis(
        gemini_data,
        len(original_segments)
    )

    # --------------------------------------------------------
    # Merge
    # --------------------------------------------------------

    final_segments = merge_srt_and_analysis(
        original_segments,
        gemini_data
    )

    # --------------------------------------------------------
    # Final validation
    # --------------------------------------------------------

    validate_final_output(
        original_segments,
        final_segments
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_final_output(
        final_segments
    )

    # --------------------------------------------------------
    # Done
    # --------------------------------------------------------

    print("\n" + "=" * 60)
    print("STEP 1 COMPLETED — V3")
    print("=" * 60)

    print(
        """
VIDEO + ORIGINAL SRT
        ↓
Gemini Visual Grounding
        ↓
Gemini Segment Analysis
        ↓
Python SRT Master Merge
        ↓
Structural Validation
        ↓
01_llmops_analysis.json
        ↓
READY FOR TRANSLATION
"""
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()