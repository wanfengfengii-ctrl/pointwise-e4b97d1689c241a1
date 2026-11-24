import json
from decimal import Decimal

from instrument.frames import Frame
from records.readback import get_result


def _integer(value: int) -> str:
    # Decimal(int) preserves the complete integer without Python's int-to-string digit cap.
    return str(Decimal(value))


def _frame_json(frame: Frame) -> str:
    return (
        f'{{"number":{_integer(frame.number)},"value":{_integer(frame.value)},'
        f'"origin":{json.dumps(frame.origin)}}}'
    )


def export_result(store, result_id: int) -> str:
    published = get_result(store, result_id)
    artifact, receipt = published.artifact, published.receipt
    frames = ','.join(_frame_json(frame) for frame in artifact.frames)
    receipt_json = json.dumps({
        'recording_id': receipt.recording_id,
        'window_id': receipt.window_id,
        'result_id': receipt.result_id,
    }, separators=(',', ':'))
    return (
        f'{{"result_id":{artifact.result_id},"recording_id":{json.dumps(artifact.recording_id)},'
        f'"window_id":{json.dumps(artifact.window_id)},"unit":"device_count",'
        f'"peak_to_peak":{_integer(artifact.peak_to_peak)},"frames":[{frames}],'
        f'"receipt":{receipt_json}}}\n'
    )
