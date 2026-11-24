from dataclasses import dataclass

from storage import Conflict, decode_integer, encode_integer, unpack_numbers
from windows.intervals import WindowInterval
from windows.registrations import WindowRegistration


@dataclass(frozen=True, slots=True)
class DeliveryObligation:
    registration: WindowRegistration
    frame_number: int


def initialize_obligations(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS delivery_obligations (
            recording_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            number TEXT NOT NULL,
            accepted INTEGER NOT NULL DEFAULT 0 CHECK (accepted IN (0, 1)),
            PRIMARY KEY (recording_id, window_id, number),
            FOREIGN KEY (recording_id, window_id) REFERENCES registrations(recording_id, window_id),
            FOREIGN KEY (recording_id, number) REFERENCES custody(recording_id, number)
        )
    ''')
    connection.execute('''
        CREATE INDEX IF NOT EXISTS pending_delivery_by_recording
        ON delivery_obligations(recording_id, number, window_id) WHERE accepted = 0
    ''')


def add_obligations(connection, recording_id: str, frame_number: int,
                    windows: tuple[WindowRegistration, ...]) -> None:
    connection.executemany('''
        INSERT INTO delivery_obligations(recording_id, window_id, number) VALUES (?, ?, ?)
    ''', ((recording_id, window.window_id, encode_integer(frame_number)) for window in windows))


def list_pending_obligations(connection, recording_id: str) -> tuple[DeliveryObligation, ...]:
    rows = connection.execute('''
        SELECT o.window_id, o.number, r.left_number, r.right_number, r.expected
        FROM delivery_obligations AS o
        JOIN registrations AS r USING (recording_id, window_id)
        JOIN custody AS c ON c.recording_id = o.recording_id AND c.number = o.number
        WHERE o.recording_id = ? AND o.accepted = 0
        ORDER BY c.position, o.window_id
    ''', (recording_id,))
    obligations = []
    for row in rows:
        interval = WindowInterval(decode_integer(row['left_number']), decode_integer(row['right_number']),
                                  unpack_numbers(row['expected']))
        registration = WindowRegistration(recording_id, row['window_id'], interval)
        obligations.append(DeliveryObligation(registration, decode_integer(row['number'])))
    return tuple(obligations)


def mark_obligation_accepted(connection, obligation: DeliveryObligation) -> None:
    registration = obligation.registration
    updated = connection.execute('''
        UPDATE delivery_obligations SET accepted = 1
        WHERE recording_id = ? AND window_id = ? AND number = ?
    ''', (registration.recording_id, registration.window_id, encode_integer(obligation.frame_number)))
    if updated.rowcount != 1:
        raise Conflict('archive acknowledgment has no corresponding obligation')
