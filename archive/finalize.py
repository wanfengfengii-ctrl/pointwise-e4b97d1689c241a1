from archive.completeness import IncompleteWindow, inspect_window_progress
from archive.measurements import measure_window
from archive.store import ArchiveStore
from records.catalog import insert_artifact
from records.readback import PublishedResult, read_published
from records.receipts import find_window_receipt, insert_receipt
from storage import require_text


def finalize(store: ArchiveStore, recording_id: str, window_id: str) -> PublishedResult:
    require_text(recording_id, 'recording ID')
    require_text(window_id, 'window ID')
    with store.read_snapshot() as connection:
        receipt = find_window_receipt(connection, recording_id, window_id)
        if receipt is not None:
            return read_published(connection, receipt.result_id)

    with store.write_transaction() as connection:
        receipt = find_window_receipt(connection, recording_id, window_id)
        if receipt is not None:
            return read_published(connection, receipt.result_id)
        progress = inspect_window_progress(connection, recording_id, window_id)
        if not progress.complete:
            raise IncompleteWindow(progress)
        measurement = measure_window(progress.frames)
        artifact = insert_artifact(connection, recording_id, window_id, measurement.frames,
                                   measurement.peak_to_peak)
        receipt = insert_receipt(connection, recording_id, window_id, artifact.result_id)
        published = PublishedResult(artifact, receipt)
    return published
