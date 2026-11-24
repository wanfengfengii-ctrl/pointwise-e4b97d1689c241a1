from archive.evidence import initialize_evidence
from archive.spill import initialize_spill
from records.catalog import initialize_catalog
from records.receipts import initialize_receipts
from storage import connect, transaction


class ArchiveStore:
    def __init__(self, path):
        self.connection = connect(path)
        try:
            initialize_archive(self)
        except BaseException:
            self.connection.close()
            raise

    def read_snapshot(self):
        return transaction(self.connection)

    def write_transaction(self):
        return transaction(self.connection, write=True)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def initialize_archive(store: ArchiveStore) -> None:
    with store.write_transaction() as connection:
        initialize_evidence(connection)
        initialize_spill(connection)
        initialize_catalog(connection)
        initialize_receipts(connection)
