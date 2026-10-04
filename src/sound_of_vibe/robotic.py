"""Constant-pitch resynthesis for deliberately mechanical voice delivery."""

from io import BytesIO
from threading import Lock

_PRAAT_LOCK = Lock()


def mechanical_audio(audio: bytes, gender: str = "Female", pitch_hz: float | None = None) -> bytes:
    # Praat's command dispatcher is shared within this process. Concurrent
    # preview requests must not interleave its object selection state.
    with _PRAAT_LOCK:
        return _render(audio, gender, pitch_hz)


def _render(audio: bytes, gender: str, pitch_hz: float | None) -> bytes:
    import numpy as np
    import parselmouth
    import soundfile as sf
    from parselmouth.praat import call

    samples, rate = sf.read(BytesIO(audio), always_2d=True)
    if len(samples) / rate > 60:
        raise ValueError("Speech segment exceeds 60 seconds")
    sound = parselmouth.Sound(samples.mean(axis=1), sampling_frequency=rate)
    manipulation = call(sound, "To Manipulation", 0.01, 60, 500)
    tier = call(manipulation, "Extract pitch tier")
    call(tier, "Remove points between", sound.xmin, sound.xmax)
    pitch = pitch_hz if pitch_hz is not None else (120 if gender == "Male" else 190)
    if not 60 <= pitch <= 500:
        raise ValueError("Mechanical pitch must be between 60 and 500 Hz")
    call(tier, "Add point", sound.xmin, pitch)
    call(tier, "Add point", sound.xmax, pitch)
    call([tier, manipulation], "Replace pitch tier")
    output = call(manipulation, "Get resynthesis (overlap-add)").values.T
    # Normalize utterance volume without clipping or amplifying silence.
    peak = float(np.max(np.abs(output)))
    if peak > 0:
        output *= min(2.0, 0.85 / peak)
    buffer = BytesIO()
    sf.write(buffer, output, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()
