from dataclasses import dataclass

from instrument.frames import Frame, validate_frame
from storage import ContractError


@dataclass(frozen=True, slots=True)
class WindowMeasurement:
    frames: tuple[Frame, ...]
    peak_to_peak: int


def measure_window(frames: tuple[Frame, ...]) -> WindowMeasurement:
    if not frames:
        raise ContractError('calculation requires a complete, nonempty window')
    for frame in frames:
        validate_frame(frame)
    values = [frame.value for frame in frames]
    return WindowMeasurement(frames, max(values) - min(values))
