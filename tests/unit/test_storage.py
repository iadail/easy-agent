from pathlib import Path

from agent_integrations.storage import SQLiteRunStore


def test_sqlite_run_store_persists_trace(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path, "state.db")
    store.create_run("run_1", "baseline", {"input": "hello"})
    store.record_node("run_1", "node_1", "succeeded", 1, {"value": 1}, None)
    store.record_event("run_1", "custom", {"value": 2})
    store.finish_run("run_1", "succeeded", {"result": "ok"})

    trace = store.load_trace("run_1")

    assert trace["status"] == "succeeded"
    assert trace["nodes"][0]["node_id"] == "node_1"
    assert trace["events"][0]["kind"] == "custom"


