from dataclasses import dataclass

from storage import ContractError, decode_integer, encode_integer, pack_numbers, require_text, unpack_numbers
from windows.intervals import WindowInterval


@dataclass(frozen=True, slots=True)
class WindowRegistration:
    recording_id: str
    window_id: str
    interval: WindowInterval

    def __post_init__(self) -> None:
        require_text(self.recording_id, 'recording ID')
        require_text(self.window_id, 'window ID')
        if not isinstance(self.interval, WindowInterval):
            raise ContractError('registration requires a frozen window interval')


def initialize_registrations(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS registrations (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            window_id TEXT NOT NULL,
            left_number TEXT NOT NULL,
            right_number TEXT NOT NULL,
            expected TEXT NOT NULL,
            PRIMARY KEY (recording_id, window_id)
        )
    ''')


def _registration(recording_id: str, row) -> WindowRegistration:
    interval = WindowInterval(decode_integer(row['left_number']), decode_integer(row['right_number']),
                              unpack_numbers(row['expected']))
    return WindowRegistration(recording_id, row['window_id'], interval)


def find_registration(connection, recording_id: str, window_id: str) -> WindowRegistration | None:
    row = connection.execute('''
        SELECT window_id, left_number, right_number, expected
        FROM registrations WHERE recording_id = ? AND window_id = ?
    ''', (recording_id, window_id)).fetchone()
    return None if row is None else _registration(recording_id, row)


def insert_registration(connection, registration: WindowRegistration) -> None:
    interval = registration.interval
    connection.execute('INSERT INTO registrations VALUES (?, ?, ?, ?, ?)', (
        registration.recording_id, registration.window_id, encode_integer(interval.left),
        encode_integer(interval.right), pack_numbers(interval.expected),
    ))


def list_registrations(connection, recording_id: str) -> tuple[WindowRegistration, ...]:
    rows = connection.execute('''
        SELECT window_id, left_number, right_number, expected
        FROM registrations WHERE recording_id = ? ORDER BY window_id
    ''', (recording_id,))
    return tuple(_registration(recording_id, row) for row in rows)
