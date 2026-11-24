from dataclasses import dataclass

from instrument.read_head import read_head
from storage import Conflict, ContractError, LinkUnavailable, encode_integer, require_text, transaction


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    recording_id: str
    frame_number: int
    position: int


def initialize_releases(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS source_releases (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            number TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (recording_id, number),
            UNIQUE (recording_id, position)
        )
    ''')


def read_release_result(connection, recording_id: str, frame_number: int) -> ReleaseResult | None:
    require_text(recording_id, 'recording ID')
    if type(frame_number) is not int:
        raise ContractError('release frame number must be an integer')
    row = connection.execute(
        'SELECT position FROM source_releases WHERE recording_id = ? AND number = ?',
        (recording_id, encode_integer(frame_number)),
    ).fetchone()
    return None if row is None else ReleaseResult(recording_id, frame_number, row['position'])


def release_frame(connection, recording_id: str, frame_number: int) -> ReleaseResult:
    with transaction(connection, write=True):
        existing = read_release_result(connection, recording_id, frame_number)
        if existing is not None:
            return existing
        head = read_head(connection, recording_id)
        if head.frame_number != frame_number:
            raise Conflict('release request does not match the current source frame')
        number = encode_integer(frame_number)
        connection.execute('INSERT INTO source_releases VALUES (?, ?, ?)',
                           (recording_id, number, head.position))
        connection.execute('DELETE FROM source_frames WHERE recording_id = ? AND position = ?',
                           (recording_id, head.position))
        connection.execute('UPDATE source_heads SET position = ? WHERE recording_id = ?',
                           (head.position + 1, recording_id))
    return ReleaseResult(recording_id, frame_number, head.position)


class ReleasePort:
    """Recorded transport with optional, one-shot interruption at explicit frame keys."""

    def __init__(self, *, hold_once=None, drop_reply_once=None):
        self.hold_once = hold_once
        self.drop_reply_once = drop_reply_once

    def __call__(self, connection, recording_id: str, frame_number: int) -> ReleaseResult:
        key = (recording_id, frame_number)
        if key == self.hold_once:
            self.hold_once = None
            raise LinkUnavailable('release command did not reach the instrument')
        result = release_frame(connection, recording_id, frame_number)
        if key == self.drop_reply_once:
            self.drop_reply_once = None
            raise LinkUnavailable('instrument released the frame but the reply was lost')
        return result
