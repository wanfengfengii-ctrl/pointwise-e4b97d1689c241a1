"""Durable archive side storage for original frames spilled by the edge.

A spill is only a durable copy of the instrument original.  It does not by
itself deliver the frame to any registered window; window receipt remains a
separate business fact handled by :mod:`archive.intake`.
"""
import sqlite3
from dataclasses import dataclass

from instrument.frames import Frame, same_frame, validate_frame
from storage import (
    Conflict, ContractError, LinkUnavailable, decode_integer, encode_integer, require_text,
)


@dataclass(frozen=True, slots=True)
class SpilledFrame:
    recording_id: str
    frame: Frame
    position: int


def initialize_spill(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS archive_spills (
            recording_id TEXT NOT NULL,
            number TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0),
            value TEXT NOT NULL,
            origin TEXT NOT NULL,
            PRIMARY KEY (recording_id, number),
            UNIQUE (recording_id, position)
        )
    ''')


def _spilled(recording_id: str, row) -> SpilledFrame | None:
    if row is None:
        return None
    frame = Frame(decode_integer(row['number']), decode_integer(row['value']), row['origin'])
    return SpilledFrame(recording_id, frame, row['position'])


def find_spilled(connection, recording_id: str, frame_number: int) -> SpilledFrame | None:
    row = connection.execute(
        'SELECT number, position, value, origin FROM archive_spills '
        'WHERE recording_id = ? AND number = ?',
        (recording_id, encode_integer(frame_number)),
    ).fetchone()
    return _spilled(recording_id, row)


def store_spilled(connection, spilled: SpilledFrame) -> SpilledFrame:
    recording_id = spilled.recording_id
    require_text(recording_id, 'recording ID')
    frame = spilled.frame
    validate_frame(frame)
    if type(spilled.position) is not int or spilled.position < 0:
        raise ContractError('spilled source position must be a nonnegative integer')
    existing = find_spilled(connection, recording_id, frame.number)
    if existing is not None:
        if existing != spilled:
            raise Conflict('recording frame already has a different spilled original')
        return existing
    other = connection.execute(
        'SELECT number FROM archive_spills WHERE recording_id = ? AND position = ?',
        (recording_id, spilled.position),
    ).fetchone()
    if other is not None:
        raise Conflict('source position already holds a different spilled original')
    connection.execute(
        'INSERT INTO archive_spills(recording_id, number, position, value, origin) VALUES (?, ?, ?, ?, ?)',
        (recording_id, encode_integer(frame.number), spilled.position,
         encode_integer(frame.value), frame.origin),
    )
    return spilled


def read_spilled(archive, recording_id: str, frame_number: int) -> SpilledFrame | None:
    with archive.read_snapshot() as connection:
        return find_spilled(connection, recording_id, frame_number)


def spill_frame(archive, recording_id: str, position: int, frame: Frame) -> SpilledFrame:
    """Durably copy one source frame into the archive, then verify it.

    ``LinkUnavailable`` means the original could not be confirmed as durable,
    so the caller must keep the source frame pending release.
    """
    require_text(recording_id, 'recording ID')
    validate_frame(frame)
    expected = SpilledFrame(recording_id, frame, position)
    failure = None
    try:
        with archive.write_transaction() as connection:
            store_spilled(connection, expected)
    except (sqlite3.Error, LinkUnavailable) as error:
        failure = error
    try:
        durable = read_spilled(archive, recording_id, frame.number)
    except (sqlite3.Error, LinkUnavailable) as error:
        raise LinkUnavailable('spilled original could not be verified as durable') from error
    if durable is None:
        raise LinkUnavailable('spilled original was not durably recorded') from failure
    if durable != expected:
        raise Conflict('archive holds a different original for the spilled frame')
    return durable


def verify_spilled_original(connection, recording_id: str, frame: Frame,
                            position: int | None, window_id: str,
                            authorized_windows: tuple[str, ...] | None) -> None:
    """Archive reconciliation of a delivered frame against a durable spill.

    Whenever the archive holds a spilled original for this recording and
    frame, the delivery must present the same reading and origin, and the
    same source position.  For a delivery sourced from the spill the window
    must also be one the formal takeover actually authorized.
    """
    spilled = find_spilled(connection, recording_id, frame.number)
    if spilled is None:
        if authorized_windows is not None:
            raise Conflict('archive has no durable spill for this delivery')
        return
    if not same_frame(spilled.frame, frame):
        raise Conflict('delivered frame differs from the spilled original')
    if position is not None and position != spilled.position:
        raise Conflict('delivered source position differs from the spilled original')
    if authorized_windows is not None and window_id not in authorized_windows:
        raise Conflict('delivery is not authorized by the formal takeover for this window')
