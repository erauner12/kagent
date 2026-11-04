"""The release controller's exact rendered cards remain usable by the runtime."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from a2a.types import AgentSkill
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError

from kagent.adk.cli import parse_agent_card


@pytest.mark.parametrize("fixture", ["basic_agent", "agent_with_a2a_config", "agent_with_allowed_headers"])
def test_release_controller_card_advertises_only_supported_v1_interfaces(fixture):
    root = Path(__file__).resolve().parents[5]
    rendered = json.loads(
        (root / "go/core/internal/controller/translator/agent/testdata/outputs" / f"{fixture}.json").read_text()
    )
    documents = [
        value["agent-card.json"]
        for resource in rendered["manifest"]
        for value in (resource.get("data", {}), resource.get("stringData", {}))
        if "agent-card.json" in value
    ]
    assert len(documents) == 1
    original = json.loads(documents[0])
    admitted = copy.deepcopy(original)
    card = MessageToDict(parse_agent_card(admitted))
    assert admitted == original
    assert card["name"] == original["name"]
    assert card.get("skills", []) == [
        MessageToDict(ParseDict(skill, AgentSkill())) for skill in original.get("skills", [])
    ]
    assert card["supportedInterfaces"] == [
        item for item in original["supportedInterfaces"] if item["protocolVersion"] == "1.0"
    ]


def test_legacy_card_without_reviewed_v1_interface_is_not_upgraded():
    with pytest.raises(ValueError, match="1.0 interface"):
        parse_agent_card({"name": "agent", "url": "http://agent:8080", "protocolVersion": "0.3"})


def test_unknown_card_fields_still_refuse():
    with pytest.raises(ParseError):
        parse_agent_card({"name": "agent", "inventedField": True})


@pytest.mark.parametrize(
    "path",
    sorted((Path(__file__).resolve().parents[5] / "python/samples").rglob("agent-card.json"))
    + sorted((Path(__file__).resolve().parents[5] / "go/core/test/e2e/agents").rglob("agent-card.json")),
)
def test_sample_cards_declare_native_v1_interfaces(path):
    card = parse_agent_card(json.loads(path.read_text()))
    assert card.supported_interfaces
    assert all(item.protocol_version == "1.0" for item in card.supported_interfaces)


@pytest.mark.parametrize(
    "path",
    sorted((Path(__file__).resolve().parents[5] / "python/samples").rglob("agent.yaml"))
    + [Path(__file__).resolve().parents[5] / "go/core/test/e2e/agents/kebab/agent.yaml"],
)
def test_python_byo_manifests_select_native_v1_upstream(path):
    agent = yaml.safe_load(path.read_text())
    assert agent["spec"]["type"] == "BYO"
    assert agent["metadata"]["annotations"]["kagent.dev/a2a-protocol-version"] == "1.0"
