# Stem dogfood artifacts

## `stem_vocal_house.wav` (preferred listen)

~45s deep-house with **real sung vocals** via ElevenLabs Music, then scored by
the listen/review harness (`review_audio`).

- Review JSON: `stem_vocal_house.review.json`
- Rebuild (key via env only — never commit secrets):

```bash
export ELEVENLABS_API_KEY=...   # do not put this in git
python scripts/generate_and_review_song.py
```

The script regenerates with an improved prompt if the first pass gets
`revise`/`fail`.

## `stem_f_minor_house.wav`

24s offline synth sketch (no API): StemScript + arrange →
`scripts/render_mock_song.py`.
