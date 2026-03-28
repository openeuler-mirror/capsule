import json


def emit_stage_payload(stage: str, output: dict, *, run_id: str | None = None, output_dir: str | None = None) -> None:
    payload = {"stage": stage, "output": output}
    if run_id is not None:
        payload["run_id"] = run_id
    if output_dir is not None:
        payload["output_dir"] = output_dir
    print("\nCurrent task run status. Please use run_id and output_dir for any follow-up actions:")
    print(json.dumps(payload, ensure_ascii=False))
