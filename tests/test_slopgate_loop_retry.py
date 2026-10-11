import importlib.util
import pathlib
import subprocess
from unittest import mock


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools/slopgate/semantic/loop_test.py"
SPEC = importlib.util.spec_from_file_location("slopgate_loop_retry", SCRIPT)
loop_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop_test)


def test_llm_retries_subprocess_timeout():
    timeout = subprocess.TimeoutExpired("curl", 300, output=b"partial",
                                        stderr=b"curl: (28) Operation timed out")
    success = subprocess.CompletedProcess(
        ["curl"], 0, '{"choices":[{"message":{"content":"ok"}}]}', "")
    with mock.patch.object(loop_test.subprocess, "run", side_effect=[timeout, success]) as call, \
         mock.patch.object(loop_test.time, "sleep") as sleep, \
         mock.patch.dict(loop_test.os.environ, {"OPENAI_API_KEY": ""}):
        assert loop_test.llm("prompt") == "ok"

    assert call.call_count == 2
    sleep.assert_called_once_with(15)


def test_llm_retries_temporary_html_503_response():
    unavailable = subprocess.CompletedProcess(
        ["curl"], 22, "<html>temporarily unavailable</html>",
        "curl: (22) The requested URL returned error: 503")
    success = subprocess.CompletedProcess(
        ["curl"], 0, '{"choices":[{"message":{"content":"ok"}}]}', "")
    with mock.patch.object(loop_test.subprocess, "run", side_effect=[unavailable, success]) as call, \
         mock.patch.object(loop_test.time, "sleep") as sleep, \
         mock.patch.dict(loop_test.os.environ, {"OPENAI_API_KEY": ""}):
        assert loop_test.llm("prompt") == "ok"

    assert call.call_count == 2
    assert "--fail-with-body" in call.call_args_list[0].args[0]
    sleep.assert_called_once_with(15)


def test_llm_retries_curl_timeout_even_with_partial_output():
    timeout = subprocess.CompletedProcess(
        ["curl"], 28, "partial response", "curl: (28) Operation timed out")
    success = subprocess.CompletedProcess(
        ["curl"], 0, '{"choices":[{"message":{"content":"ok"}}]}', "")
    with mock.patch.object(loop_test.subprocess, "run", side_effect=[timeout, success]) as call, \
         mock.patch.object(loop_test.time, "sleep") as sleep, \
         mock.patch.dict(loop_test.os.environ, {"OPENAI_API_KEY": ""}):
        assert loop_test.llm("prompt") == "ok"

    assert call.call_count == 2
    sleep.assert_called_once_with(15)


def test_llm_does_not_retry_permanent_html_401_response():
    unauthorized = subprocess.CompletedProcess(
        ["curl"], 22, "<html>unauthorized</html>",
        "curl: (22) The requested URL returned error: 401")
    with mock.patch.object(loop_test.subprocess, "run", return_value=unauthorized) as call, \
         mock.patch.object(loop_test.time, "sleep") as sleep, \
         mock.patch.dict(loop_test.os.environ, {"OPENAI_API_KEY": ""}):
        try:
            loop_test.llm("prompt")
        except RuntimeError as error:
            assert "401" in str(error)
        else:
            raise AssertionError("permanent HTTP error was swallowed")

    call.assert_called_once()
    sleep.assert_not_called()


def test_llm_retries_overload_named_only_in_the_http_error_body():
    overloaded = subprocess.CompletedProcess(
        ["curl"], 22, '{"error":{"message":"Overloaded"}}',
        "curl: (22) The requested URL returned error: 529")
    success = subprocess.CompletedProcess(
        ["curl"], 0, '{"choices":[{"message":{"content":"ok"}}]}', "")
    with mock.patch.object(loop_test.subprocess, "run", side_effect=[overloaded, success]) as call, \
         mock.patch.object(loop_test.time, "sleep") as sleep, \
         mock.patch.dict(loop_test.os.environ, {"OPENAI_API_KEY": ""}):
        assert loop_test.llm("prompt") == "ok"

    assert call.call_count == 2
    sleep.assert_called_once_with(15)


def test_llm_error_keeps_the_gateway_body():
    bad_request = subprocess.CompletedProcess(
        ["curl"], 22, '{"error":{"message":"context length exceeded"}}',
        "curl: (22) The requested URL returned error: 400")
    with mock.patch.object(loop_test.subprocess, "run", return_value=bad_request), \
         mock.patch.object(loop_test.time, "sleep") as sleep, \
         mock.patch.dict(loop_test.os.environ, {"OPENAI_API_KEY": ""}):
        try:
            loop_test.llm("prompt")
        except RuntimeError as error:
            assert "context length exceeded" in str(error)
        else:
            raise AssertionError("permanent HTTP error was swallowed")

    sleep.assert_not_called()
