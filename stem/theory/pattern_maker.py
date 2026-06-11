"""
Pattern maker for drums, bass, melodies, and chords
"""
from typing import List, Dict, Optional, Tuple
import numpy as np
from .notes import Note, Chord, ChordType, Scale, ScaleType, ChordProgression


class DrumPattern:
    """Drum pattern sequencer"""
    
    # Standard drum map
    DRUM_MAP = {
        "kick": 36,
        "snare": 38,
        "clap": 39,
        "hihat_closed": 42,
        "hihat_open": 46,
        "tom_low": 41,
        "tom_mid": 47,
        "tom_high": 50,
        "crash": 49,
        "ride": 51,
        "rim": 37,
        "shaker": 82,
        "conga": 63,
        "cowbell": 56,
    }
    
    # Pre-made patterns (16 steps = 1 bar)
    PATTERNS = {
        "four_on_floor": {
            "kick": [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
            "hihat_closed": [0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0],
            "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
        },
        "hip_hop": {
            "kick": [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0],
            "hihat_closed": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
            "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
        },
        "trap": {
            "kick": [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "hihat_closed": [1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 0],
            "snare": [0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0],
        },
        "breakbeat": {
            "kick": [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0],
            "snare": [0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0],
            "hihat_closed": [0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0],
        },
        "dnb": {
            "kick": [1, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0],
            "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
            "hihat_closed": [0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0],
        },
        "house": {
            "kick": [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
            "hihat_open": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
            "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
        },
    }
    
    def __init__(self, name: str = "Custom", bpm: float = 120):
        self.name = name
        self.bpm = bpm
        self.steps = 16
        self.tracks: Dict[str, List[int]] = {}
        self.velocities: Dict[str, List[int]] = {}
        
    @classmethod
    def from_preset(cls, preset_name: str, bpm: float = 120) -> 'DrumPattern':
        """Create pattern from preset"""
        pattern = cls(preset_name, bpm)
        
        if preset_name in cls.PATTERNS:
            for drum, steps in cls.PATTERNS[preset_name].items():
                pattern.tracks[drum] = steps.copy()
                pattern.velocities[drum] = [100 if s else 0 for s in steps]
        else:
            # Default empty pattern
            for drum in cls.DRUM_MAP.keys():
                pattern.tracks[drum] = [0] * 16
                pattern.velocities[drum] = [0] * 16
                
        return pattern
        
    def set_step(self, drum: str, step: int, velocity: int = 100):
        """Set a step in the pattern"""
        if drum not in self.tracks:
            self.tracks[drum] = [0] * self.steps
            self.velocities[drum] = [0] * self.steps
            
        self.tracks[drum][step % self.steps] = 1 if velocity > 0 else 0
        self.velocities[drum][step % self.steps] = velocity
        
    def clear(self):
        """Clear all steps"""
        for drum in self.tracks:
            self.tracks[drum] = [0] * self.steps
            self.velocities[drum] = [0] * self.steps
            
    def randomize(self, density: float = 0.3):
        """Generate random pattern"""
        for drum in self.DRUM_MAP.keys():
            if drum not in self.tracks:
                self.tracks[drum] = [0] * self.steps
                self.velocities[drum] = [0] * self.steps
                
            for i in range(self.steps):
                if np.random.random() < density:
                    self.tracks[drum][i] = 1
                    self.velocities[drum][i] = int(np.random.randint(60, 127))
                else:
                    self.tracks[drum][i] = 0
                    self.velocities[drum][i] = 0
                    
    def generate_variation(self, variation_amount: float = 0.2) -> 'DrumPattern':
        """Create a variation of this pattern"""
        new_pattern = DrumPattern(f"{self.name} Variation", self.bpm)
        
        for drum, steps in self.tracks.items():
            new_pattern.tracks[drum] = steps.copy()
            new_pattern.velocities[drum] = self.velocities[drum].copy()
            
            # Randomly modify some steps
            for i in range(len(steps)):
                if np.random.random() < variation_amount:
                    new_pattern.tracks[drum][i] = 1 - new_pattern.tracks[drum][i]
                    if new_pattern.tracks[drum][i]:
                        new_pattern.velocities[drum][i] = np.random.randint(60, 127)
                        
        return new_pattern
        
    def to_midi_notes(self, bars: int = 1) -> List[Note]:
        """Convert pattern to MIDI notes"""
        notes = []
        beat_duration = 60.0 / self.bpm
        step_duration = beat_duration / 4  # 16th notes
        
        for bar in range(bars):
            bar_offset = bar * self.steps * step_duration
            
            for drum, steps in self.tracks.items():
                if drum not in self.DRUM_MAP:
                    continue
                    
                midi_note = self.DRUM_MAP[drum]
                
                for step, active in enumerate(steps):
                    if active:
                        velocity = self.velocities.get(drum, [100] * self.steps)[step]
                        note = Note(
                            midi_note,
                            velocity=velocity,
                            start_time=bar_offset + step * step_duration,
                            duration=step_duration * 0.8
                        )
                        notes.append(note)
                        
        return notes
        
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "bpm": self.bpm,
            "steps": self.steps,
            "tracks": self.tracks,
            "velocities": self.velocities
        }


class BassPattern:
    """Bass line pattern generator"""
    
    PATTERNS = {
        "walking": [1, 0, 1, 0, 1, 0, 1, 0],  # Steady quarter notes
        "pulse": [1, 0, 0, 0, 1, 0, 0, 0],    # On the beat
        "syncopated": [0, 0, 1, 0, 0, 1, 0, 1],  # Off-beat
        "bounce": [1, 0, 0, 1, 0, 0, 1, 0],   # Bouncy rhythm
        "driving": [1, 1, 0, 1, 0, 1, 1, 0],  # Driving rhythm
    }
    
    def __init__(self, scale: Scale, bpm: float = 120):
        self.scale = scale
        self.bpm = bpm
        self.notes: List[Tuple[int, float, float]] = []  # (degree, time, duration)
        
    @classmethod
    def from_chord_progression(cls, progression, pattern_type: str = "walking", 
                               bpm: float = 120) -> 'BassPattern':
        """Generate bass line from chord progression"""
        bass = cls(progression.scale, bpm)
        
        pattern = cls.PATTERNS.get(pattern_type, cls.PATTERNS["walking"])
        beat_duration = 60.0 / bpm
        
        current_time = 0.0
        for chord, chord_duration in progression.chords:
            bar_duration = chord_duration * 4 * beat_duration  # 4 beats per bar
            step_duration = bar_duration / len(pattern)
            
            for i, active in enumerate(pattern):
                if active:
                    # Use root note
                    degree = 1
                    note = progression.get_chord_for_degree(degree, ChordType.MAJOR)
                    
                    bass.notes.append((
                        note.root_note,
                        current_time + i * step_duration,
                        step_duration * 0.9
                    ))
                    
            current_time += bar_duration
            
        return bass
        
    def generate_melodic(self, complexity: float = 0.5) -> List[Note]:
        """Generate melodic bass line"""
        notes = []
        
        # Get scale notes
        scale_notes = self.scale.intervals
        
        for root_note, start_time, duration in self.notes:
            # Randomly choose from scale
            if np.random.random() < complexity:
                interval = np.random.choice(scale_notes)
                note = Note(
                    (root_note + interval + 36) % 128,  # Bass octave
                    velocity=np.random.randint(80, 110),
                    start_time=start_time,
                    duration=duration
                )
            else:
                note = Note(
                    (root_note + 36) % 128,  # Root note
                    velocity=100,
                    start_time=start_time,
                    duration=duration
                )
                
            notes.append(note)
            
        return notes
        
    def to_midi_notes(self) -> List[Note]:
        """Convert to MIDI notes"""
        return [
            Note(
                (root + 36) % 128,
                velocity=100,
                start_time=start,
                duration=dur
            )
            for root, start, dur in self.notes
        ]


class MelodyPattern:
    """Melody pattern generator"""
    
    def __init__(self, scale: Scale, bpm: float = 120):
        self.scale = scale
        self.bpm = bpm
        self.notes: List[Note] = []
        
    def generate(self, bars: int = 4, density: float = 0.5, 
                octave: int = 5) -> List[Note]:
        """Generate random melody"""
        notes = []
        beat_duration = 60.0 / self.bpm
        
        scale_notes = self.scale.intervals
        base_note = (octave + 1) * 12 + self.scale.root_note
        
        current_time = 0.0
        
        for bar in range(bars):
            for beat in range(4):
                if np.random.random() < density:
                    # Choose random scale degree
                    interval = np.random.choice(scale_notes)
                    midi_note = base_note + interval
                    
                    # Randomize velocity for human feel
                    velocity = np.random.randint(60, 120)
                    
                    # Random duration
                    duration_options = [beat_duration * 0.25, beat_duration * 0.5, 
                                      beat_duration, beat_duration * 2]
                    duration = np.random.choice(duration_options)
                    
                    note = Note(
                        midi_note,
                        velocity=velocity,
                        start_time=current_time,
                        duration=duration
                    )
                    notes.append(note)
                    
                current_time += beat_duration
                
        self.notes = notes
        return notes
        
    def generate_from_chords(self, progression, density: float = 0.5) -> List[Note]:
        """Generate melody that follows chord progression"""
        notes = []
        beat_duration = 60.0 / self.progression.bpm
        
        current_time = 0.0
        for chord, duration in progression.chords:
            chord_notes = chord.get_notes()
            bar_duration = duration * 4 * beat_duration
            
            num_notes = int(density * 4 * duration)
            
            for _ in range(num_notes):
                note_time = current_time + np.random.random() * bar_duration
                base_note = np.random.choice(chord_notes)
                
                # Add some passing tones
                if np.random.random() < 0.3:
                    midi_note = base_note.midi_note + np.random.choice([-2, -1, 1, 2])
                else:
                    midi_note = base_note.midi_note
                    
                note = Note(
                    midi_note,
                    velocity=np.random.randint(60, 120),
                    start_time=note_time,
                    duration=beat_duration * np.random.choice([0.5, 0.5, 1])
                )
                notes.append(note)
                
            current_time += bar_duration
            
        return notes


class PatternLibrary:
    """Library of pre-made patterns"""
    
    @staticmethod
    def get_drum_presets() -> List[str]:
        """Get available drum pattern presets"""
        return list(DrumPattern.PATTERNS.keys())
        
    @staticmethod
    def get_chord_progressions() -> List[str]:
        """Get available chord progressions"""
        return list(ChordProgression.COMMON_PROGRESSIONS.keys())
        
    @staticmethod
    def create_full_pattern(style: str, key: int = 0, bpm: float = 120) -> Dict:
        """Create full song pattern with drums, bass, chords, melody"""
        scale = Scale(key, ScaleType.MAJOR if style not in ["trap", "dnb"] else ScaleType.MINOR)
        
        # Create progression
        if style in ["pop", "house", "edm"]:
            progression = ChordProgression.from_progression_name("I_V_vi_IV", key)
        elif style == "jazz":
            progression = ChordProgression.from_progression_name("Jazz_ii_V_I", key)
        elif style in ["trap", "hip_hop"]:
            progression = ChordProgression.from_progression_name("i_VII_VI_V", key)
        else:
            progression = ChordProgression.from_progression_name("I_V_vi_IV", key)
            
        # Create patterns
        drums = DrumPattern.from_preset(
            "four_on_floor" if style in ["pop", "house", "edm"] else 
            "trap" if style == "trap" else "hip_hop",
            bpm
        )
        
        bass = BassPattern.from_chord_progression(progression, "walking", bpm)
        
        melody = MelodyPattern(scale, bpm)
        melody_notes = melody.generate(bars=4, density=0.6)
        
        return {
            "style": style,
            "bpm": bpm,
            "key": Note.NOTE_NAMES[key],
            "drums": drums.to_dict(),
            "chords": progression.to_dict(),
            "bass": [n.to_dict() for n in bass.to_midi_notes()],
            "melody": [n.to_dict() for n in melody_notes]
        }
