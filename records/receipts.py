from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Receipt:
    recording_id: str
    window_id: str
    result_id: int


def initialize_receipts(connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS publication_receipts (
            recording_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            result_id INTEGER NOT NULL UNIQUE,
            PRIMARY KEY (recording_id, window_id),
            FOREIGN KEY (recording_id, window_id, result_id)
                REFERENCES artifacts(recording_id, window_id, result_id)
        )
    ''')


def _receipt(row) -> Receipt | None:
    if row is None:
        return None
    return Receipt(row['recording_id'], row['window_id'], row['result_id'])


def find_window_receipt(connection, recording_id: str, window_id: str) -> Receipt | None:
    row = connection.execute('''
        SELECT * FROM publication_receipts WHERE recording_id = ? AND window_id = ?
    ''', (recording_id, window_id)).fetchone()
    return _receipt(row)


def find_result_receipt(connection, result_id: int) -> Receipt | None:
    row = connection.execute('SELECT * FROM publication_receipts WHERE result_id = ?',
                             (result_id,)).fetchone()
    return _receipt(row)


def insert_receipt(connection, recording_id: str, window_id: str, result_id: int) -> Receipt:
    connection.execute('INSERT INTO publication_receipts VALUES (?, ?, ?)',
                       (recording_id, window_id, result_id))
    return Receipt(recording_id, window_id, result_id)
