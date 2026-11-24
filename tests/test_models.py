"""Phase 4 models: the three provider clients against the fake model server, generate()'s retry and
fallback policy, token estimates, and cost calculation. No real API is called."""

import dataclasses
import json

import httpx
import pytest

import fake_gemini
from app.models import gemini, generate, groq, ollama, pricing, tokens
from app.models.base import ProviderError, QuotaError

SOURCES = "Sources:\n\n[1] (a.pdf, chunk 0)\nThe fee is Rs. 300.\n\nQuestion: fee?"


def use_settings(monkeypatch, module, **changes):
    """Settings is frozen, so give the provider module a changed copy."""
    monkeypatch.setattr(module, "settings", dataclasses.replace(module.settings, **changes))


@pytest.fixture
def server(monkeypatch):
    srv = fake_gemini.start(0)
    monkeypatch.setattr(fake_gemini, "PAUSE_SECONDS", 0.01)
    fake_gemini.REQUESTS.clear()
    url = f"http://127.0.0.1:{srv.server_port}"
    use_settings(monkeypatch, gemini, gemini_base_url=url, gemini_api_key="test-key")
    use_settings(monkeypatch, groq, groq_base_url=url, groq_api_key="test-key")
    use_settings(monkeypatch, ollama, ollama_url=url)
    yield srv
    srv.shutdown()


def collect(stream):
    pieces = list(stream)
    return "".join(p.get("text", "") for p in pieces), pieces[-1]["usage"]


# ---------- provider clients ----------
@pytest.mark.real_models
@pytest.mark.parametrize("provider", [gemini, groq, ollama])
def test_provider_streams_pieces_and_usage(server, provider):
    pieces = list(provider.stream("system", SOURCES))
    texts = [p["text"] for p in pieces if "text" in p]
    assert len(texts) >= 3 and "".join(texts).strip().endswith("[1]")
    assert pieces[-1]["usage"]["output_tokens"] > 0 and pieces[-1]["usage"]["input_tokens"] > 0


@pytest.mark.real_models
@pytest.mark.parametrize("provider", [gemini, groq])
def test_provider_without_usage_returns_none(server, provider):
    _, usage = collect(provider.stream("s", SOURCES + " FAULT_NO_USAGE"))
    assert usage is None


@pytest.mark.real_models
def test_ollama_without_prompt_count_leaves_input_unknown(server):
    _, usage = collect(ollama.stream("s", SOURCES + " NO_PROMPT_COUNT"))
    assert usage["input_tokens"] is None and usage["output_tokens"] > 0


@pytest.mark.real_models
@pytest.mark.parametrize("provider,key", [(gemini, "gemini_api_key"), (groq, "groq_api_key")])
def test_missing_key_is_reported_without_a_request(monkeypatch, provider, key):
    use_settings(monkeypatch, provider, **{key: ""})
    with pytest.raises(ProviderError) as err:
        list(provider.stream("s", "p"))
    assert err.value.code == "no_key" and not err.value.retryable and err.value.fallback


@pytest.mark.real_models
@pytest.mark.parametrize("provider", [gemini, groq])
def test_server_error_is_retryable(server, provider):
    with pytest.raises(ProviderError) as err:
        list(provider.stream("s", "q FAULT_500"))
    assert err.value.code == "server_error" and err.value.retryable


@pytest.mark.real_models
def test_gemini_daily_and_per_minute_quota(server, monkeypatch):
    with pytest.raises(QuotaError) as err:
        list(gemini.stream("s", "q FAULT_DAILY"))
    assert err.value.code == "quota_daily" and not err.value.retryable
    with pytest.raises(QuotaError) as err:
        list(gemini.stream("s", "q2 FAULT_RATE_ONCE"))
    assert err.value.code == "quota_rate" and err.value.retryable and err.value.retry_after == 0.05


@pytest.mark.real_models
def test_groq_daily_and_per_minute_quota(server):
    with pytest.raises(QuotaError) as err:
        list(groq.stream("s", "q FAULT_DAILY"))
    assert err.value.code == "quota_daily" and not err.value.retryable
    with pytest.raises(QuotaError) as err:
        list(groq.stream("s", "q3 FAULT_RATE_ONCE"))
    assert err.value.code == "quota_rate" and err.value.retry_after == 0.05


@pytest.mark.real_models
@pytest.mark.parametrize("provider", [gemini, groq])
def test_dropped_connection_mid_answer_is_a_network_error(server, provider):
    received = []
    with pytest.raises(ProviderError) as err:
        for piece in provider.stream("s", SOURCES + " FAULT_MIDSTREAM"):
            received.append(piece)
    assert err.value.code == "network_error" and err.value.retryable
    assert len(received) >= 1  # some text had already arrived


@pytest.mark.real_models
def test_unreachable_provider_is_network_error_not_quota(monkeypatch):
    use_settings(monkeypatch, gemini, gemini_api_key="k", gemini_base_url="http://127.0.0.1:9")
    with pytest.raises(ProviderError) as err:
        list(gemini.stream("s", "p"))
    assert not isinstance(err.value, QuotaError) and err.value.code == "network_error"
    assert "Cannot reach Gemini" in str(err.value)


@pytest.mark.real_models
def test_local_model_not_pulled(server, monkeypatch):
    use_settings(monkeypatch, ollama, ollama_chat_model="missing-model")
    with pytest.raises(ProviderError) as err:
        list(ollama.stream("s", "p"))
    assert err.value.code == "model_not_found" and "ollama pull missing-model" in str(err.value)


@pytest.mark.real_models
def test_error_messages_never_contain_the_key(server, monkeypatch):
    use_settings(monkeypatch, gemini, gemini_api_key="quota-daily")
    with pytest.raises(QuotaError) as err:
        list(gemini.stream("s", "p"))
    assert "quota-daily" not in str(err.value)


def test_classify_429_reads_retry_hints():
    body = json.dumps(fake_gemini._google_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "45s"))
    assert gemini.classify_429(httpx.Headers(), body) == (
        "rate", 45.0, "GenerateRequestsPerMinutePerProjectPerModel-FreeTier")
    assert gemini.classify_429(httpx.Headers({"retry-after": "7"}), "{}") == ("rate", 7.0, "unknown")


def test_gemini_request_body_has_system_and_no_thinking():
    body = gemini._body("rules", "question")
    assert body["systemInstruction"]["parts"][0]["text"] == "rules"
    if "2.5-flash" in gemini.settings.gemini_model:
        assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


# ---------- generate(): retry once, then fall back to local ----------
def scripted(*outcomes):
    """A fake provider stream that plays one outcome per call: text, or an exception to raise.
    ("partial", exc) yields some text first and then raises exc."""
    calls = []

    def stream(system, prompt, **kwargs):
        outcome = outcomes[min(len(calls), len(outcomes) - 1)]
        calls.append(outcome)
        if isinstance(outcome, tuple):
            yield {"text": "half an ans"}
            raise outcome[1]
        if isinstance(outcome, Exception):
            raise outcome
        yield {"text": outcome}
        yield {"usage": {"input_tokens": 100, "output_tokens": 20}}
    stream.calls = calls
    return stream


@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(generate, "sleep", recorded.append)
    return recorded


def run_generate(model="gemini"):
    events = list(generate.stream(model, "s", "p"))
    return events, events[-1]["done"]


def test_success_first_try(monkeypatch, waits):
    monkeypatch.setattr(gemini, "stream", scripted("hi"))
    events, gen = run_generate()
    assert gen.model == "gemini" and gen.text == "hi" and gen.attempts == [] and gen.fallback is None
    assert (gen.input_tokens, gen.output_tokens, gen.tokens_estimated) == (100, 20, False)
    assert waits == []


def test_transient_error_is_retried_once_then_succeeds(monkeypatch, waits):
    fake = scripted(ProviderError("boom", code="server_error", retryable=True), "hi")
    monkeypatch.setattr(gemini, "stream", fake)
    _, gen = run_generate()
    assert gen.model == "gemini" and len(fake.calls) == 2 and waits == [generate.BACKOFF_SECONDS]
    assert [a["code"] for a in gen.attempts] == ["server_error"]


def test_retry_waits_as_long_as_the_provider_asks(monkeypatch, waits):
    monkeypatch.setattr(gemini, "stream", scripted(QuotaError("rate", "slow down", retry_after=3), "hi"))
    run_generate()
    assert waits == [3]


def test_transient_error_twice_falls_back_to_local(monkeypatch, waits):
    fake = scripted(ProviderError("down", code="server_error", retryable=True))
    monkeypatch.setattr(gemini, "stream", fake)
    monkeypatch.setattr(ollama, "stream", scripted("local answer"))
    events, gen = run_generate()
    assert len(fake.calls) == generate.MAX_TRIES == 2
    assert gen.model == "local" and gen.text == "local answer" and gen.fallback["from"] == "gemini"
    assert any("fallback" in e for e in events)


def test_daily_quota_is_not_retried_and_falls_back(monkeypatch, waits):
    fake = scripted(QuotaError("daily", "used up", retry_after=30))
    monkeypatch.setattr(gemini, "stream", fake)
    monkeypatch.setattr(ollama, "stream", scripted("local answer"))
    _, gen = run_generate()
    assert len(fake.calls) == 1 and waits == [] and gen.model == "local"
    assert gen.fallback["code"] == "quota_daily"


def test_too_long_retry_after_is_not_waited_for(monkeypatch, waits):
    fake = scripted(QuotaError("rate", "wait a minute", retry_after=60))
    monkeypatch.setattr(gemini, "stream", fake)
    monkeypatch.setattr(ollama, "stream", scripted("local answer"))
    _, gen = run_generate()
    assert len(fake.calls) == 1 and waits == [] and gen.model == "local"


def test_partial_answer_is_reset_before_retry(monkeypatch, waits):
    monkeypatch.setattr(gemini, "stream", scripted(("partial", ProviderError("cut", code="network_error",
                                                                             retryable=True)), "full answer"))
    events, gen = run_generate()
    kinds = [next(iter(e)) for e in events]
    assert kinds == ["text", "reset", "text", "done"]
    assert gen.text == "full answer"


def test_safety_refusal_is_not_sent_to_another_model(monkeypatch, waits):
    monkeypatch.setattr(gemini, "stream", scripted(ProviderError("refused", code="safety", fallback=False)))
    local = scripted("should not be called")
    monkeypatch.setattr(ollama, "stream", local)
    with pytest.raises(ProviderError) as err:
        run_generate()
    assert err.value.code == "safety" and local.calls == []


def test_main_local_model_falls_back_to_the_small_one(monkeypatch, waits):
    calls = []

    def stream(system, prompt, model=None):
        calls.append(model)
        if model == ollama.settings.ollama_chat_model:
            raise ProviderError("not pulled", code="model_not_found")
        yield {"text": "small answer"}
        yield {"usage": {"input_tokens": 5, "output_tokens": 2}}
    monkeypatch.setattr(ollama, "stream", stream)
    _, gen = run_generate("local")
    assert gen.model == "local_small" and gen.fallback["from"] == "local" and gen.text == "small answer"
    assert calls == [ollama.settings.ollama_chat_model, ollama.settings.ollama_backup_model]


def test_small_local_model_has_nothing_after_it(monkeypatch, waits):
    monkeypatch.setattr(ollama, "stream", scripted(ProviderError("not pulled", code="model_not_found")))
    with pytest.raises(generate.AllProvidersFailed) as err:
        run_generate("local_small")
    assert [a["model"] for a in err.value.attempts] == ["local_small"]


@pytest.mark.parametrize("model,chain", [
    ("gemini", ["gemini", "local", "local_small"]),
    ("groq", ["groq", "local", "local_small"]),
    ("local", ["local", "local_small"]),
    ("local_small", ["local_small"]),
])
def test_fallback_chains(model, chain):
    assert generate.chain_for(model) == chain


def test_local_only_refuses_cloud_models_and_keeps_the_chain_local():
    assert generate.chain_for("local", local_only=True) == ["local", "local_small"]
    for cloud in ("gemini", "groq"):
        with pytest.raises(generate.LocalOnlyError):
            generate.chain_for(cloud, local_only=True)


def test_local_only_never_calls_a_cloud_provider(monkeypatch, waits):
    cloud_calls = []
    monkeypatch.setattr(gemini, "stream", lambda s, p, **k: cloud_calls.append("gemini") or iter(()))
    monkeypatch.setattr(groq, "stream", lambda s, p, **k: cloud_calls.append("groq") or iter(()))
    monkeypatch.setattr(ollama, "stream", scripted(ProviderError("down", code="network_error", retryable=True)))
    with pytest.raises(generate.AllProvidersFailed) as err:
        list(generate.stream("local", "s", "p", local_only=True))
    assert cloud_calls == [] and {a["model"] for a in err.value.attempts} == {"local", "local_small"}


def test_everything_failing_lists_every_attempt(monkeypatch, waits):
    monkeypatch.setattr(groq, "stream", scripted(ProviderError("down", code="server_error", retryable=True)))
    monkeypatch.setattr(ollama, "stream", scripted(ProviderError("no ollama", code="network_error", retryable=True)))
    with pytest.raises(generate.AllProvidersFailed) as err:
        run_generate("groq")
    assert [(a["model"], a["try"]) for a in err.value.attempts] == [
        ("groq", 1), ("groq", 2), ("local", 1), ("local", 2), ("local_small", 1), ("local_small", 2)]
    assert err.value.code == "all_providers_failed"


def test_missing_token_counts_are_estimated_and_labelled(monkeypatch, waits):
    def no_usage(system, prompt):
        yield {"text": "Refunds take five working days."}
        yield {"usage": None}
    monkeypatch.setattr(gemini, "stream", no_usage)
    _, gen = run_generate()
    assert gen.tokens_estimated is True and gen.output_tokens == tokens.estimate("Refunds take five working days.")
    assert gen.input_tokens == tokens.estimate("s\np")


def test_generate_non_streaming_returns_text_tokens_and_time(monkeypatch):
    monkeypatch.setattr(gemini, "stream", scripted("hello"))
    gen = generate.generate("gemini", "s", "p")
    assert gen.text == "hello" and gen.input_tokens == 100 and gen.response_ms >= 0


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        list(generate.stream("gpt-5", "s", "p"))


# ---------- tokens and cost ----------
def test_token_estimate_counts_words_and_indic_text():
    assert 1 <= tokens.estimate("hello world") <= 3
    assert tokens.estimate("") == 0
    assert tokens.estimate("రిఫండ్ పాలసీ ఏమిటి") > 3  # Indic text costs many tokens


def test_cost_uses_the_price_table():
    price = pricing.price_of("gemini-2.5-flash")
    c = pricing.cost("gemini-2.5-flash", 1_000_000, 1_000_000)
    assert c["usd"] == pytest.approx(price["input_per_million"] + price["output_per_million"])
    assert c["inr"] == pytest.approx(c["usd"] * pricing.settings.usd_to_inr, rel=1e-6)
    assert c["priced"] and c["api_fee"]


def test_small_cost_keeps_precision():
    c = pricing.cost("gemini-2.5-flash", 300, 50)
    assert c["usd"] == pytest.approx((300 * 0.30 + 50 * 2.50) / 1e6)


def test_local_model_has_no_api_fee():
    assert pricing.cost("qwen2.5:3b", 5000, 500, local=True) == {"usd": 0.0, "inr": 0.0, "priced": True, "api_fee": False}


def test_unpriced_model_gets_no_cost_not_zero():
    c = pricing.cost("llama-3.3-70b-versatile", 1000, 100)
    assert c["usd"] is None and c["inr"] is None and c["priced"] is False


def test_price_table_has_a_check_date_and_sources():
    t = pricing.table()
    assert t["checked_on"] and t["currency"] == "USD"
    for model_id, p in t["models"].items():
        assert "input_per_million" in p and "output_per_million" in p, model_id
        assert p.get("local") or p.get("source"), model_id


def test_comparison_prices_the_same_tokens_on_every_model():
    rows = pricing.comparison([("gemini", "gemini-2.5-flash", False), ("groq", "llama-3.3-70b-versatile", False),
                               ("local", "qwen2.5:3b", True)], 1000, 100)
    by = {r["model"]: r for r in rows}
    assert by["gemini"]["usd"] > 0 and by["groq"]["priced"] is False and by["local"]["api_fee"] is False


def test_default_groq_model_is_priced():
    c = pricing.cost("openai/gpt-oss-120b", 1_000_000, 1_000_000)
    assert c["usd"] == pytest.approx(0.15 + 0.60) and c["priced"] is True


# ---------- local models ----------
def test_thinking_blocks_are_never_streamed():
    state = {"inside": False}
    pieces = ["<think>", "Let me check the", " sources.</think>", "80 percent", " is refunded [1]."]
    assert "".join(ollama._drop_thinking(p, state) for p in pieces) == "80 percent is refunded [1]."
    assert ollama._drop_thinking("Plain answer.", {"inside": False}) == "Plain answer."


@pytest.mark.real_models
def test_qwen3_is_asked_not_to_think(server, monkeypatch):
    seen = {}
    real_stream = ollama.httpx.stream

    def spy(method, url, json=None, **kw):
        seen.update(json)
        return real_stream(method, url, json=json, **kw)
    monkeypatch.setattr(ollama.httpx, "stream", spy)
    list(ollama.stream("s", SOURCES, model="qwen3:14b"))
    assert seen["think"] is False
    seen.clear()
    list(ollama.stream("s", SOURCES, model="qwen2.5:3b"))
    assert "think" not in seen


@pytest.mark.real_models
def test_local_replies_have_an_output_cap(server, monkeypatch):
    """Phase 7.1: qwen2.5:3b once looped for 40,960 tokens (11 minutes) on a Telugu question."""
    seen = {}
    real_stream = ollama.httpx.stream

    def spy(method, url, json=None, **kw):
        seen.update(json)
        return real_stream(method, url, json=json, **kw)
    monkeypatch.setattr(ollama.httpx, "stream", spy)
    list(ollama.stream("s", SOURCES, model="qwen2.5:3b"))
    assert seen["options"]["num_predict"] == 1536


def test_two_local_models_are_configured():
    assert generate.PROVIDERS["local"].model_id() == ollama.settings.ollama_chat_model
    assert generate.PROVIDERS["local_small"].model_id() == ollama.settings.ollama_backup_model
    assert generate.is_local("local") and generate.is_local("local_small") and not generate.is_local("groq")
