"""Local-only Ollama transport, with streamed latency and token measurements.

Metadata methods never load a model. Only :meth:`OllamaClient.chat` performs
inference. No third-party package, cloud provider, model pull, or proxy is used.
Ollama durations are nanoseconds; this module exposes them as seconds.
"""

import ipaddress
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class OllamaError(RuntimeError):
    """A transport or response error that must remain visible in the run data."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects rather than risk sending benchmark data off the machine."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OllamaError(f"Ollama redirects are disabled (HTTP {code}).")


def local_base_url(value=None):
    """Normalize an HTTP loopback endpoint, accepting Ollama's host:port form."""
    value = value or os.environ.get("OLLAMA_URL") or os.environ.get("OLLAMA_HOST")
    value = value or "http://127.0.0.1:11434"
    if "://" not in value:
        value = "http://" + value
    parsed = urllib.parse.urlsplit(value)
    hostname = parsed.hostname or ""
    try:
        local = hostname.lower() == "localhost" or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        local = hostname.lower() == "localhost"
    if (parsed.scheme != "http" or not local or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Use a local Ollama HTTP address, e.g. http://127.0.0.1:11434; "
                         "remote endpoints, credentials, paths, and HTTPS are not supported.")
    try:
        port = parsed.port or 11434
    except ValueError as exc:
        raise ValueError("Invalid Ollama port.") from exc
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"http://{host}:{port}"


def _number(value, name, *, integer=False):
    """Validate optional server counters; missing measurements remain missing."""
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
            or (integer and int(value) != value)):
        raise OllamaError(f"Invalid Ollama measurement: {name}={value!r}")
    return int(value) if integer else float(value)


def _duration(payload, key):
    """Convert an optional nanosecond counter without inventing a zero."""
    value = _number(payload.get(key), key)
    return None if value is None else value / 1_000_000_000


class OllamaClient:
    """Read metadata and generate text from a loopback Ollama server."""

    def __init__(self, base_url=None, timeout=180.0):
        self.base_url = local_base_url(base_url)
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        # An explicit empty proxy map ignores HTTP_PROXY and system proxies.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())

    def _open(self, path, payload=None):
        """Open one fixed local API path and translate transport failures."""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="GET" if data is None else "POST")
        try:
            return self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama HTTP {exc.code} at {path}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OllamaError(f"Cannot reach local Ollama at {self.base_url}: {exc}") from exc

    def _json(self, path, payload=None):
        """Read a small metadata object; this never calls a generation endpoint."""
        try:
            with self._open(path, payload) as response:
                value = json.load(response)
        except (ValueError, OSError) as exc:
            raise OllamaError(f"Invalid Ollama metadata at {path}: {exc}") from exc
        if not isinstance(value, dict) or value.get("error"):
            raise OllamaError(f"Invalid Ollama metadata at {path}: {value!r}")
        return value

    def inventory(self):
        """Return every installed tag and its eligibility, without loading weights.

        Alias tags remain separate inventory entries. Failed model inspection is
        recorded as a skip, and a generation run must not silently ignore it.
        Older servers without capability metadata receive an explicit warning.
        """
        tags = self._json("/api/tags")
        models = tags.get("models")
        if not isinstance(models, list):
            raise OllamaError("/api/tags did not return a models list")
        try:
            version = str(self._json("/api/version").get("version", "unknown"))
        except OllamaError as exc:
            version = "unknown"
            version_warning = str(exc)
        else:
            version_warning = ""
        inspected = []
        for original in models:
            if not isinstance(original, dict):
                raise OllamaError("Invalid model entry in /api/tags")
            model = dict(original)
            name = model.get("name") or model.get("model")
            if not isinstance(name, str) or not name:
                raise OllamaError("Installed model entry has no name")
            model.update(name=name, eligible=False, skip_reason="", capabilities=[])
            try:
                info = self._json("/api/show", {"model": name})
                capabilities = info.get("capabilities", [])
                if not isinstance(capabilities, list) or any(
                        not isinstance(c, str) for c in capabilities):
                    raise OllamaError("Invalid capabilities in /api/show")
                model["capabilities"] = capabilities
                for key in ("details", "parameters", "model_info", "license",
                            "remote_host", "remote_model", "modified_at"):
                    if key in info:
                        model[key] = info[key]
                cloud = (model.get("remote_host") or model.get("remote_model")
                         or name.lower().endswith(":cloud")
                         or name.lower().endswith("-cloud"))
                if cloud:
                    model["skip_reason"] = "Remote/cloud model; local inference only."
                elif capabilities and "completion" not in capabilities:
                    model["skip_reason"] = "Model has no text completion capability."
                else:
                    model["eligible"] = True
                    if not capabilities:
                        model["metadata_warning"] = (
                            "Server did not expose capabilities; text support unverified.")
            except OllamaError as exc:
                model["skip_reason"] = f"Model metadata unavailable: {exc}"
            inspected.append(model)
        return {"version": version, "version_warning": version_warning,
                "base_url": self.base_url, "models": inspected}

    @staticmethod
    def _remaining_timeout(response, deadline):
        """Apply the remaining wall deadline when urllib exposes its socket."""
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise OllamaError("Ollama generation exceeded the wall-clock timeout")
        # urllib's timeout normally bounds each individual read. Replacing it
        # with the remaining budget also bounds a stream that drips tiny chunks.
        raw = getattr(getattr(response, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.settimeout(remaining)

    def chat(self, model, messages, *, options, keep_alive="2m", think=None):
        """Generate one streamed reply and return text plus measured telemetry.

        TTFT is time to first visible answer text; first_event_seconds also
        includes thinking. Thinking is saved separately, never used as an answer.
        Completion token counts may include thinking tokens. Incomplete or
        malformed streams raise instead of scoring partial replies.
        """
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty installed tag")
        if model.lower().endswith((":cloud", "-cloud")):
            raise ValueError("Remote/cloud models are not allowed")
        payload = {"model": model, "messages": messages, "stream": True,
                   "options": options, "keep_alive": keep_alive}
        if think is not None:
            payload["think"] = think
        started = time.perf_counter()
        deadline = started + self.timeout
        content, thinking = [], []
        ttft = first_event = None
        final = None
        try:
            with self._open("/api/chat", payload) as response:
                while True:
                    self._remaining_timeout(response, deadline)
                    line = response.readline()
                    if not line:
                        break
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise OllamaError("Ollama emitted invalid streamed JSON") from exc
                    if not isinstance(chunk, dict):
                        raise OllamaError("Ollama emitted a non-object stream event")
                    if chunk.get("error"):
                        raise OllamaError(f"Ollama generation failed: {chunk['error']}")
                    message = chunk.get("message", {})
                    if not isinstance(message, dict):
                        raise OllamaError("Ollama emitted an invalid message")
                    visible = message.get("content", "")
                    thought = message.get("thinking", "")
                    if not isinstance(visible, str) or not isinstance(thought, str):
                        raise OllamaError("Ollama emitted non-text content or thinking")
                    if message.get("tool_calls"):
                        raise OllamaError("Unexpected tool calls: the benchmark supplies no tools")
                    elapsed = time.perf_counter() - started
                    if elapsed > self.timeout:
                        raise OllamaError("Ollama generation exceeded the wall-clock timeout")
                    if first_event is None and (visible or thought):
                        first_event = elapsed
                    if ttft is None and visible:
                        ttft = elapsed
                    # Thinking is never concatenated into the answer. Otherwise
                    # a correct number in private reasoning could pass an empty
                    # or incorrect final response, contaminating the endpoint.
                    content.append(visible)
                    thinking.append(thought)
                    if chunk.get("done") is True:
                        final = chunk
                        break
        except (OSError, urllib.error.URLError) as exc:
            raise OllamaError(f"Ollama stream interrupted: {exc}") from exc
        if final is None:
            raise OllamaError("Ollama stream ended without a completed response")
        seconds = time.perf_counter() - started
        eval_seconds = _duration(final, "eval_duration")
        completion_tokens = _number(final.get("eval_count"), "eval_count", integer=True)
        done_reason = final.get("done_reason", "")
        if not isinstance(done_reason, str):
            raise OllamaError("Ollama emitted a non-text done_reason")
        return {
            "reply": "".join(content), "thinking": "".join(thinking),
            "seconds": seconds, "ttft_seconds": ttft,
            "first_event_seconds": first_event,
            "total_duration_seconds": _duration(final, "total_duration"),
            "load_seconds": _duration(final, "load_duration"),
            "prompt_eval_seconds": _duration(final, "prompt_eval_duration"),
            "eval_seconds": eval_seconds,
            "prompt_tokens": _number(final.get("prompt_eval_count"), "prompt_eval_count", integer=True),
            "completion_tokens": completion_tokens,
            "tokens_per_second": (completion_tokens / eval_seconds
                                  if completion_tokens is not None and eval_seconds else None),
            "done_reason": done_reason,
        }


def ask_ollama(model, prompt):
    """Compatibility helper; the evaluation runner uses the full client."""
    return OllamaClient().chat(model, [{"role": "user", "content": prompt}],
                               options={})["reply"]


def ask(spec, prompt):
    """Legacy backend:model entry point, now local Ollama only."""
    backend, separator, model = spec.partition(":")
    if backend != "ollama" or not separator or not model:
        raise ValueError("Use ollama:<installed-model>; this project runs local models only")
    return ask_ollama(model, prompt)
