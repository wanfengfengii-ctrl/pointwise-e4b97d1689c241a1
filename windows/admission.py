from instrument.recording import expected_frame_numbers
from storage import Conflict, ContractError, LateRegistration, require_text, transaction
from windows.frontier import has_capture_reached
from windows.intervals import bind_interval
from windows.registrations import WindowRegistration, find_registration, insert_registration


def register(connection, recording_id: str, window_id: str, left: int, right: int) -> WindowRegistration:
    require_text(recording_id, 'recording ID')
    require_text(window_id, 'window ID')
    if type(left) is not int or type(right) is not int:
        raise ContractError('window endpoints must be integers')
    with transaction(connection, write=True):
        existing = find_registration(connection, recording_id, window_id)
        if existing is not None:
            if (left, right) != (existing.interval.left, existing.interval.right):
                raise Conflict(f'window {window_id!r} has a different interval')
            return existing
        interval = bind_interval(expected_frame_numbers(connection, recording_id), left, right)
        if has_capture_reached(connection, recording_id, interval.first_frame):
            raise LateRegistration(f'window {window_id!r} starts at an already captured frame')
        registration = WindowRegistration(recording_id, window_id, interval)
        insert_registration(connection, registration)
    return registration
