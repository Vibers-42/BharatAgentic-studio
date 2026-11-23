"""Static checks on the Phase 1 setup files: compose services, networks, health checks,
published ports, and that secrets stay out of git. Needs no Docker."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]


def test_exactly_five_services():
    assert set(SERVICES) == {"web", "api", "db", "mcp-runner", "ollama"}


def test_mcp_runner_shares_no_network_with_db():
    assert not set(SERVICES["mcp-runner"]["networks"]) & set(SERVICES["db"]["networks"])


def test_api_reaches_db_and_mcp_runner():
    api = set(SERVICES["api"]["networks"])
    assert api & set(SERVICES["db"]["networks"])
    assert api & set(SERVICES["mcp-runner"]["networks"])


def test_api_waits_for_healthy_db():
    assert SERVICES["api"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert "healthcheck" in SERVICES["db"]


def test_every_dependency_has_a_healthcheck():
    for name, svc in SERVICES.items():
        for dep, rule in svc.get("depends_on", {}).items():
            if rule["condition"] == "service_healthy":
                assert "healthcheck" in SERVICES[dep], f"{name} waits on {dep} without a healthcheck"


def test_db_port_is_not_published():
    assert "ports" not in SERVICES["db"]


def test_published_ports_bind_to_localhost_only():
    for name, svc in SERVICES.items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} on all interfaces"


def test_images_are_pinned():
    for name, svc in SERVICES.items():
        if "image" in svc:
            assert not svc["image"].endswith(":latest") and ":" in svc["image"], name


def test_uploads_live_in_a_docker_volume():
    assert any(v.startswith("uploads:") for v in SERVICES["api"]["volumes"])


def test_gitignore_excludes_env_but_keeps_example():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines
    assert "!.env.example" in lines


def test_env_example_lists_every_key():
    keys = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    required = {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "GEMINI_API_KEY", "GROQ_API_KEY",
                "OLLAMA_CHAT_MODEL", "OLLAMA_EMBED_MODEL"}
    assert required <= keys


def test_env_example_has_no_real_keys():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.endswith("_API_KEY=") or line.endswith("_TOKEN="):
            continue
        assert "_API_KEY=" not in line and "_TOKEN=" not in line, f"value filled in: {line}"
