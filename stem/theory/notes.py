"""
MIDI Note and Music Theory utilities
"""
from typing import List, Dict, Tuple, Optional
from enum import Enum
import numpy as np


class Note:
    """Represents a musical note"""
    
    NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    
    def __init__(self, midi_note: int, velocity: int = 100, 
                 start_time: float = 0.0, duration: float = 0.5):
        self.midi_note = midi_note
        self.velocity = max(0, min(127, velocity))
        self.start_time = start_time
        self.duration = max(0, duration)
        
    @property
    def name(self) -> str:
        """Get note name (e.g., 'C4')"""
        octave = (self.midi_note // 12) - 1
        note_name = self.NOTE_NAMES[self.midi_note % 12]
        return f"{note_name}{octave}"
        
    @property
    def frequency(self) -> float:
        """Get note frequency in Hz"""
        return 440.0 * (2.0 ** ((self.midi_note - 69) / 12.0))
        
    @classmethod
    def from_name(cls, name: str, octave: int, velocity: int = 100) -> 'Note':
        """Create note from name and octave"""
        note_name = name.upper()
        if note_name not in cls.NOTE_NAMES:
            raise ValueError(f"Invalid note name: {name}")
        
        midi_note = cls.NOTE_NAMES.index(note_name) + (octave + 1) * 12
        return cls(midi_note, velocity)
        
    def transpose(self, semitones: int) -> 'Note':
        """Transpose note by semitones"""
        return Note(self.midi_note + semitones, self.velocity, 
                   self.start_time, self.duration)
        
    def to_dict(self) -> Dict:
        return {
            "midi": self.midi_note,
            "name": self.name,
            "velocity": self.velocity,
            "start": self.start_time,
            "duration": self.duration,
            "frequency": self.frequency
        }


class ScaleType(Enum):
    MAJOR = "major"
    MINOR = "minor"
    DORIAN = "dorian"
    PHRYGIAN = "phrygian"
    LYDIAN = "lydian"
    MIXOLYDIAN = "mixolydian"
    LOCRIAN = "locrian"
    PENTATONIC_MAJOR = "pentatonic_major"
    PENTATONIC_MINOR = "pentatonic_minor"
    BLUES = "blues"
    CHROMATIC = "chromatic"
    HARMONIC_MINOR = "harmonic_minor"
    MELODIC_MINOR = "melodic_minor"


class Scale:
    """Musical scale with intervals"""
    
    INTERVALS = {
        ScaleType.MAJOR: [0, 2, 4, 5, 7, 9, 11],
        ScaleType.MINOR: [0, 2, 3, 5, 7, 8, 10],
        ScaleType.DORIAN: [0, 2, 3, 5, 7, 9, 10],
        ScaleType.PHRYGIAN: [0, 1, 3, 5, 7, 8, 10],
        ScaleType.LYDIAN: [0, 2, 4, 6, 7, 9, 11],
        ScaleType.MIXOLYDIAN: [0, 2, 4, 5, 7, 9, 10],
        ScaleType.LOCRIAN: [0, 1, 3, 5, 6, 8, 10],
        ScaleType.PENTATONIC_MAJOR: [0, 2, 4, 7, 9],
        ScaleType.PENTATONIC_MINOR: [0, 3, 5, 7, 10],
        ScaleType.BLUES: [0, 3, 5, 6, 7, 10],
        ScaleType.CHROMATIC: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        ScaleType.HARMONIC_MINOR: [0, 2, 3, 5, 7, 8, 11],
        ScaleType.MELODIC_MINOR: [0, 2, 3, 5, 7, 9, 11],
    }
    
    def __init__(self, root_note: int, scale_type: ScaleType):
        self.root_note = root_note
        self.scale_type = scale_type
        self.intervals = self.INTERVALS[scale_type]
        
    def get_notes(self, octave: int = 4) -> List[Note]:
        """Get all notes in scale for an octave"""
        base = (octave + 1) * 12
        return [Note(base + self.root_note + interval) for interval in self.intervals]
        
    def is_in_scale(self, midi_note: int) -> bool:
        """Check if note is in scale"""
        relative = (midi_note - self.root_note) % 12
        return relative in self.intervals
        
    def get_degree(self, midi_note: int) -> Optional[int]:
        """Get scale degree of note (1-7 for diatonic)"""
        relative = (midi_note - self.root_note) % 12
        if relative in self.intervals:
            return self.intervals.index(relative) + 1
        return None


class ChordType(Enum):
    MAJOR = "major"
    MINOR = "minor"
    DIMINISHED = "diminished"
    AUGMENTED = "augmented"
    MAJOR_7 = "major_7"
    MINOR_7 = "minor_7"
    DOMINANT_7 = "dominant_7"
    MAJOR_9 = "major_9"
    MINOR_9 = "minor_9"
    SUS2 = "sus2"
    SUS4 = "sus4"
    ADD9 = "add9"
    SIX = "six"
    MINOR_SIX = "minor_six"
    DIMINISHED_7 = "diminished_7"
    HALF_DIMINISHED = "half_diminished"


class Chord:
    """Musical chord"""
    
    # Intervals from root (in semitones)
    INTERVALS = {
        ChordType.MAJOR: [0, 4, 7],
        ChordType.MINOR: [0, 3, 7],
        ChordType.DIMINISHED: [0, 3, 6],
        ChordType.AUGMENTED: [0, 4, 8],
        ChordType.MAJOR_7: [0, 4, 7, 11],
        ChordType.MINOR_7: [0, 3, 7, 10],
        ChordType.DOMINANT_7: [0, 4, 7, 10],
        ChordType.MAJOR_9: [0, 4, 7, 11, 14],
        ChordType.MINOR_9: [0, 3, 7, 10, 14],
        ChordType.SUS2: [0, 2, 7],
        ChordType.SUS4: [0, 5, 7],
        ChordType.ADD9: [0, 4, 7, 14],
        ChordType.SIX: [0, 4, 7, 9],
        ChordType.MINOR_SIX: [0, 3, 7, 9],
        ChordType.DIMINISHED_7: [0, 3, 6, 9],
        ChordType.HALF_DIMINISHED: [0, 3, 6, 10],
    }
    
    # Roman numeral notation for progressions
    ROMAN_NUMERALS = {
        1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII"
    }
    
    def __init__(self, root_note: int, chord_type: ChordType, 
                 octave: int = 4, inversion: int = 0):
        self.root_note = root_note
        self.chord_type = chord_type
        self.octave = octave
        self.inversion = inversion
        self.intervals = self.INTERVALS[chord_type]
        
    def get_notes(self) -> List[Note]:
        """Get all notes in chord"""
        base = (self.octave + 1) * 12 + self.root_note
        notes = [Note(base + interval) for interval in self.intervals]
        
        # Apply inversion
        for i in range(self.inversion):
            if i < len(notes):
                notes[i] = notes[i].transpose(12)
                
        return sorted(notes, key=lambda n: n.midi_note)
        
    def get_name(self) -> str:
        """Get chord name"""
        root_name = Note.NOTE_NAMES[self.root_note]
        type_suffix = {
            ChordType.MAJOR: "",
            ChordType.MINOR: "m",
            ChordType.DIMINISHED: "dim",
            ChordType.AUGMENTED: "aug",
            ChordType.MAJOR_7: "maj7",
            ChordType.MINOR_7: "m7",
            ChordType.DOMINANT_7: "7",
            ChordType.MAJOR_9: "maj9",
            ChordType.MINOR_9: "m9",
            ChordType.SUS2: "sus2",
            ChordType.SUS4: "sus4",
            ChordType.ADD9: "add9",
            ChordType.SIX: "6",
            ChordType.MINOR_SIX: "m6",
            ChordType.DIMINISHED_7: "dim7",
            ChordType.HALF_DIMINISHED: "m7b5",
        }
        return f"{root_name}{type_suffix.get(self.chord_type, '')}"
        
    def to_dict(self) -> Dict:
        return {
            "name": self.get_name(),
            "root": Note.NOTE_NAMES[self.root_note],
            "type": self.chord_type.value,
            "octave": self.octave,
            "inversion": self.inversion,
            "notes": [n.to_dict() for n in self.get_notes()]
        }


class ChordProgression:
    """Chord progression builder"""
    
    # Common progressions (scale degrees)
    COMMON_PROGRESSIONS = {
        "I_V_vi_IV": [(1, ChordType.MAJOR), (5, ChordType.MAJOR), 
                     (6, ChordType.MINOR), (4, ChordType.MAJOR)],
        "I_IV_V": [(1, ChordType.MAJOR), (4, ChordType.MAJOR), (5, ChordType.MAJOR)],
        "ii_V_I": [(2, ChordType.MINOR), (5, ChordType.MAJOR), (1, ChordType.MAJOR)],
        "I_vi_IV_V": [(1, ChordType.MAJOR), (6, ChordType.MINOR),
                     (4, ChordType.MAJOR), (5, ChordType.MAJOR)],
        "vi_IV_I_V": [(6, ChordType.MINOR), (4, ChordType.MAJOR),
                     (1, ChordType.MAJOR), (5, ChordType.MAJOR)],
        "I_V_vi_iii_IV": [(1, ChordType.MAJOR), (5, ChordType.MAJOR),
                         (6, ChordType.MINOR), (3, ChordType.MINOR),
                         (4, ChordType.MAJOR)],
        "i_VII_VI_V": [(1, ChordType.MINOR), (7, ChordType.MAJOR),
                      (6, ChordType.MAJOR), (5, ChordType.MAJOR)],
        "Andalusian": [(1, ChordType.MINOR), (7, ChordType.MAJOR),
                      (6, ChordType.MAJOR), (5, ChordType.MAJOR)],
        "Jazz_ii_V_I": [(2, ChordType.MINOR_7), (5, ChordType.DOMINANT_7),
                       (1, ChordType.MAJOR_7)],
        "Emotional": [(6, ChordType.MINOR), (4, ChordType.MAJOR),
                     (1, ChordType.MAJOR), (5, ChordType.MAJOR)],
    }
    
    def __init__(self, scale: Scale):
        self.scale = scale
        self.chords: List[Tuple[Chord, float]] = []  # (chord, duration)
        
    @classmethod
    def from_progression_name(cls, name: str, key: int = 0) -> 'ChordProgression':
        """Create progression from common name"""
        if name not in cls.COMMON_PROGRESSIONS:
            raise ValueError(f"Unknown progression: {name}")
            
        scale_type = ScaleType.MAJOR
        if name.startswith("i_") or name == "Andalusian":
            scale_type = ScaleType.MINOR
            
        scale = Scale(key, scale_type)
        progression = cls(scale)
        
        for degree, chord_type in cls.COMMON_PROGRESSIONS[name]:
            chord = progression.get_chord_for_degree(degree, chord_type)
            progression.add_chord(chord, 1.0)  # 1 bar per chord
            
        return progression
        
    def get_chord_for_degree(self, degree: int, chord_type: ChordType = None) -> Chord:
        """Get chord for scale degree (1-7)"""
        # Get root note for degree
        root_interval = self.scale.intervals[degree - 1]
        root = (self.scale.root_note + root_interval) % 12
        
        # Determine chord type if not specified
        if chord_type is None:
            chord_type = self._get_diatonic_chord_type(degree)
            
        return Chord(root, chord_type)
        
    def _get_diatonic_chord_type(self, degree: int) -> ChordType:
        """Get diatonic chord type for degree"""
        is_major = self.scale.scale_type in [ScaleType.MAJOR, ScaleType.LYDIAN, ScaleType.MIXOLYDIAN]
        
        if is_major:
            types = [ChordType.MAJOR, ChordType.MINOR, ChordType.MINOR, 
                    ChordType.MAJOR, ChordType.MAJOR, ChordType.MINOR, ChordType.DIMINISHED]
        else:
            types = [ChordType.MINOR, ChordType.DIMINISHED, ChordType.MAJOR,
                    ChordType.MINOR, ChordType.MINOR, ChordType.MAJOR, ChordType.MAJOR]
                    
        return types[degree - 1]
        
    def add_chord(self, chord: Chord, duration: float = 1.0):
        """Add chord to progression"""
        self.chords.append((chord, duration))
        
    def to_midi_notes(self, bpm: float = 120) -> List[Note]:
        """Convert progression to MIDI notes"""
        notes = []
        current_time = 0.0
        seconds_per_bar = 240.0 / bpm
        
        for chord, bars in self.chords:
            chord_notes = chord.get_notes()
            duration = bars * seconds_per_bar
            
            for note in chord_notes:
                notes.append(Note(
                    note.midi_note,
                    velocity=80,
                    start_time=current_time,
                    duration=duration
                ))
                
            current_time += duration
            
        return notes
        
    def to_dict(self) -> Dict:
        return {
            "scale": self.scale.scale_type.value,
            "root": Note.NOTE_NAMES[self.scale.root_note],
            "chords": [
                {
                    "chord": c.to_dict(),
                    "duration": d,
                    "roman": self._get_roman_numeral(c)
                }
                for c, d in self.chords
            ]
        }
        
    def _get_roman_numeral(self, chord: Chord) -> str:
        """Get Roman numeral for chord"""
        relative = (chord.root_note - self.scale.root_note) % 12
        if relative in self.scale.intervals:
            degree = self.scale.intervals.index(relative) + 1
            numeral = Chord.ROMAN_NUMERALS[degree]
            
            # Lowercase for minor
            if chord.chord_type in [ChordType.MINOR, ChordType.MINOR_7, ChordType.DIMINISHED]:
                return numeral.lower()
            return numeral
        return "?"


class Arpeggiator:
    """Arpeggio pattern generator"""
    
    PATTERNS = {
        "up": [0, 1, 2, 3],
        "down": [3, 2, 1, 0],
        "up_down": [0, 1, 2, 3, 2, 1],
        "down_up": [3, 2, 1, 0, 1, 2],
        "random": None,  # Randomized
        "chord": [0, 0, 0, 0],  # All together
    }
    
    def __init__(self, pattern: str = "up", octave_range: int = 1, 
                 rate: str = "1/8", gate: float = 0.8):
        self.pattern = pattern
        self.octave_range = octave_range
        self.rate = rate
        self.gate = gate
        
    def generate(self, chord: Chord, duration: float, bpm: float = 120) -> List[Note]:
        """Generate arpeggio notes from chord"""
        notes = []
        chord_notes = chord.get_notes()
        
        if self.pattern == "random":
            pattern_indices = [np.random.randint(0, len(chord_notes)) 
                             for _ in range(int(duration * 4))]
        else:
            pattern_indices = self.PATTERNS.get(self.pattern, self.PATTERNS["up"])
            
        # Calculate note duration from rate
        rate_divisor = {
            "1/1": 1, "1/2": 2, "1/4": 4, "1/8": 8,
            "1/16": 16, "1/32": 32, "1/4T": 6, "1/8T": 12, "1/16T": 24
        }
        
        div = rate_divisor.get(self.rate, 8)
        beat_duration = 60.0 / bpm
        note_duration = (4.0 / div) * beat_duration * self.gate
        
        current_time = 0.0
        step_duration = (4.0 / div) * beat_duration
        
        step = 0
        while current_time < duration:
            idx = pattern_indices[step % len(pattern_indices)]
            
            # Handle octave jumps
            octave_offset = (step // len(pattern_indices)) % self.octave_range
            base_note = chord_notes[idx % len(chord_notes)]
            
            note = Note(
                base_note.midi_note + (octave_offset * 12),
                velocity=100,
                start_time=current_time,
                duration=note_duration
            )
            notes.append(note)
            
            current_time += step_duration
            step += 1
            
        return notes
