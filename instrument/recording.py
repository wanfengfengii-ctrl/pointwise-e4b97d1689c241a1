import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

from instrument.frames import Frame, validate_frame
from storage import (
    Conflict, NotFound, encode_integer, pack_numbers, require_text,
    transaction, unpack_numbers,
)


@dataclass(frozen=True, slots=True)
class Recording:
    recording_id: str
    frame_numbers: tuple[int, ...]


def initialize_recordings(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS recordings (
            recording_id TEXT PRIMARY KEY,
            frame_numbers TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS source_frames (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            position INTEGER NOT NULL CHECK (position >= 0),
            number TEXT NOT NULL,
            value TEXT NOT NULL,
            origin TEXT NOT NULL,
            PRIMARY KEY (recording_id, position),
            UNIQUE (recording_id, number)
        )
    ''')


def _definition(frames: Iterable[Frame]):
    rows = []
    numbers = []
    for frame in frames:
        validate_frame(frame)
        if numbers and frame.number <= numbers[-1]:
            raise Conflict('source frame numbers must be strictly increasing')
        numbers.append(frame.number)
        rows.append((encode_integer(frame.number), encode_integer(frame.value), frame.origin))
    canonical = json.dumps(rows, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return tuple(numbers), rows, hashlib.sha256(canonical).hexdigest()


def open_recording(connection, recording_id: str, frames: Iterable[Frame]) -> Recording:
    require_text(recording_id, 'recording ID')
    numbers, rows, fingerprint = _definition(frames)
    with transaction(connection, write=True):
        existing = connection.execute(
            'SELECT frame_numbers, fingerprint FROM recordings WHERE recording_id = ?',
            (recording_id,),
        ).fetchone()
        if existing is not None:
            if existing['fingerprint'] != fingerprint or unpack_numbers(existing['frame_numbers']) != numbers:
                raise Conflict(f'recording {recording_id!r} has a different definition')
        else:
            connection.execute(
                'INSERT INTO recordings VALUES (?, ?, ?)',
                (recording_id, pack_numbers(numbers), fingerprint),
            )
            connection.executemany(
                'INSERT INTO source_frames VALUES (?, ?, ?, ?, ?)',
                ((recording_id, position, *row) for position, row in enumerate(rows)),
            )
            connection.execute('INSERT INTO source_heads VALUES (?, 0)', (recording_id,))
    return Recording(recording_id, numbers)


def load_recording(connection, recording_id: str) -> Recording:
    require_text(recording_id, 'recording ID')
    row = connection.execute(
        'SELECT frame_numbers FROM recordings WHERE recording_id = ?', (recording_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f'unknown recording {recording_id!r}')
    return Recording(recording_id, unpack_numbers(row['frame_numbers']))


def expected_frame_numbers(connection, recording_id: str) -> tuple[int, ...]:
    return load_recording(connection, recording_id).frame_numbers
