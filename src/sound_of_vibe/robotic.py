"""Constant-pitch resynthesis for deliberately mechanical voice delivery."""

from io import BytesIO
from threading import Lock

_PRAAT_LOCK = Lock()


def mechanical_audio(audio: bytes, gender: str = "Female", pitch_hz: float | None = None,
                     pitch_scale: float = 1.0) -> bytes:
    # Praat's command dispatcher is shared within this process. Concurrent
    # preview requests must not interleave its object selection state.
    with _PRAAT_LOCK:
        return _render(audio, gender, pitch_hz, pitch_scale)


def _render(audio: bytes, gender: str, pitch_hz: float | None, pitch_scale: float) -> bytes:
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
    if pitch_hz is None and gender == "Unknown":
        frequencies = sound.to_pitch(time_step=.01, pitch_floor=60, pitch_ceiling=500).selected_array["frequency"]
        voiced = frequencies[frequencies > 0]
        pitch = float(np.median(voiced)) if len(voiced) else 190
    else:
        pitch = pitch_hz if pitch_hz is not None else (120 if gender == "Male" else 190)
    pitch *= pitch_scale
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
    # Remove provider padding at sentence boundaries, retaining 10 ms to avoid
    # clipping consonant onsets and releases. Internal pauses remain intact.
    active = np.flatnonzero(np.max(np.abs(output), axis=1) > 0.003)
    if len(active):
        padding = round(rate * 0.01)
        output = output[max(0, active[0] - padding):min(len(output), active[-1] + padding + 1)]
    buffer = BytesIO()
    sf.write(buffer, output, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()
