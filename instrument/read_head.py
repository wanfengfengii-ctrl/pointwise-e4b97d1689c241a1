from dataclasses import dataclass

from instrument.frames import Frame
from storage import NotFound, decode_integer, require_text


@dataclass(frozen=True, slots=True)
class ReadHead:
    recording_id: str
    position: int
    frame_number: int | None

    @property
    def at_end(self) -> bool:
        return self.frame_number is None


def initialize_read_head(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS source_heads (
            recording_id TEXT PRIMARY KEY REFERENCES recordings(recording_id),
            position INTEGER NOT NULL CHECK (position >= 0)
        )
    ''')


def _current_source_row(connection, recording_id: str):
    require_text(recording_id, 'recording ID')
    row = connection.execute('''
        SELECT h.position, f.number, f.value, f.origin
        FROM source_heads AS h
        LEFT JOIN source_frames AS f
          ON f.recording_id = h.recording_id AND f.position = h.position
        WHERE h.recording_id = ?
    ''', (recording_id,)).fetchone()
    if row is None:
        raise NotFound(f'unknown recording {recording_id!r}')
    return row


def read_head(connection, recording_id: str) -> ReadHead:
    row = _current_source_row(connection, recording_id)
    number = None if row['number'] is None else decode_integer(row['number'])
    return ReadHead(recording_id, row['position'], number)


def read_next_frame(connection, recording_id: str) -> Frame | None:
    row = _current_source_row(connection, recording_id)
    if row['number'] is None:
        return None
    return Frame(decode_integer(row['number']), decode_integer(row['value']), row['origin'])
