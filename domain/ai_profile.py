"""Local AI profiles and prompt templates (N05).

A *profile* names a way of reaching the local model server: endpoint,
model, how much transcript a request may carry, and a temperature for the
long-form step. Choosing one changes those settings for the LLM steps;
the built-in defaults (the plain ``lm_studio_url`` etc.) always remain
the baseline and are the "Default" profile.

A *prompt template* is a user copy of a built-in prompt: edited text, a
version number that grows with each save, and optional input/output
examples. The built-in prompt file is never modified. Both are plain data:
a template's text is a prompt for the model, never code, and neither holds
a key or token — importing one cannot configure credentials.

Qt-free; nothing here reaches the network. Ollama is *not* supported yet:
it needs a separate adapter with its own contract tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

PROFILE_SCHEMA_VERSION = 1
TEMPLATE_SCHEMA_VERSION = 1
DEFAULT_PROFILE = "Default"

# Steps a profile may be marked as meant for (informational: a profile is
# applied to every LLM step; the dialog shows what it was made for).
LLM_STEPS = ("clean", "article", "insights", "youtube_package", "book")

MIN_CONTEXT = 4_000
MAX_CONTEXT = 400_000
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SECRET_KEYS = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)")


class ProfileError(ValueError):
    """A profile or template that is not valid (the message says why)."""


@dataclass(frozen=True)
class LmProfile:
    name: str
    endpoint: str
    model: str = ""                 # empty = whatever LM Studio has loaded
    context_chars: int = 48_000
    temperature: float = 0.3        # the book step; other steps keep the server's
    steps: tuple[str, ...] = ()     # empty = suited to every LLM step

    def to_dict(self) -> dict:
        return {"schema_version": PROFILE_SCHEMA_VERSION, "name": self.name,
                "endpoint": self.endpoint, "model": self.model,
                "context_chars": self.context_chars, "temperature": self.temperature,
                "steps": list(self.steps)}

    @property
    def is_local(self) -> bool:
        host = (urlparse(self.endpoint).hostname or "").lower()
        return host in _LOCAL_HOSTS or host.endswith(".local") or host.endswith(".localhost")


def default_profile(cfg: Any) -> LmProfile:
    """The built-in baseline, read from the plain settings."""
    return LmProfile(
        name=DEFAULT_PROFILE, endpoint=cfg.lm_studio_url, model="",
        context_chars=int(getattr(cfg, "insights_context_chars", 48_000)),
        temperature=float(getattr(cfg, "book_temperature", 0.3)),
    )


def profile_from_dict(data: Any) -> LmProfile:
    """Validate and build a profile; raises :class:`ProfileError`. Unknown
    keys are ignored; a key that looks like a credential is refused (a
    profile never carries one)."""
    if not isinstance(data, dict):
        raise ProfileError("a profile must be an object")
    if int(data.get("schema_version", 1)) > PROFILE_SCHEMA_VERSION:
        raise ProfileError("made by a newer Whispered")
    for key in data:
        if _SECRET_KEYS.search(str(key)):
            raise ProfileError(f"{key}: profiles do not hold credentials")
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 60:
        raise ProfileError("name: 1–60 characters")
    endpoint = str(data.get("endpoint", "")).strip().rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProfileError("endpoint: an http(s) address like http://localhost:1234/v1")
    try:
        context = int(data.get("context_chars", 48_000))
        temperature = float(data.get("temperature", 0.3))
    except (TypeError, ValueError) as exc:
        raise ProfileError("context_chars / temperature must be numbers") from exc
    if not MIN_CONTEXT <= context <= MAX_CONTEXT:
        raise ProfileError(f"context_chars: {MIN_CONTEXT}–{MAX_CONTEXT}")
    if not 0.0 <= temperature <= 2.0:
        raise ProfileError("temperature: 0–2")
    steps = tuple(str(s) for s in data.get("steps", ()) if str(s) in LLM_STEPS)
    return LmProfile(name, endpoint, str(data.get("model", "")).strip(), context, temperature, steps)


# ---------------------------------------------------------------- templates

@dataclass
class TemplateExample:
    input: str
    output: str


@dataclass
class PromptTemplate:
    name: str                       # display name, also the file slug
    base: str                       # the built-in prompt it is a copy of ("cleaning")
    text: str
    version: int = 1
    examples: list[TemplateExample] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"schema_version": TEMPLATE_SCHEMA_VERSION, "name": self.name,
                "base": self.base, "text": self.text, "version": self.version,
                "examples": [{"input": e.input, "output": e.output} for e in self.examples]}

    def rendered(self) -> str:
        """The prompt as the model receives it: the text, then the worked
        examples (if any) as data to imitate."""
        if not self.examples:
            return self.text
        blocks = [self.text.rstrip(), "", "Examples:"]
        for n, example in enumerate(self.examples, start=1):
            blocks += [f"--- Example {n} input ---", example.input.strip(),
                       f"--- Example {n} output ---", example.output.strip()]
        return "\n".join(blocks)


def slug(name: str) -> str:
    cleaned = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE).strip().replace(" ", "-")
    return cleaned[:60] or "template"


def template_from_dict(data: Any, known_bases: Optional[set] = None) -> PromptTemplate:
    """Validate and build a template; raises :class:`ProfileError`. The
    text is only ever sent to the model."""
    if not isinstance(data, dict):
        raise ProfileError("a template must be an object")
    if int(data.get("schema_version", 1)) > TEMPLATE_SCHEMA_VERSION:
        raise ProfileError("made by a newer Whispered")
    name = str(data.get("name", "")).strip()
    base = str(data.get("base", "")).strip()
    text = str(data.get("text", ""))
    if not name or len(name) > 60:
        raise ProfileError("name: 1–60 characters")
    if not base or (known_bases is not None and base not in known_bases):
        raise ProfileError(f"base: unknown built-in prompt {base!r}")
    if not text.strip():
        raise ProfileError("text: empty")
    if len(text) > 50_000:
        raise ProfileError("text: too long")
    examples = []
    for entry in data.get("examples", []):
        if isinstance(entry, dict) and str(entry.get("input", "")).strip() \
                and str(entry.get("output", "")).strip():
            examples.append(TemplateExample(str(entry["input"]), str(entry["output"])))
    try:
        version = max(1, int(data.get("version", 1)))
    except (TypeError, ValueError) as exc:
        raise ProfileError("version must be a number") from exc
    return PromptTemplate(name, base, text, version, examples)
