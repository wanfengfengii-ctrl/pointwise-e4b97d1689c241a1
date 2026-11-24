import sqlite3
from dataclasses import dataclass

from edge.obligations import add_obligations
from instrument.frames import Frame, validate_frame
from storage import Conflict, decode_integer, encode_integer
from windows.registrations import WindowRegistration


@dataclass(frozen=True, slots=True)
class CustodyRecord:
    recording_id: str
    number: int
    position: int
    frame: Frame | None
    release_confirmed: bool
    body_spilled: bool = False


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
    # A frame whose body lives only in the durable archive spill keeps its
    # edge-side takeover fact here, separate from the ordinary local body.
    connection.execute('''
        CREATE TABLE IF NOT EXISTS spilled_custody (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            number TEXT NOT NULL,
            position INTEGER NOT NULL,
            release_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (release_confirmed IN (0, 1)),
            PRIMARY KEY (recording_id, number),
            UNIQUE (recording_id, position)
        )
    ''')
    connection.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS one_unconfirmed_spilled_release
        ON spilled_custody(recording_id) WHERE release_confirmed = 0
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS spilled_custody_windows (
            recording_id TEXT NOT NULL,
            number TEXT NOT NULL,
            window_id TEXT NOT NULL,
            PRIMARY KEY (recording_id, number, window_id),
            FOREIGN KEY (recording_id, number)
                REFERENCES spilled_custody(recording_id, number)
        )
    ''')


class LocalBodyUnavailable(sqlite3.OperationalError):
    """The local frame body cannot be written while metadata remains writable."""


def write_local_body(connection, recording_id: str, position: int, frame: Frame) -> None:
    connection.execute('''
        INSERT INTO custody(recording_id, number, position, value, origin) VALUES (?, ?, ?, ?, ?)
    ''', (recording_id, encode_integer(frame.number), position, encode_integer(frame.value), frame.origin))


def _check_coverage(recording_id: str, frame_number: int,
                    windows: tuple[WindowRegistration, ...]) -> None:
    if any(window.recording_id != recording_id or not window.interval.contains(frame_number)
           for window in windows):
        raise Conflict('custody coverage includes an unrelated window')


def persist_custody(connection, recording_id: str, position: int, frame: Frame,
                    windows: tuple[WindowRegistration, ...]) -> CustodyRecord:
    validate_frame(frame)
    _check_coverage(recording_id, frame.number, windows)
    write_local_body(connection, recording_id, position, frame)
    add_obligations(connection, recording_id, frame.number, windows)
    return CustodyRecord(recording_id, frame.number, position, frame, False, False)


def persist_spill_metadata(connection, recording_id: str, position: int, frame_number: int,
                           windows: tuple[WindowRegistration, ...]) -> CustodyRecord:
    """Record the edge-side formal takeover for a frame whose body is in the archive.

    Only the recording, original number, source position and the windows
    authorized by this takeover are written.  The reading and origin stay
    solely in the durable archive spill and are reconciled again on delivery.
    """
    if type(frame_number) is not int:
        raise Conflict('spilled frame number must be an integer')
    _check_coverage(recording_id, frame_number, windows)
    if _position_used(connection, recording_id, position):
        raise Conflict('source position already holds a different original')
    connection.execute('''
        INSERT INTO spilled_custody(recording_id, number, position) VALUES (?, ?, ?)
    ''', (recording_id, encode_integer(frame_number), position))
    add_obligations(connection, recording_id, frame_number, windows)
    connection.executemany('''
        INSERT INTO spilled_custody_windows(recording_id, number, window_id) VALUES (?, ?, ?)
    ''', ((recording_id, encode_integer(frame_number), window.window_id) for window in windows))
    return CustodyRecord(recording_id, frame_number, position, None, False, True)


def _position_used(connection, recording_id: str, position: int) -> bool:
    row = connection.execute('''
        SELECT 1 FROM custody WHERE recording_id = ? AND position = ?
        UNION ALL
        SELECT 1 FROM spilled_custody WHERE recording_id = ? AND position = ?
    ''', (recording_id, position, recording_id, position)).fetchone()
    return row is not None


def _local_record(recording_id: str, row) -> CustodyRecord | None:
    if row is None:
        return None
    number = decode_integer(row['number'])
    frame = Frame(number, decode_integer(row['value']), row['origin'])
    return CustodyRecord(recording_id, number, row['position'], frame,
                         bool(row['release_confirmed']), False)


def _spilled_record(recording_id: str, row) -> CustodyRecord | None:
    if row is None:
        return None
    return CustodyRecord(recording_id, decode_integer(row['number']), row['position'], None,
                         bool(row['release_confirmed']), True)


def load_custody(connection, recording_id: str, frame_number: int) -> CustodyRecord | None:
    number = encode_integer(frame_number)
    record = _local_record(recording_id, connection.execute(
        'SELECT * FROM custody WHERE recording_id = ? AND number = ?', (recording_id, number)).fetchone())
    if record is not None:
        return record
    return _spilled_record(recording_id, connection.execute(
        'SELECT * FROM spilled_custody WHERE recording_id = ? AND number = ?',
        (recording_id, number)).fetchone())


def find_unconfirmed_release(connection, recording_id: str) -> CustodyRecord | None:
    local = _local_record(recording_id, connection.execute(
        'SELECT * FROM custody WHERE recording_id = ? AND release_confirmed = 0',
        (recording_id,)).fetchone())
    spilled = _spilled_record(recording_id, connection.execute(
        'SELECT * FROM spilled_custody WHERE recording_id = ? AND release_confirmed = 0',
        (recording_id,)).fetchone())
    if local is not None and spilled is not None:
        raise Conflict('more than one source frame awaits release confirmation')
    return local if local is not None else spilled


def list_custody_windows(connection, recording_id: str, frame_number: int) -> tuple[str, ...]:
    rows = connection.execute('''
        SELECT window_id FROM spilled_custody_windows
        WHERE recording_id = ? AND number = ? ORDER BY window_id
    ''', (recording_id, encode_integer(frame_number)))
    return tuple(row['window_id'] for row in rows)


def mark_release_confirmed(connection, custody: CustodyRecord) -> None:
    table = 'spilled_custody' if custody.body_spilled else 'custody'
    updated = connection.execute(f'''
        UPDATE {table} SET release_confirmed = 1
        WHERE recording_id = ? AND number = ? AND position = ?
    ''', (custody.recording_id, encode_integer(custody.number), custody.position))
    if updated.rowcount != 1:
        raise Conflict('release confirmation has no corresponding custody record')
