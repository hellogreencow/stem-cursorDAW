# Stem dogfood artifacts

## Product rule

**Stem produces the song.** Style, chords, progression — Stem. Vocals are an
optional overlay after the bed is good. Never treat a full external song+vocals
API mix as the product.

## Latest: `stem_now_song.wav`

Made with the enforced pipeline: `produce_instrumental` (A minor Andalusian @ 122) → `judge_session` → Stem synth bed → isolated vocal overlay → mix.

- Hygiene review: `stem_now_song.review.json` (**pass / 80.5**)
- Music jury: `stem_now_song.jury.json` (rhythm + critics; MIDI can pass while the synth still sounds sketchy — that split is intentional)

Also: `stem_now_instrumental.wav`, `stem_now_vocals.wav`. Rebuild: `python scripts/make_song_now.py`.

## Preferred listen: `stem_vocal_house.wav`

**Pipeline (correct):**

1. **Stem** builds the instrumental (`produce_instrumental` / StemScript /
   `arrange_loop_to_song` → synth render) → `stem_instrumental_house.wav`
2. Analyze / get comfortable with the bed
3. **ElevenLabs** supplies an **isolated vocal stem only** (`overlay_vocals`) →
   `stem_vocals_only.wav`
4. Mix → `stem_vocal_house.wav`
5. Listen harness → `stem_vocal_house.review.json`

This is **not** a full ElevenLabs song replacing Stem.

Rebuild:

```bash
export ELEVENLABS_API_KEY=...   # never commit
python scripts/stem_track_plus_vocals.py
```

## Also present

- `stem_instrumental_house.wav` — Stem bed alone
- `stem_vocals_only.wav` — vocal stem alone
- `stem_f_minor_house.wav` — earlier Stem-only synth sketch (no vocals)
