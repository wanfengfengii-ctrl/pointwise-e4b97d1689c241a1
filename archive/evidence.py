from instrument.frames import Frame, same_frame, validate_frame
from storage import Conflict, ContractError, decode_integer, encode_integer, pack_numbers, unpack_numbers
from windows.intervals import WindowInterval
from windows.registrations import WindowRegistration


def initialize_evidence(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS archive_windows (
            recording_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            left_number TEXT NOT NULL,
            right_number TEXT NOT NULL,
            expected TEXT NOT NULL,
            PRIMARY KEY (recording_id, window_id)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS archive_frames (
            recording_id TEXT NOT NULL,
            number TEXT NOT NULL,
            value TEXT NOT NULL,
            origin TEXT NOT NULL,
            PRIMARY KEY (recording_id, number)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS archive_window_frames (
            recording_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            number TEXT NOT NULL,
            PRIMARY KEY (recording_id, window_id, number),
            FOREIGN KEY (recording_id, window_id) REFERENCES archive_windows(recording_id, window_id),
            FOREIGN KEY (recording_id, number) REFERENCES archive_frames(recording_id, number)
        )
    ''')


def load_window_definition(connection, recording_id: str, window_id: str) -> WindowRegistration | None:
    row = connection.execute('''
        SELECT left_number, right_number, expected FROM archive_windows
        WHERE recording_id = ? AND window_id = ?
    ''', (recording_id, window_id)).fetchone()
    if row is None:
        return None
    interval = WindowInterval(decode_integer(row['left_number']), decode_integer(row['right_number']),
                              unpack_numbers(row['expected']))
    return WindowRegistration(recording_id, window_id, interval)


def store_window_definition(connection, registration: WindowRegistration) -> None:
    recording_id, window_id = registration.recording_id, registration.window_id
    existing = load_window_definition(connection, recording_id, window_id)
    if existing is not None:
        if existing != registration:
            raise Conflict('delivery changes an archived window definition')
        return
    interval = registration.interval
    connection.execute('INSERT INTO archive_windows VALUES (?, ?, ?, ?, ?)', (
        recording_id, window_id, encode_integer(interval.left), encode_integer(interval.right),
        pack_numbers(interval.expected),
    ))


def store_window_frame(connection, registration: WindowRegistration, frame: Frame) -> None:
    validate_frame(frame)
    if not registration.interval.contains(frame.number):
        raise ContractError('frame is not expected by this window')
    recording_id = registration.recording_id
    number = encode_integer(frame.number)
    row = connection.execute('''
        SELECT value, origin FROM archive_frames WHERE recording_id = ? AND number = ?
    ''', (recording_id, number)).fetchone()
    if row is None:
        connection.execute('INSERT INTO archive_frames VALUES (?, ?, ?, ?)',
                           (recording_id, number, encode_integer(frame.value), frame.origin))
    elif not same_frame(frame, Frame(frame.number, decode_integer(row['value']), row['origin'])):
        raise Conflict('recording frame already has a different reading or origin')
    key = (recording_id, registration.window_id, number)
    existing = connection.execute('''
        SELECT 1 FROM archive_window_frames WHERE recording_id = ? AND window_id = ? AND number = ?
    ''', key).fetchone()
    if existing is None:
        connection.execute('INSERT INTO archive_window_frames VALUES (?, ?, ?)', key)


def list_window_frames(connection, recording_id: str, window_id: str) -> tuple[Frame, ...]:
    rows = connection.execute('''
        SELECT f.number, f.value, f.origin
        FROM archive_window_frames AS w
        JOIN archive_frames AS f ON f.recording_id = w.recording_id AND f.number = w.number
        WHERE w.recording_id = ? AND w.window_id = ?
    ''', (recording_id, window_id))
    frames = (Frame(decode_integer(row['number']), decode_integer(row['value']), row['origin'])
              for row in rows)
    return tuple(sorted(frames, key=lambda frame: frame.number))
