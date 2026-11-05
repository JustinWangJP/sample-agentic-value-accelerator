"""A2A の条件が上位20件を決める前に適用されることを Seam 1 で検証する。"""

import json
from collections.abc import Mapping
from typing import Any

import pytest

from registry_portal.services.http import TransportResponse

from test_catalog_discovery import SEARCH_URL, SearchTransport, search_event
from test_catalog_list import SentRequest, call, json_response, make_handler, summary


def agent(record_id: str, a2a: bool) -> dict[str, Any]:
    record = summary(
        record_id,
        descriptors={"a2aAgentCard" if a2a else "http": {"data": "{}"}},
        customMetadata={"isA2A": a2a, "dataClassification": "private"},
    )
    del record["descriptorTypes"]
    return record


class RankedSearchTransport(SearchTransport):
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
        make_handler(transport),
        search_event({"query": "受注を処理したい", "a2a": a2a, "dataClassification": "private"}),
    )

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == [
        f"match-{index:02d}" for index in range(20)
    ]
    assert len(transport.to(SEARCH_URL)) == 1


@pytest.mark.parametrize("metadata", [{}, {"isA2A": False}, {"isA2A": "true"}, {"isA2A": 1}])
def test_a2a_search_rejects_missing_or_inconsistent_metadata(metadata: dict[str, Any]) -> None:
    record = agent("inconsistent", True)
    record["customMetadata"] = metadata
    transport = SearchTransport()
    transport.registry.append(json_response(200, {"registryRecords": [record]}))

    status, body = call(make_handler(transport), search_event({"query": "受注", "a2a": True}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"


def test_a2a_search_rejects_descriptors_that_disagree_with_the_filter() -> None:
    record = agent("inconsistent", False)
    record["customMetadata"]["isA2A"] = True
    transport = SearchTransport()
    transport.registry.append(json_response(200, {"registryRecords": [record]}))

    status, body = call(make_handler(transport), search_event({"query": "受注", "a2a": True}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"


def test_a2a_search_schema_error_is_not_reported_as_no_matches() -> None:
    transport = SearchTransport()
    transport.registry.append(json_response(400, {"message": "Unknown custom metadata field"}))

    status, body = call(make_handler(transport), search_event({"query": "受注", "a2a": True}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"
