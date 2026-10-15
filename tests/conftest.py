import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def _module(name):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    value = ModuleType(name)
    sys.modules[name] = value
    return value


# The test runner image omits several runtime packages listed in
# requirements.txt. These shims satisfy import-time references only; tested
# paths replace API, browser, and similarity calls before invoking them.
try:
    import dotenv  # noqa: F401
except ImportError:
    _module("dotenv").load_dotenv = lambda *args, **kwargs: None

try:
    import openai  # noqa: F401
except ImportError:
    _module("openai").OpenAI = object

try:
    import playwright.sync_api  # noqa: F401
except ImportError:
    playwright = _module("playwright")
    playwright.__path__ = []
    sync_api = _module("playwright.sync_api")
    sync_api.sync_playwright = lambda: None

try:
    import sklearn  # noqa: F401
except ImportError:
    sklearn = _module("sklearn")
    sklearn.__path__ = []
    feature_extraction = _module("sklearn.feature_extraction")
    feature_extraction.__path__ = []
    text = _module("sklearn.feature_extraction.text")
    text.TfidfVectorizer = object
    metrics = _module("sklearn.metrics")
    metrics.__path__ = []
    pairwise = _module("sklearn.metrics.pairwise")
    pairwise.cosine_similarity = lambda *args, **kwargs: None
