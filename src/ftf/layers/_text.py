"""Select one body representation without adding navigation or duplicate text."""

from ..contracts import Observation, Purpose, State

# Older observations remain usable when the body extractor did not run.
TEXT_NAMESPACES = ("page.body_text", "text.body", "page.clean_text")


def body_text(observations: list[Observation]) -> str:
    for namespace in TEXT_NAMESPACES:
        values = {
            o.value.strip()
            for o in observations
            if o.namespace == namespace
            and o.state is State.PRESENT
            and o.purpose is not Purpose.RISK
            and isinstance(o.value, str)
        }
        if values:
            return "\n".join(sorted(values))
    return ""
