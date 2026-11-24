from dataclasses import dataclass

from records.catalog import Artifact, find_artifact
from records.receipts import Receipt, find_result_receipt
from storage import ContractError, NotFound


@dataclass(frozen=True, slots=True)
class PublishedResult:
    artifact: Artifact
    receipt: Receipt


def read_published(connection, result_id: int) -> PublishedResult:
    artifact = find_artifact(connection, result_id)
    receipt = find_result_receipt(connection, result_id)
    if artifact is None or receipt is None:
        raise NotFound(f'unknown published result {result_id}')
    return PublishedResult(artifact, receipt)


def get_result(store, result_id: int) -> PublishedResult:
    if type(result_id) is not int or result_id <= 0:
        raise ContractError('result ID must be a positive integer')
    if result_id >= 1 << 63:
        raise NotFound('result ID is outside the archive catalog')
    with store.read_snapshot() as connection:
        return read_published(connection, result_id)
