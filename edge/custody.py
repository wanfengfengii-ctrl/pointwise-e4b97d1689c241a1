import sqlite3
from dataclasses import dataclass

from edge.obligations import add_obligations
from instrument.frames import Frame, validate_frame
from storage import Conflict, decode_integer, encode_integer
from windows.registrations import WindowRegistration


@dataclass(frozen=True, slots=True)
class CustodyRecord:
    recording_id: str
    position: int
    frame: Frame
    release_confirmed: bool


def initialize_custody(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS custody (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            number TEXT NOT NULL,
            position INTEGER NOT NULL,
            value TEXT NOT NULL,
            origin TEXT NOT NULL,
            release_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (release_confirmed IN (0, 1)),
            PRIMARY KEY (recording_id, number),
            UNIQUE (recording_id, position)
        )
    ''')
    connection.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS one_unconfirmed_release
        ON custody(recording_id) WHERE release_confirmed = 0
    ''')


class LocalBodyUnavailable(sqlite3.OperationalError):
    """The local frame body cannot be written while metadata remains writable."""


def write_local_body(connection, recording_id: str, position: int, frame: Frame) -> None:
    connection.execute('''
        INSERT INTO custody(recording_id, number, position, value, origin) VALUES (?, ?, ?, ?, ?)
    ''', (recording_id, encode_integer(frame.number), position, encode_integer(frame.value), frame.origin))


def persist_custody(connection, recording_id: str, position: int, frame: Frame,
                    windows: tuple[WindowRegistration, ...]) -> CustodyRecord:
    validate_frame(frame)
    if any(window.recording_id != recording_id or not window.interval.contains(frame.number)
           for window in windows):
        raise Conflict('custody coverage includes an unrelated window')
    write_local_body(connection, recording_id, position, frame)
    add_obligations(connection, recording_id, frame.number, windows)
    return CustodyRecord(recording_id, position, frame, False)


def _record(recording_id: str, row) -> CustodyRecord | None:
    if row is None:
        return None
    frame = Frame(decode_integer(row['number']), decode_integer(row['value']), row['origin'])
    return CustodyRecord(recording_id, row['position'], frame, bool(row['release_confirmed']))


def load_custody(connection, recording_id: str, frame_number: int) -> CustodyRecord | None:
    row = connection.execute('SELECT * FROM custody WHERE recording_id = ? AND number = ?',
                             (recording_id, encode_integer(frame_number))).fetchone()
    return _record(recording_id, row)


def find_unconfirmed_release(connection, recording_id: str) -> CustodyRecord | None:
    row = connection.execute('SELECT * FROM custody WHERE recording_id = ? AND release_confirmed = 0',
                             (recording_id,)).fetchone()
    return _record(recording_id, row)


def mark_release_confirmed(connection, custody: CustodyRecord) -> None:
    updated = connection.execute('''
        UPDATE custody SET release_confirmed = 1
        WHERE recording_id = ? AND number = ? AND position = ?
    ''', (custody.recording_id, encode_integer(custody.frame.number), custody.position))
    if updated.rowcount != 1:
        raise Conflict('release confirmation has no corresponding custody record')
