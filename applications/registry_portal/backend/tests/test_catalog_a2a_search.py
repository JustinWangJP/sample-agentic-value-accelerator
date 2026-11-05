"""A2A の条件が上位20件を決める前に適用されることを Seam 1 で検証する。"""

import json
from collections.abc import Mapping
from typing import Any

import pytest

from registry_portal.services.http import TransportResponse

from catalog_helpers import (
    SEARCH_URL,
    FakeTransport,
    SentRequest,
    call,
    json_response,
    make_handler,
    search_event,
    summary,
)

A2A_READY = {"A2A_SEARCH_ENABLED": "true"}


def agent(record_id: str, a2a: bool, *, indexed_a2a: bool | None = None) -> dict[str, Any]:
    """descriptor と、検索用の写し（customMetadata.isA2A）を持つ Agent。"""
    record = summary(
        record_id,
        descriptors={"a2aAgentCard" if a2a else "http": {"data": "{}"}},
        customMetadata={
            "isA2A": a2a if indexed_a2a is None else indexed_a2a,
            "dataClassification": "private",
        },
    )
    del record["descriptorTypes"]
    return record


class RankedSearchTransport(FakeTransport):
    """AWS と同様にフィルターを適用してから順位順の上限件数を返す。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__()
        self.records = records

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> TransportResponse:
        if url != SEARCH_URL:
            return super().__call__(method, url, headers, body, timeout)
        self.requests.append(SentRequest(method, url, dict(headers), body))
        assert body is not None
        payload = json.loads(body)
        filters = payload["filters"]
        conditions = filters.get("$and", [filters])
        records = self.records
        for condition in conditions:
            for field, comparison in condition.items():

                def value(record: dict[str, Any]) -> Any:
                    if field.startswith("customMetadata."):
                        return record["customMetadata"].get(field.removeprefix("customMetadata."))
                    return record.get(field)

                records = [record for record in records if value(record) == comparison["$eq"]]
        return json_response(200, {"registryRecords": records[: payload["maxResults"]]})


@pytest.mark.parametrize("a2a", [True, False])
def test_a2a_filter_finds_matches_beyond_twenty_other_agents(a2a: bool) -> None:
    records = [agent(f"other-{index}", not a2a) for index in range(25)]
    records.extend(agent(f"match-{index:02d}", a2a) for index in range(23))
    transport = RankedSearchTransport(records)

    status, body = call(
        make_handler(transport, env=A2A_READY),
        search_event({"query": "受注を処理したい", "a2a": a2a, "dataClassification": "private"}),
    )

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == [
        f"match-{index:02d}" for index in range(20)
    ]
    assert body["inconsistentA2ACount"] == 0
    (request,) = transport.to(SEARCH_URL)
    assert request.json()["filters"] == {
        "$and": [
            {"recordType": {"$eq": "AGENT"}},
            {"customMetadata.isA2A": {"$eq": a2a}},
            {"customMetadata.dataClassification": {"$eq": "private"}},
        ],
    }


@pytest.mark.parametrize("a2a", [True, False])
def test_records_whose_search_metadata_disagrees_with_the_descriptor_are_excluded_and_counted(
    a2a: bool,
) -> None:
    # 検索用の写しが誤っていても、descriptor を正として扱う。ほかの結果は返す
    records = [
        agent("consistent-1", a2a),
        agent("stale", not a2a, indexed_a2a=a2a),
        agent("consistent-2", a2a),
    ]
    transport = RankedSearchTransport(records)

    status, body = call(
        make_handler(transport, env=A2A_READY), search_event({"query": "受注", "a2a": a2a})
    )

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == ["consistent-1", "consistent-2"]
    assert all(item["isA2A"] is a2a for item in body["items"])
    assert body["inconsistentA2ACount"] == 1


def test_a2a_search_schema_error_is_not_reported_as_no_matches() -> None:
    transport = FakeTransport()
    transport.registry.append(json_response(400, {"message": "Unknown custom metadata field"}))

    status, body = call(
        make_handler(transport, env=A2A_READY), search_event({"query": "受注", "a2a": True})
    )

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"
