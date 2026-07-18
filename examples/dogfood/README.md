# Stem dogfood artifacts

## `stem_f_minor_house.wav`

24s F-minor house sketch rendered **offline** from Stem’s own stack:

1. `house_beat.stem` (StemScript → chords/bass/drums/lead)
2. `arrange_loop_to_song` (intro / verse / chorus markers + repeats)
3. `scripts/render_mock_song.py` (simple synth render — no API keys)

Play it in any audio player. Rebuild:

```bash
python scripts/render_mock_song.py
```

For higher-quality vocals/full mixes on your machine, set `ELEVENLABS_API_KEY`
(or ACE-Step) and use `generate_song` / the CLI against live Ardour.
