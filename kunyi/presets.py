"""Deck-option presets, applied by post-processing a finished .apkg.

genanki has no public API for Anki's per-deck study limits (new cards/day,
reviews/day) — every deck it generates shares the same baked-in options
group ("dconf" id 1) from its internal SQLite schema template. To adjust
those limits, this module reopens the .apkg after genanki has written it,
edits the embedded collection.anki2 database directly, and re-zips it.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

PRESETS: dict[str, dict[str, int]] = {
    "exam_sprint": {"new_per_day": 9999, "review_per_day": 9999},
}


def apply_preset(apkg_path: Path, preset: str) -> None:
    """Raise the new/review daily limits on the deck options in apkg_path.

    Parameters
    ----------
    apkg_path:
        Path to an already-written .apkg file.
    preset:
        Key into PRESETS.

    Raises
    ------
    KeyError
        If preset is not a known preset name.
    """
    if preset not in PRESETS:
        raise KeyError(f"Unknown preset {preset!r}. Available presets: {sorted(PRESETS)}")
    limits = PRESETS[preset]

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        with zipfile.ZipFile(apkg_path, "r") as zf:
            zf.extractall(tmp_dir_path)

        db_path = tmp_dir_path / "collection.anki2"
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            (dconf_json,) = cursor.execute("SELECT dconf FROM col").fetchone()
            dconf = json.loads(dconf_json)
            for group in dconf.values():
                group["new"]["perDay"] = limits["new_per_day"]
                group["rev"]["perDay"] = limits["review_per_day"]
            cursor.execute("UPDATE col SET dconf = ?", (json.dumps(dconf),))
            conn.commit()
        finally:
            conn.close()

        with zipfile.ZipFile(apkg_path, "w") as zf:
            for item in tmp_dir_path.iterdir():
                zf.write(item, item.name)
