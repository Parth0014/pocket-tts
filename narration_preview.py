"""Prepare local reference auditions and compare short narration profiles.

The reference-only workflow deliberately does not import PocketTTS or Torch.
All audio and the review page stay in an isolated local session directory.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

BASE_DIR = Path(__file__).resolve().parent
PROFILES = ("faithful", "balanced", "expressive", "legacy")
PROFILE_DESCRIPTIONS = {
    "faithful": "A conservative starting point for voice likeness and stable delivery.",
    "balanced": "A starting point for natural narration with room for variation.",
    "expressive": "An experiment with more variation; listen for changes in voice and word accuracy.",
    "legacy": "The earlier generation settings, included for a direct listening comparison.",
}


def _reference_tools():
    from reference_audio import export_candidate_previews, prepare_reference

    return prepare_reference, export_candidate_previews


def _generation_tools():
    import generate_narration

    return generate_narration


def _escape(value):
    return html.escape(str(value), quote=True)


def _asset_url(path, session_dir):
    """Only link to files inside this session; encode reserved URL characters."""
    relative = Path(path).resolve().relative_to(Path(session_dir).resolve())
    return _escape(quote(relative.as_posix(), safe="/"))


def _write_json(path, data):
    temporary = Path(path).with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _command(arguments, *, windows=None):
    """Produce a copyable command for the shell used on this platform."""
    if windows is None:
        windows = os.name == "nt"
    arguments = [os.fspath(argument) for argument in arguments]
    if windows:
        return "& " + " ".join("'" + argument.replace("'", "''") + "'" for argument in arguments)
    return shlex.join(arguments)


def build_excerpt(source_html, *, word_budget):
    """Use the normal production extractor and retain one fixed, bounded excerpt.

    Prefer stopping at an existing sentence ending. No emotion instructions or
    invented words are added to the spoken text. All profiles receive this file.
    """
    from worker_document import extract_worker_blocks

    if word_budget < 1:
        raise ValueError("word_budget must be positive")
    blocks = extract_worker_blocks(source_html)
    selected = []
    remaining = word_budget
    for block in blocks:
        text = str(block.get("text", "")).strip()
        if not text or remaining <= 0:
            continue
        words = list(re.finditer(r"\S+", text))
        if len(words) > remaining:
            text = text[:words[remaining - 1].end()]
            endings = list(re.finditer(r"[.!?][\"'\u2019\u201d)]*(?=\s|$)", text))
            # Avoid throwing away nearly the whole excerpt for an early period.
            if endings and endings[-1].end() >= len(text) * 0.45:
                text = text[:endings[-1].end()]
            selected.append({**block, "text": text})
            break
        selected.append({**block, "text": text})
        remaining -= len(words)

    if not selected:
        raise ValueError("The source HTML contains no narratable text")
    markup = []
    for block in selected:
        content = _escape(block["text"])
        if block.get("type") == "quote":
            attribution = ""
            if block.get("speaker"):
                attribution = f"<p>\u2014 {_escape(block['speaker'])}</p>"
            markup.append(f"<blockquote><p>{content}</p>{attribution}</blockquote>")
        elif block.get("type") == "heading":
            markup.append(f"<h2>{content}</h2>")
        else:
            markup.append(f"<p>{content}</p>")
    return "\n".join(markup), "\n\n".join(block["text"] for block in selected)


def _audio_card(title, path, session_dir, description="", extra=""):
    url = _asset_url(path, session_dir)
    return (
        '<article class="card">'
        f"<h3>{_escape(title)}</h3><p>{_escape(description)}</p>"
        f'<audio controls preload="none" aria-label="{_escape(title)}" src="{url}"></audio>'
        f'<p><a href="{url}" download>Download WAV</a></p>{extra}</article>'
    )


def _render_page(manifest, session_dir):
    reference = manifest["reference"]
    start = float(reference.get("selected_start_seconds", 0))
    duration = float(reference.get("duration_seconds", 0))
    channel = reference.get("selected_channel")
    description = f"Selected {start:.2f}s to {start + duration:.2f}s of your recording."
    if channel is not None:
        description += f" Channel {int(channel) + 1}."
    reference_cards = [_audio_card(
        "Prepared reference", reference["anchor_path"], session_dir, description,
    )]
    quote_reference = manifest.get("quote_reference")
    if quote_reference:
        reference_cards.append(_audio_card(
            "Prepared quote reference", quote_reference["anchor_path"], session_dir,
            "This separate reference is used for quotes in two-voice mode.",
        ))
    for index, candidate in enumerate(manifest.get("reference_candidates", []), start=1):
        candidate_start = float(candidate.get("start_seconds", 0))
        candidate_end = float(candidate.get("end_seconds", candidate_start))
        extra = ""
        command = candidate.get("select_command")
        if command:
            extra = f"<details><summary>Use this segment</summary><pre>{_escape(command)}</pre></details>"
        reference_cards.append(_audio_card(
            f"Reference alternative {index}", candidate["audio_path"], session_dir,
            f"{candidate_start:.2f}s to {candidate_end:.2f}s of your recording.", extra,
        ))

    warnings = list(reference.get("warnings", [])) + list(manifest.get("warnings", []))
    if quote_reference:
        warnings.extend(quote_reference.get("warnings", []))
    warning_markup = ""
    if warnings:
        warning_markup = '<aside class="notice"><h2>Recording notes</h2><ul>' + "".join(
            f"<li>{_escape(warning)}</li>" for warning in dict.fromkeys(str(item) for item in warnings)
        ) + "</ul></aside>"

    profiles = []
    for result in manifest.get("results", []):
        name = result["profile"]
        if result["status"] != "complete":
            profiles.append(
                f'<article class="card"><h3>{_escape(name.capitalize())}</h3>'
                f'<p class="notice">{_escape(result.get("error", "Preview pending"))}</p></article>'
            )
            continue
        extra = ""
        if result.get("transcript"):
            extra += (
                "<details><summary>Words sent to the model</summary>"
                f'<p class="transcript">{_escape(result["transcript"])}</p></details>'
            )
        if result.get("warnings"):
            extra += '<ul class="notes">' + "".join(
                f"<li>{_escape(item)}</li>" for item in result["warnings"]
            ) + "</ul>"
        extra += (
            "<details><summary>Generate the full narration with this profile</summary>"
            f'<pre>{_escape(result["full_generation_command"])}</pre></details>'
        )
        profiles.append(_audio_card(
            name.capitalize(), result["audio_path"], session_dir, PROFILE_DESCRIPTIONS[name], extra,
        ))

    generation_markup = ""
    if profiles:
        generation_markup = (
            '<section><h2>Compare narration</h2><p>Each profile reads the same excerpt '
            f'with seed {_escape(manifest["seed"])}. Listen at the same volume. '
            'The profiles are listening experiments, not measured quality rankings.</p>'
            '<div class="grid">' + "".join(profiles) + "</div></section>"
        )
    elif manifest["prepare_only"]:
        generation_markup = (
            '<section class="notice"><h2>Reference ready</h2><p>No narration model was loaded. '
            'Listen to the selected segment and alternatives before generating.</p>'
            '<details open><summary>Compare narration using this reference</summary>'
            f'<pre>{_escape(manifest["preview_command"])}</pre></details></section>'
        )

    excerpt_markup = ""
    if manifest.get("excerpt_text"):
        excerpt_markup = (
            '<details><summary>Source excerpt</summary><p class="transcript">'
            f'{_escape(manifest["excerpt_text"])}</p></details>'
        )

    page = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; media-src 'self' file:; base-uri 'none'; form-action 'none'">
<title>Local voice audition</title>
<style>
:root{color-scheme:light;--ink:#202b33;--muted:#53616b;--line:#d8e2df;--accent:#145e4c}
*{box-sizing:border-box}body{margin:0;background:#f3f6f3;color:var(--ink);font:16px/1.6 system-ui,sans-serif}
main{max-width:1240px;margin:auto;padding:38px 24px 64px}h1{font-size:clamp(2rem,4vw,3rem);line-height:1.15;margin:10px 0}
h2{font-size:1.45rem;margin:0 0 12px}h3{font-size:1.1rem;margin:0 0 6px}p{margin:8px 0 16px;color:var(--muted)}
.eyebrow{letter-spacing:.12em;text-transform:uppercase;font-size:.75rem;font-weight:700;color:var(--accent)}
section{margin:32px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,285px),1fr));gap:18px}
.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px;min-width:0}.card p{font-size:.94rem}
audio{width:100%;display:block;margin:20px 0 8px}a{color:var(--accent);text-underline-offset:3px}
.notice{border-left:4px solid #b48b37;background:#fffbef;padding:18px 22px;border-radius:8px;margin:22px 0}
.notice h2{font-size:1.1rem}.guidance{background:#e4efea;border-radius:16px;padding:22px 28px}.guidance li{margin:8px 0}
details{margin:16px 0}summary{cursor:pointer;font-weight:600;color:var(--accent)}pre{overflow-x:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#edf1f2;padding:14px;border-radius:8px;font-size:.8rem}
.transcript{white-space:pre-wrap}.notes{padding-left:20px;font-size:.9rem}footer{font-size:.84rem;border-top:1px solid var(--line);padding-top:18px;color:var(--muted)}
</style></head><body><main><header><span class="eyebrow">PocketTTS / local listening room</span>
<h1>Find a voice that tells the story.</h1><p>Start with a clear reference, then compare a short passage before spending time on a full narration.</p></header>
'''
    page += warning_markup
    page += '<section><h2>Listen to the reference</h2><p>Choose clean speech from one speaker with the delivery you want. Automatic selection considers recording quality; it cannot judge acting, speaker identity, or emotional fit.</p><div class="grid">'
    page += "".join(reference_cards) + "</div></section>"
    page += generation_markup
    page += '''<section class="guidance"><h2>What to listen for</h2><ol>
<li><strong>Voice likeness:</strong> compare accent, vocal texture, and speaking habits with the reference.</li>
<li><strong>Word accuracy:</strong> follow the transcript for omissions, repeats, pronunciation mistakes, or added sounds.</li>
<li><strong>Natural emotion:</strong> listen for emphasis that follows the meaning, and believable changes between reflection, tension, and dialogue.</li>
<li><strong>Pacing:</strong> check sentence flow, breathing room, and joins between chunks. A slower voice does not necessarily tell a better story.</li>
</ol><p>If none sounds convincing, try another reference segment or recording before choosing a profile. PocketTTS cannot guarantee directed acting from these settings.</p></section>'''
    page += excerpt_markup
    page += '<footer>All files remain local. <a href="manifest.json" download>Download session details</a></footer></main></body></html>'
    (Path(session_dir) / "index.html").write_text(page, encoding="utf-8")


def _full_command(manifest, profile, session_dir):
    arguments = [
        sys.executable, BASE_DIR / "generate_narration.py",
        "--html", manifest["source_html"],
        "--reference", manifest["reference"]["anchor_path"],
        "--profile", profile, "--seed", str(manifest["seed"]),
        "--output-dir", Path(session_dir) / "full" / profile,
    ]
    if manifest["quote_mode"] != "preserve":
        arguments += ["--quote-mode", manifest["quote_mode"]]
    if manifest.get("quote_reference"):
        arguments += ["--quote-reference", manifest["quote_reference"]["anchor_path"]]
    return _command(arguments)


def create_audition_session(
    reference_path, output_root, *, html_path=None,
    profiles=("faithful", "balanced", "expressive"), seed=1234,
    reference_start_seconds=None, preview_words=90, max_chunks=None, prepare_only=False,
    quote_reference_path=None, quote_mode="preserve", quote_reference_start_seconds=None,
):
    """Write one portable review folder, returning its manifest and directory."""
    reference_path = Path(reference_path).expanduser().resolve()
    if not reference_path.is_file():
        raise FileNotFoundError(f"Reference audio does not exist: {reference_path}")
    if max_chunks is not None and (
        not isinstance(max_chunks, int) or isinstance(max_chunks, bool) or not 1 <= max_chunks <= 20
    ):
        raise ValueError("max_chunks must be an integer between 1 and 20")
    if not isinstance(preview_words, int) or isinstance(preview_words, bool) or not 1 <= preview_words <= 600:
        raise ValueError("preview_words must be an integer between 1 and 600")
    profiles = list(dict.fromkeys(profiles))
    if not profiles or any(profile not in PROFILES for profile in profiles):
        raise ValueError(f"Choose profiles from: {', '.join(PROFILES)}")
    if quote_mode not in {"preserve", "two_voice", "exclude"}:
        raise ValueError("quote_mode must be preserve, two_voice, or exclude")
    for label, value in (
        ("reference_start_seconds", reference_start_seconds),
        ("quote_reference_start_seconds", quote_reference_start_seconds),
    ):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError(f"{label} must be a finite, non-negative number")
    if quote_reference_path:
        quote_reference_path = Path(quote_reference_path).expanduser().resolve()
        if not quote_reference_path.is_file():
            raise FileNotFoundError(f"Quote reference does not exist: {quote_reference_path}")
    if html_path is not None:
        html_path = Path(html_path).expanduser().resolve()
    excerpt_html, excerpt_text = None, None
    if not prepare_only:
        if html_path is None or not html_path.is_file():
            raise FileNotFoundError(f"Narration HTML does not exist: {html_path}")
        excerpt_html, excerpt_text = build_excerpt(
            html_path.read_text(encoding="utf-8-sig"), word_budget=preview_words,
        )

    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_dir = Path(tempfile.mkdtemp(prefix=f"audition_{stamp}_", dir=output_root))
    reference_dir = session_dir / "reference"
    reference_dir.mkdir()
    prepare_reference, export_candidates = _reference_tools()
    reference = prepare_reference(
        reference_path, reference_dir / "selected.wav", start_seconds=reference_start_seconds,
    )
    candidates = export_candidates(reference_path, reference, reference_dir / "alternatives", limit=3)
    manifest = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_reference": str(reference_path),
        "source_html": str(html_path) if html_path is not None else None,
        "prepare_only": bool(prepare_only),
        "profiles": profiles,
        "seed": int(seed),
        "excerpt_word_budget": preview_words,
        "max_chunks": max_chunks,
        "excerpt_text": excerpt_text,
        "quote_mode": quote_mode,
        "reference": reference,
        "reference_candidates": candidates,
        "results": [],
        "warnings": [],
    }
    if quote_reference_path:
        manifest["quote_reference"] = prepare_reference(
            quote_reference_path, reference_dir / "quote_selected.wav",
            start_seconds=quote_reference_start_seconds,
        )
    preview_arguments = [
        sys.executable, BASE_DIR / "narration_preview.py", "--reference", reference["anchor_path"],
        "--html", html_path if html_path is not None else BASE_DIR / "sample.html",
        "--output-dir", output_root, "--seed", str(seed), "--preview-words", str(preview_words),
        "--profiles", *profiles,
    ]
    if max_chunks is not None:
        preview_arguments += ["--max-chunks", str(max_chunks)]
        manifest["warnings"].append(
            "A chunk limit was requested. Profiles may stop at different words; compare their generated transcripts."
        )
    if quote_mode != "preserve":
        preview_arguments += ["--quote-mode", quote_mode]
    if manifest.get("quote_reference"):
        preview_arguments += ["--quote-reference", manifest["quote_reference"]["anchor_path"]]
    manifest["preview_command"] = _command(preview_arguments)
    for candidate in candidates:
        # Use the actual exported clip so the audition retains its selected channel.
        arguments = list(preview_arguments)
        arguments[arguments.index("--reference") + 1] = candidate["audio_path"]
        if prepare_only:
            arguments.append("--prepare-only")
        candidate["select_command"] = _command(arguments)

    def persist():
        _write_json(session_dir / "manifest.json", manifest)
        _render_page(manifest, session_dir)

    if prepare_only:
        persist()
        return manifest, session_dir

    excerpt_path = session_dir / "excerpt.html"
    excerpt_path.write_text(excerpt_html, encoding="utf-8")
    manifest["excerpt_html"] = str(excerpt_path)
    persist()
    generator = _generation_tools()
    for profile in profiles:
        result = {"profile": profile, "status": "running"}
        manifest["results"].append(result)
        persist()
        print(f"\nAudition profile: {profile}")
        try:
            audio_path = generator.run_pipeline(
                post_html_file=str(excerpt_path), narration_reference_audio=reference["anchor_path"],
                quote_reference_audio=(manifest.get("quote_reference") or {}).get("anchor_path"),
                quote_mode=quote_mode, output_dir=str(session_dir / profile),
                profile=profile, seed=seed, max_chunks=max_chunks,
            )
            audio_path = Path(audio_path).resolve()
            _asset_url(audio_path, session_dir)
            if not audio_path.is_file():
                raise FileNotFoundError(f"Generation did not produce an audio file: {audio_path}")
            result.update({
                "status": "complete", "audio_path": str(audio_path),
                "full_generation_command": _full_command(manifest, profile, session_dir),
            })
            report_path = audio_path.with_suffix(".json")
            if report_path.is_file():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    result["transcript"] = "\n\n".join(
                        str(chunk["text"]) for chunk in report.get("chunks", []) if chunk.get("text")
                    )
                    result["warnings"] = report.get("warnings", [])
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    result["warnings"] = [f"Could not read the generation report: {exc}"]
        except Exception as exc:
            result.update({"status": "error", "error": str(exc)})
            print(f"  Preview failed: {exc}", file=sys.stderr)
        persist()
    return manifest, session_dir


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", help="Raw reference recording; generation can also use .env defaults")
    parser.add_argument("--html", help="Source HTML; defaults to the configured narration HTML")
    parser.add_argument("--output-dir", default=str(BASE_DIR / "output" / "auditions"))
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=list(PROFILES[:3]))
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--reference-start", "--start", dest="reference_start", type=float)
    parser.add_argument(
        "--preview-words", type=int, default=90,
        help="Maximum source words in the shared preview excerpt (1-600, default: 90)",
    )
    parser.add_argument(
        "--max-chunks", type=int,
        help="Optional generation chunk cap (1-20); may stop at different words across profiles",
    )
    parser.add_argument("--prepare-only", action="store_true", help="Prepare and audition references without loading a TTS model")
    parser.add_argument("--quote-reference")
    parser.add_argument("--quote-mode", choices=("preserve", "two_voice", "exclude"))
    parser.add_argument("--quote-reference-start", type=float)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.prepare_only and not args.reference:
        parser.error("--prepare-only requires --reference; this mode does not load the narration runtime")
    config = {}
    if not args.prepare_only:
        generator = _generation_tools()
        config = generator.build_runtime_config(
            str(BASE_DIR), generator.load_runtime_environment(str(BASE_DIR)),
        )
    try:
        manifest, session_dir = create_audition_session(
            args.reference or config.get("narration_reference_audio"), args.output_dir,
            html_path=args.html or config.get("post_html_file"),
            profiles=args.profiles, seed=args.seed, reference_start_seconds=args.reference_start,
            preview_words=args.preview_words, max_chunks=args.max_chunks, prepare_only=args.prepare_only,
            quote_reference_path=args.quote_reference or config.get("quote_reference_audio"),
            quote_mode=args.quote_mode or config.get("quote_mode", "preserve"),
            quote_reference_start_seconds=args.quote_reference_start,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Audition could not start: {exc}", file=sys.stderr)
        return 1
    print(f"\nOpen this local review page in your browser:\n{session_dir / 'index.html'}")
    print(f"Session details: {session_dir / 'manifest.json'}")
    return 1 if any(result["status"] == "error" for result in manifest["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
