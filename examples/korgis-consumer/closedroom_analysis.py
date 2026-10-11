"""Illustrative *consumer* integration; ClosedRoom itself owns meeting state."""

from pathlib import Path

from korgis_sdk import Korgis

BINDINGS = Path(__file__).with_name("closedroom-bindings.json")


def summarize_transcript(transcript: str) -> str:
    # The source meeting transcript and application prompt remain caller-owned.
    with Korgis.connect(BINDINGS) as ai:
        return ai.role("analysis").generate(
            "Summarize the following meeting and list action items:\n" + transcript,
            temperature=0,
        )


if __name__ == "__main__":
    print(summarize_transcript("Alice agreed to send the report tomorrow."))
