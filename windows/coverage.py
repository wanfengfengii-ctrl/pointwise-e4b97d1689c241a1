from windows.registrations import WindowRegistration, list_registrations


def covering_windows(connection, recording_id: str, frame_number: int) -> tuple[WindowRegistration, ...]:
    return tuple(
        registration for registration in list_registrations(connection, recording_id)
        if registration.interval.contains(frame_number)
    )
