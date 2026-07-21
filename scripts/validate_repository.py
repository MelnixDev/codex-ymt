#!/usr/bin/env python3
"""Dependency-free validation for repository plugin, skill, and marketplace metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from youtube_mcp import SERVER_VERSION, tool_definitions


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "codex-ymt"
SKILL_NAME = "localize-youtube-video"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_object(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), f"{relative_path} must contain a JSON object")
    return payload


def validate_plugin() -> None:
    manifest = load_object(".codex-plugin/plugin.json")
    require(manifest.get("name") == PLUGIN_NAME, "plugin name does not match the repository")
    require(manifest.get("version") == SERVER_VERSION, "plugin and server versions differ")
    require(SEMVER.fullmatch(SERVER_VERSION) is not None, "release version must be strict semver")
    require(manifest.get("skills") == "./skills/", "plugin skills path is invalid")
    require(manifest.get("mcpServers") == "./.mcp.json", "plugin MCP path is invalid")
    require("[TODO:" not in json.dumps(manifest), "plugin manifest contains a TODO placeholder")

    interface = manifest.get("interface")
    require(isinstance(interface, dict), "plugin interface must be an object")
    for field in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "capabilities",
        "defaultPrompt",
    ):
        require(bool(interface.get(field)), f"plugin interface.{field} is required")

    mcp = load_object(".mcp.json")
    server = mcp.get("mcpServers", {}).get(PLUGIN_NAME, {})
    require(server.get("command") == "python3", "MCP command must use python3")
    require(
        server.get("args") == ["${PLUGIN_ROOT}/scripts/youtube_mcp.py"],
        "MCP server path is invalid",
    )

    tools = tool_definitions()
    names = [tool.get("name") for tool in tools]
    require(len(names) == 13, "the public MCP tool count changed")
    require(len(names) == len(set(names)), "MCP tool names must be unique")


def validate_skill() -> None:
    path = ROOT / "skills" / SKILL_NAME / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", content, re.DOTALL)
    require(match is not None, "skill YAML frontmatter is missing or invalid")

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        require(bool(separator), f"invalid skill frontmatter line: {line!r}")
        fields[key.strip()] = value.strip()
    require(set(fields) == {"name", "description"}, "skill frontmatter fields changed")
    require(fields["name"] == SKILL_NAME, "skill name does not match its directory")
    require(SKILL_NAME_PATTERN.fullmatch(fields["name"]) is not None, "skill name is invalid")
    require(0 < len(fields["description"]) <= 1024, "skill description length is invalid")
    require("<" not in fields["description"] and ">" not in fields["description"], "skill description contains angle brackets")


def validate_marketplace() -> None:
    marketplace = load_object(".agents/plugins/marketplace.json")
    require(marketplace.get("name") == PLUGIN_NAME, "marketplace name is invalid")
    plugins = marketplace.get("plugins")
    require(isinstance(plugins, list) and len(plugins) == 1, "marketplace must contain one plugin")
    entry = plugins[0]
    require(entry.get("name") == PLUGIN_NAME, "marketplace plugin name is invalid")
    require(
        entry.get("source")
        == {"source": "url", "url": "https://github.com/MelnixDev/codex-ymt.git"},
        "marketplace source is invalid",
    )
    require(
        entry.get("policy")
        == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "marketplace policy is invalid",
    )
    require(entry.get("category") == "Productivity", "marketplace category is invalid")


def main() -> None:
    validate_plugin()
    print("Plugin metadata: valid")
    validate_skill()
    print("Skill metadata: valid")
    validate_marketplace()
    print("Marketplace metadata: valid")


if __name__ == "__main__":
    main()
