# Stem dogfood artifacts

## Preferred listen: `stem_vocal_house.wav`

**Pipeline (correct):**

1. **Stem** builds the instrumental (`house_beat.stem` + `arrange_loop_to_song` → synth render) → `stem_instrumental_house.wav`
2. **ElevenLabs** supplies an **isolated vocal stem only** → `stem_vocals_only.wav`
3. Mix → `stem_vocal_house.wav`
4. Listen harness → `stem_vocal_house.review.json`

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
