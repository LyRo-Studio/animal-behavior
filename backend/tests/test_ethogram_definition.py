"""The committed consolidation definition must be exactly what its
generator derives from the committed Ethogram (ticket #150), so the
Behaviour group mapping can't drift from the Ethogram unnoticed.
"""

from consolidation.ethogram_definition import DEFINITION_PATH, ETHOGRAM_PATH, generate


def test_regenerating_the_definition_reproduces_the_committed_one() -> None:
    assert generate(ETHOGRAM_PATH) == DEFINITION_PATH.read_text(encoding="utf-8")
