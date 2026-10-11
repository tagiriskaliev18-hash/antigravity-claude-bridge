import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

PROVIDER_ENV_KEYS = (
    "XYVERO_API_KEY",
    "OCTAVAPI_API_KEY",
    "MULTILLM_API_KEY",
    "XYVERO_BASE_URL",
    "OCTAVAPI_BASE_URL",
    "MULTILLM_BASE_URL",
    "XYVERO_MODELS",
    "OCTAVAPI_MODELS",
    "MULTILLM_MODELS",
    "MULTILLM_DEFAULT_MODEL",
    "MULTILLM_CONFIG",
    "LOCAL_LLM_API_KEY",
    "LOW_KEY",
    "HIGH_KEY",
)


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    for key in PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
