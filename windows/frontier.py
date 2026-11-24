from storage import Conflict, decode_integer, encode_integer


def initialize_frontier(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS capture_frontiers (
            recording_id TEXT PRIMARY KEY REFERENCES recordings(recording_id),
            number TEXT NOT NULL
        )
    ''')


def read_frontier(connection, recording_id: str) -> int | None:
    row = connection.execute('SELECT number FROM capture_frontiers WHERE recording_id = ?',
                             (recording_id,)).fetchone()
    return None if row is None else decode_integer(row['number'])


def has_capture_reached(connection, recording_id: str, frame_number: int) -> bool:
    frontier = read_frontier(connection, recording_id)
    return frontier is not None and frontier >= frame_number


def advance_frontier(connection, recording_id: str, frame_number: int) -> None:
    if has_capture_reached(connection, recording_id, frame_number):
        raise Conflict('capture frontier must advance in recording order')
    connection.execute('''
        INSERT INTO capture_frontiers VALUES (?, ?)
        ON CONFLICT (recording_id) DO UPDATE SET number = excluded.number
    ''', (recording_id, encode_integer(frame_number)))
