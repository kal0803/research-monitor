from collectors.generic import (
    GenericNewsCollector,
)


class SafetyNewsCollector(
    GenericNewsCollector
):
    source_key = "safetynews"
    