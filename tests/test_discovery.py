from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from research_harness.config import SourceSpec
from research_harness.discovery import (
    Candidate,
    DataNeed,
    Discovery,
    ProposalDraft,
    compile_proposal,
)


def candidate(source, probe_id="probe-1"):
    return Candidate(
        name="Official feed",
        purpose="Track official policy changes",
        covers=["policy"],
        status="ready",
        evidence_urls=[source.url],
        source=source,
        probe_id=probe_id,
        freshness_assessment="Publication is irregular; collect hourly",
        historical_coverage="Current feed window only",
        access_notes="Public GET sample succeeded",
        limitations=["Feed entries can be excerpts"],
    )


def draft(source, probe_id="probe-1"):
    return ProposalDraft(
        name="policy-monitor",
        title="Policy monitor",
        research_question="Collect official policy evidence",
        needs=[DataNeed(id="policy", description="Official policy changes", required=True)],
        candidates=[candidate(source, probe_id)],
        open_questions=[],
    )


def test_proposal_cannot_claim_verified_without_probe(source):
    with pytest.raises(ValueError, match="probe"):
        compile_proposal(draft(source), {}, {source.url})


def test_changed_configuration_invalidates_probe(source):
    proof = {"probe-1": {"status": "verified_sample", "source_fingerprint": source.fingerprint()}}
    altered = source.model_copy(update={"items_pointer": "/other"})
    with pytest.raises(ValueError, match="changed after probing"):
        compile_proposal(draft(altered), proof, {source.url})


def test_unsupported_sources_remain_visible_without_executable_pipeline(source):
    value = draft(source)
    value.candidates[0].status = "needs_access"
    value.candidates[0].source = None
    value.candidates[0].probe_id = None
    pipeline, gaps = compile_proposal(value, {}, {source.url})
    assert pipeline is None
    assert gaps == ["policy"]


def test_unobserved_citations_are_rejected(source):
    with pytest.raises(ValueError, match="actually observed"):
        compile_proposal(draft(source), {}, set())


def api_response(output, number):
    return {
        "id": f"resp_{number}",
        "object": "response",
        "created_at": 1788782400,
        "status": "completed",
        "model": "gpt-5.4-mini",
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


@pytest.mark.parametrize("repair_first", [False, True])
def test_actual_sdk_tool_loop_probes_and_compiles_a_pipeline(tmp_path, rss, repair_first):
    source = SourceSpec(
        id="official-policy",
        name="Official policy feed",
        connector="rss",
        url="https://source.example/feed",
    )
    requests = []

    def model_handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert data["store"] is False
        assert data["text"]["format"]["strict"] is True
        for entry in data["input"]:
            assert "parsed_arguments" not in entry
            if isinstance(entry.get("content"), list):
                assert all("parsed" not in content for content in entry["content"])
        if len(requests) == 1:
            if repair_first:
                return httpx.Response(
                    200,
                    json=api_response(
                        [
                            {
                                "id": "ws_1",
                                "type": "web_search_call",
                                "status": "completed",
                                "action": {
                                    "type": "search",
                                    "query": "official policy feed",
                                    "sources": [{"type": "url", "url": source.url}],
                                },
                            },
                            {
                                "id": "msg_unverified",
                                "type": "message",
                                "status": "completed",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": draft(source).model_dump_json(),
                                        "annotations": [],
                                    }
                                ],
                            },
                        ],
                        1,
                    ),
                )
            return httpx.Response(
                200,
                json=api_response(
                    [
                        {
                            "id": "ws_1",
                            "type": "web_search_call",
                            "status": "completed",
                            "action": {
                                "type": "search",
                                "query": "official policy feed",
                                "sources": [{"type": "url", "url": source.url}],
                            },
                        },
                        {
                            "id": "fc_1",
                            "type": "function_call",
                            "name": "probe_source",
                            "call_id": "call_1",
                            "status": "completed",
                            "arguments": json.dumps({"source_json": source.model_dump_json()}),
                        },
                    ],
                    1,
                ),
            )
        if repair_first and len(requests) == 2:
            assert any(
                "Application validation rejected" in entry.get("content", "")
                for entry in data["input"]
                if isinstance(entry.get("content"), str)
            )
            return httpx.Response(
                200,
                json=api_response(
                    [
                        {
                            "id": "fc_1",
                            "type": "function_call",
                            "name": "probe_source",
                            "call_id": "call_1",
                            "status": "completed",
                            "arguments": json.dumps({"source_json": source.model_dump_json()}),
                        }
                    ],
                    2,
                ),
            )
        tool_output = next(
            item for item in data["input"] if item.get("type") == "function_call_output"
        )
        proof = json.loads(tool_output["output"])
        assert proof["status"] == "verified_sample"
        answer = draft(source, proof["probe_id"])
        return httpx.Response(
            200,
            json=api_response(
                [
                    {
                        "id": "msg_2",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": answer.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                2,
            ),
        )

    with (
        httpx.Client(transport=httpx.MockTransport(model_handler)) as model_http,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, content=rss, headers={"content-type": "application/rss+xml"}
                )
            )
        ) as source_http,
    ):
        with OpenAI(
            api_key="test-only-not-a-real-key", http_client=model_http, max_retries=0
        ) as client:
            result = Discovery(
                tmp_path / "discovery", client=client, http_client=source_http, public_only=False
            ).run("Find official policy sources")
    assert result["status"] == "proposed"
    assert result["verified_sources"] == 1
    rounds = 3 if repair_first else 2
    assert result["usage"] == {"input_tokens": 100 * rounds, "output_tokens": 50 * rounds}
    pipeline = json.loads((tmp_path / "discovery/pipeline.json").read_text())
    assert pipeline["sources"][0]["url"] == source.url
    assert (tmp_path / "discovery/probes.json").exists()
    assert "test-only-not-a-real-key" not in (tmp_path / "discovery/trace.jsonl").read_text()
    assert len(requests) == rounds


def test_round_limit_preserves_failure_and_never_creates_fake_pipeline(tmp_path, source):
    response = SimpleNamespace(
        id="r", status="completed", usage=None, output=[], output_parsed=draft(source)
    )
    client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs: response))
    with pytest.raises(RuntimeError, match="round limit"):
        Discovery(tmp_path, client=client, max_rounds=2).run("Research")
    assert (tmp_path / "failure.json").exists()
    assert not (tmp_path / "pipeline.json").exists()


def test_unknown_tool_cannot_execute_commands(tmp_path):
    discovery = Discovery(tmp_path, client=None)
    result = discovery.tool("execute_shell", '{"command":"touch bad"}')
    assert result["status"] == "error"
    assert not (tmp_path / "bad").exists()
