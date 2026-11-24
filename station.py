from archive.finalize import finalize as finalize
from archive.store import ArchiveStore as ArchiveStore
from archive.store import initialize_archive as initialize_archive
from edge.capture import AdvanceResult as AdvanceResult
from edge.capture import advance as advance
from edge.capture import advance_with_archive as advance_with_archive
from edge.custody import initialize_custody
from edge.delivery import DeliveryReport as DeliveryReport
from edge.delivery import deliver as deliver
from edge.obligations import initialize_obligations
from instrument.frames import Frame as Frame
from instrument.read_head import initialize_read_head
from instrument.recording import initialize_recordings
from instrument.recording import open_recording as open_recording
from instrument.release import ReleasePort as ReleasePort
from instrument.release import initialize_releases
from records.export import export_result as export_result
from records.readback import get_result as get_result
from storage import connect, transaction
from windows.admission import register as register
from windows.frontier import initialize_frontier
from windows.registrations import initialize_registrations


def initialize_edge(path):
    connection = connect(path)
    try:
        with transaction(connection, write=True):
            initialize_recordings(connection)
            initialize_read_head(connection)
            initialize_releases(connection)
            initialize_registrations(connection)
            initialize_frontier(connection)
            initialize_custody(connection)
            initialize_obligations(connection)
    except BaseException:
        connection.close()
        raise
    return connection
