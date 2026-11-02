from __future__ import annotations

import json
from dataclasses import replace

import pytest

from pi_rsi import http, judges
from pi_rsi.config import GeneratorConfig, JudgeConfig
from pi_rsi.generators import GeneratorError, parse_output
from pi_rsi.generators.chat import ChatCompletionsGenerator
from pi_rsi.generators.pi import PiGenerator


def test_noul_and_choice_shapes() -> None:
    assert judges.noul("is it true") == {
        "type": "noul",
        "instructions": "is it true",
        "true": "yes",
        "false": "no",
    }
    assert judges.choice("pick", ["a", "b"])["options"] == ["a", "b"]
    with pytest.raises(ValueError):
        judges.choice("pick", [])


def test_validate_accepts_good_answers() -> None:
    questions = {"p": judges.noul("x"), "c": judges.choice("y", ["a", "b"])}
    answers = judges.validate(questions, {"answers": {"p": 0.25, "c": "b"}})
    assert answers == {"p": 0.25, "c": "b"}


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"answers": {"p": 0.5}}, "missing answer"),
        ({"answers": {"p": 1.5, "c": "a"}}, "outside"),
        ({"answers": {"p": "yes", "c": "a"}}, "must be a number"),
        ({"answers": {"p": 0.5, "c": "z"}}, "not one of"),
        ({"answers": {"p": True, "c": "a"}}, "must be a number"),
        ("nope", "expected an object"),
    ],
)
def test_validate_rejects_bad_answers(raw, message) -> None:
    questions = {"p": judges.noul("x"), "c": judges.choice("y", ["a", "b"])}
    with pytest.raises(judges.JudgeError, match=message):
        judges.validate(questions, raw)


def test_parse_json_object_from_chatty_text() -> None:
    text = 'Sure.\n```json\n{"answers": {"p": 0.9}}\n```\n'
    assert judges.base.parse_json_object(text) == {"answers": {"p": 0.9}}
    with pytest.raises(judges.JudgeError):
        judges.base.parse_json_object("no json here")


def test_build_returns_none_for_disabled_judge() -> None:
    assert judges.build(JudgeConfig(provider="none")) is None
    judge = judges.build(JudgeConfig(provider="systemone", base_url="http://x.test"))
    assert judge is not None and judge.name == "systemone"


def test_systemone_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url, payload, headers=None, timeout=30):
        captured.update(url=url, payload=payload, headers=headers)
        return {
            "answers": {
                "p": {"type": "noul", "noul": 0.75},
                "s": {
                    "type": "choice",
                    "choice": "project",
                    "probabilities": {"project": 0.8, "global": 0.2},
                    "confidence": 0.6,
                },
            }
        }

    monkeypatch.setattr(http, "post_json", fake_post)
    cfg = JudgeConfig(
        provider="systemone", base_url="http://judge.test/", path="/v3/decisions", model="m"
    )
    answers = judges.build(cfg).ask(
        {
            "p": judges.noul("x", true="is", false="is not"),
            "s": judges.choice("y", ["project", "global"]),
        }
    )
    assert answers == {"p": 0.75, "s": "project"}
    assert captured["url"] == "http://judge.test/v3/decisions"
    payload = captured["payload"]
    assert payload["model"] == "m"
    assert payload["state"] == {"source": "pi-rsi"}
    assert payload["questions"]["p"] == {
        "type": "noul",
        "instructions": "x",
        "criteria": {"true": "is", "false": "is not"},
    }
    assert payload["questions"]["s"]["criteria"] == {"project": "project", "global": "global"}


def test_systemone_rejects_a_choice_that_is_not_the_most_probable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url, payload, headers=None, timeout=30):
        return {
            "answers": {
                "s": {
                    "type": "choice",
                    "choice": "global",
                    "probabilities": {"project": 0.9, "global": 0.1},
                    "confidence": 0.9,
                }
            }
        }

    monkeypatch.setattr(http, "post_json", fake_post)
    judge = judges.build(JudgeConfig(provider="systemone", base_url="http://judge.test"))
    with pytest.raises(judges.JudgeError, match="highest-probability"):
        judge.ask({"s": judges.choice("y", ["project", "global"])})


def test_systemone_http_error_becomes_judge_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise http.HttpError("HTTP 500", status=500)

    monkeypatch.setattr(http, "post_json", boom)
    judge = judges.build(JudgeConfig(provider="systemone", base_url="http://judge.test"))
    with pytest.raises(judges.JudgeError, match="HTTP 500"):
        judge.ask({"p": judges.noul("x")})


def test_openai_judge_parses_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, payload, headers=None, timeout=30):
        assert url == "http://llm.test/v1/chat/completions"
        assert payload["response_format"] == {"type": "json_object"}
        assert headers["authorization"] == "Bearer key"
        return {"choices": [{"message": {"content": '{"answers": {"p": 0.6}}'}}]}

    monkeypatch.setattr(http, "post_json", fake_post)
    judge = judges.build(
        JudgeConfig(provider="openai", base_url="http://llm.test/v1", model="m", api_key="key")
    )
    assert judge.ask({"p": judges.noul("x")}) == {"p": 0.6}


def test_anthropic_judge_parses_content_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, payload, headers=None, timeout=30):
        assert url == "http://llm.test/v1/messages"
        assert headers["x-api-key"] == "key"
        return {"content": [{"type": "text", "text": '{"answers": {"p": 0.1}}'}]}

    monkeypatch.setattr(http, "post_json", fake_post)
    judge = judges.build(
        JudgeConfig(provider="anthropic", base_url="http://llm.test", model="m", api_key="key")
    )
    assert judge.ask({"p": judges.noul("x")}) == {"p": 0.1}


def test_chat_generator_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, payload, headers=None, timeout=30):
        assert payload["messages"][0]["role"] == "system"
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    monkeypatch.setattr(http, "post_json", fake_post)
    generator = ChatCompletionsGenerator(
        GeneratorConfig(provider="openai", base_url="http://llm.test/v1", model="m", api_key="k")
    )
    assert generator.generate("sys", "user") == '{"ok": true}'


def test_chat_generator_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = ChatCompletionsGenerator(
        GeneratorConfig(provider="openai", base_url="http://llm.test/v1", model="m")
    )
    with pytest.raises(GeneratorError, match="api_key is empty"):
        generator.generate("sys", "user")


def test_pi_generator_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Result:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        captured["input"] = kwargs["input"]
        return Result()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/pi")
    monkeypatch.setattr("subprocess.run", fake_run)
    generator = PiGenerator(GeneratorConfig(provider="pi", model="some/model", timeout_s=5))
    assert generator.generate("sys", "prompt") == '{"ok": true}'
    args = captured["args"]
    assert args[0] == "/usr/local/bin/pi"
    assert "--no-tools" in args and "--no-session" in args
    assert args[args.index("--model") + 1] == "some/model"
    assert captured["env"]["RSI_LEARN"] == "0"
    assert captured["input"] == "prompt"


def test_pi_generator_reports_a_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(GeneratorError, match="cannot find"):
        PiGenerator(GeneratorConfig()).generate("sys", "prompt")


def test_pi_generator_reports_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        returncode = 2
        stdout = ""
        stderr = "model not found"

    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/pi")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: Result())
    with pytest.raises(GeneratorError, match="model not found"):
        PiGenerator(GeneratorConfig()).generate("sys", "prompt")


def test_parse_output_rejects_broken_json() -> None:
    assert parse_output('{"a": 1}') == {"a": 1}
    with pytest.raises(GeneratorError):
        parse_output("not json")


def test_replace_helper_is_available() -> None:
    cfg = JudgeConfig()
    assert replace(cfg, provider="openai").provider == "openai"
    assert json.dumps(cfg.thresholds)
