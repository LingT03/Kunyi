"""Minimal AnkiConnect client for kunyi.

AnkiConnect (Anki add-on code 2055492159) exposes a local HTTP API while
Anki is running. kunyi uses a single action from it, importPackage, to push
a freshly built .apkg straight into the open collection so a regenerated
deck doesn't have to be re-imported by hand through Anki's GUI.

Only the standard library is used so this adds no install-time dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8765"
API_VERSION = 6


class AnkiConnectError(RuntimeError):
    """Raised when AnkiConnect is unreachable or reports a failure."""


def invoke(action: str, url: str = DEFAULT_URL, timeout: float = 60.0, **params: Any) -> Any:
    """Call a single AnkiConnect action and return its result.

    Parameters
    ----------
    action:
        AnkiConnect action name, e.g. "importPackage".
    url:
        Address AnkiConnect is listening on.
    timeout:
        Seconds to wait for a reply. Imports of large decks can take a
        while, so the default is generous.
    **params:
        Parameters forwarded to the action.

    Returns
    -------
    Any
        The "result" value of AnkiConnect's reply.

    Raises
    ------
    AnkiConnectError
        If AnkiConnect cannot be reached or replies with an error.
    """
    payload = json.dumps({"action": action, "version": API_VERSION, "params": params})
    request = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            reply = json.load(response)
    except (urllib.error.URLError, OSError) as exc:
        raise AnkiConnectError(
            f"could not reach AnkiConnect at {url} ({exc}). "
            "Is Anki running with the AnkiConnect add-on installed?"
        ) from exc

    if reply.get("error") is not None:
        raise AnkiConnectError(f"AnkiConnect {action} failed: {reply['error']}")
    return reply.get("result")


def import_package(apkg_path: Path, url: str = DEFAULT_URL) -> None:
    """Import an .apkg into the running Anki collection.

    Notes whose guid already exists in the collection are updated in place;
    the rest are added. Notes that exist in Anki but are absent from the
    package are left alone — importing never deletes.

    Parameters
    ----------
    apkg_path:
        Path to the .apkg file. Resolved to an absolute path because
        AnkiConnect otherwise interprets it relative to Anki's media folder.
    url:
        Address AnkiConnect is listening on.

    Raises
    ------
    AnkiConnectError
        If AnkiConnect is unreachable or the import is not accepted.
    """
    result = invoke("importPackage", url=url, path=str(Path(apkg_path).resolve()))
    if not result:
        raise AnkiConnectError("AnkiConnect did not accept the package (is a profile open?).")
