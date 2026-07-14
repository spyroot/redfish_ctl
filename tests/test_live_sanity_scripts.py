from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GB300_VIRTUAL_MEDIA_SCRIPT = (
    ROOT / "scripts/live_sanity_check/supermicro/gb300/virtual_media_roundtrip.sh"
)


def _script_text() -> str:
    return GB300_VIRTUAL_MEDIA_SCRIPT.read_text(encoding="utf-8")


def test_gb300_virtual_media_roundtrip_passes_confirm_to_mutating_commands():
    script = _script_text()
    rctl_line = next(
        line for line in script.splitlines()
        if line.startswith("RCTL=(")
    )

    assert "--confirm" in rctl_line


def test_gb300_virtual_media_roundtrip_always_captures_invalid_device_error():
    script = _script_text()

    assert 'RUN_NEGATIVE' not in script
    assert 'insert_vm --device_id "__invalid__"' in script
