from dataclasses import dataclass

from archive.evidence import list_window_frames, load_window_definition
from instrument.frames import Frame
from storage import NotFound
from windows.registrations import WindowRegistration


@dataclass(frozen=True, slots=True)
class WindowProgress:
    registration: WindowRegistration
    frames: tuple[Frame, ...]
    missing: tuple[int, ...]

    @property
    def complete(self) -> bool:
        return not self.missing


class IncompleteWindow(RuntimeError):
    def __init__(self, progress: WindowProgress):
        self.progress = progress
        self.missing = progress.missing
        numbers = ', '.join(format(number, 'x') for number in self.missing)
        super().__init__(f'window {progress.registration.window_id!r} is missing original frames (hex): {numbers}')


def inspect_window_progress(connection, recording_id: str, window_id: str) -> WindowProgress:
    registration = load_window_definition(connection, recording_id, window_id)
    if registration is None:
        raise NotFound(f'no archived definition for window {window_id!r} in {recording_id!r}')
    frames = list_window_frames(connection, recording_id, window_id)
    present = {frame.number for frame in frames}
    missing = tuple(number for number in registration.interval.expected if number not in present)
    return WindowProgress(registration, frames, missing)
