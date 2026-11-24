from dataclasses import dataclass

from instrument.frames import Frame
from storage import decode_integer, encode_integer


@dataclass(frozen=True, slots=True)
class Artifact:
    result_id: int
    recording_id: str
    window_id: str
    frames: tuple[Frame, ...]
    peak_to_peak: int


def initialize_catalog(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS artifacts (
            result_id INTEGER PRIMARY KEY,
            recording_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            peak_to_peak TEXT NOT NULL,
            UNIQUE (recording_id, window_id),
            UNIQUE (recording_id, window_id, result_id),
            FOREIGN KEY (recording_id, window_id) REFERENCES archive_windows(recording_id, window_id)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS artifact_frames (
            result_id INTEGER NOT NULL REFERENCES artifacts(result_id),
            position INTEGER NOT NULL,
            number TEXT NOT NULL,
            value TEXT NOT NULL,
            origin TEXT NOT NULL,
            PRIMARY KEY (result_id, position),
            UNIQUE (result_id, number)
        )
    ''')


def insert_artifact(connection, recording_id: str, window_id: str, frames: tuple[Frame, ...],
                    peak_to_peak: int) -> Artifact:
    cursor = connection.execute('''
        INSERT INTO artifacts(recording_id, window_id, peak_to_peak) VALUES (?, ?, ?)
    ''', (recording_id, window_id, encode_integer(peak_to_peak)))
    result_id = cursor.lastrowid
    connection.executemany('INSERT INTO artifact_frames VALUES (?, ?, ?, ?, ?)', (
        (result_id, position, encode_integer(frame.number), encode_integer(frame.value), frame.origin)
        for position, frame in enumerate(frames)
    ))
    return Artifact(result_id, recording_id, window_id, frames, peak_to_peak)


def find_artifact(connection, result_id: int) -> Artifact | None:
    row = connection.execute('SELECT * FROM artifacts WHERE result_id = ?', (result_id,)).fetchone()
    if row is None:
        return None
    evidence = connection.execute('''
        SELECT number, value, origin FROM artifact_frames WHERE result_id = ? ORDER BY position
    ''', (result_id,))
    frames = tuple(Frame(decode_integer(item['number']), decode_integer(item['value']), item['origin'])
                   for item in evidence)
    return Artifact(result_id, row['recording_id'], row['window_id'], frames,
                    decode_integer(row['peak_to_peak']))
