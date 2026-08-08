"""Unfinished recordings, retained between sessions.

An air conditioner requires 120 to 180 codes, and the largest file in the
repository holds 2689. Recording that in one session is impractical. The panel's
progress otherwise exists only in the browser tab, where a reload or a crash
discards it; the alternative was to save a half-empty device file into ``codes/``
and reopen it as a template, which discards the skip marks and the position.

A draft is that session, held on the server. It stores what the panel cannot
recompute: the spec, the codes captured so far, which cells were skipped, and
the cursor position. It deliberately does **not** store the capture plan. The
plan is derived by ``capture_plan()`` from the spec at the moment of resuming,
so a draft written by an older version cannot restore a stale list of cells.

Drafts are held in ``.storage/hub_ir.drafts``, not under ``codes/``. A draft is
not a device file, and nothing that scans ``codes/`` should have to exclude one;
``.storage`` also survives a HACS update without ``persistent_directory``
covering it.

This module follows the same import discipline as ``device_file.py``: Home
Assistant and the standard library only, nothing from this package. It is the
first store this integration owns — ``learn.py`` only reads Broadlink's — so the
version constant below starts its own lineage, with nothing to migrate from at
version 1.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

DRAFT_STORAGE_VERSION = 1
DRAFT_STORAGE_KEY = "hub_ir.drafts"

# Where the loaded drafts and the lock guarding them are parked on hass.data.
DRAFT_DATA_KEY = "hub_ir_drafts"
DRAFT_LOCK_KEY = "hub_ir_drafts_lock"

# A ceiling on accumulated unfinished work. Without a limit, a store that is
# never pruned grows unbounded.
MAX_DRAFTS = 20

# Well above the largest real device file (2689 codes), so this catches only a
# malfunction rather than a genuine recording.
MAX_DRAFT_CODES = 4000


class DraftError(HomeAssistantError):
    """A draft was rejected, with the reason."""


def draft_key(platform: str, device_code: int) -> str:
    """Return the key a draft is filed under.

    Platform and device code together, because that pair is what the draft will
    be saved as. A second recording for the same target continues the first.
    """
    return f"{platform}/{device_code}"


async def _async_drafts(hass: HomeAssistant) -> tuple[Store, dict[str, Any]]:
    """Return the store and the live dict of drafts, loading it once.

    The dict is cached rather than re-read on every call, so a save modifies
    state already held. Re-reading each time would open a window in which two
    tabs load the same state and the second write discards the first draft.
    """
    cached = hass.data.get(DRAFT_DATA_KEY)
    if cached is not None:
        return cached

    store = Store[dict[str, Any]](hass, DRAFT_STORAGE_VERSION, DRAFT_STORAGE_KEY)
    raw = await store.async_load() or {}
    drafts = raw.get("drafts")
    cached = (store, drafts if isinstance(drafts, dict) else {})
    hass.data[DRAFT_DATA_KEY] = cached
    return cached


def _lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the lock serialising read-modify-write on the store."""
    lock = hass.data.get(DRAFT_LOCK_KEY)
    if lock is None:
        # setdefault would build a Lock on every call just to throw it away.
        lock = hass.data[DRAFT_LOCK_KEY] = asyncio.Lock()
    return lock


async def async_load_drafts(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Return every draft, keyed as ``platform/device_code``."""
    async with _lock(hass):
        _store, drafts = await _async_drafts(hass)
        return dict(drafts)


async def async_get_draft(
    hass: HomeAssistant, platform: str, device_code: int
) -> dict[str, Any] | None:
    """Return one draft in full, or None if there is no such draft."""
    async with _lock(hass):
        _store, drafts = await _async_drafts(hass)
        return drafts.get(draft_key(platform, device_code))


async def async_save_draft(
    hass: HomeAssistant, draft: dict[str, Any]
) -> dict[str, Any]:
    """Write one draft, replacing any earlier draft for the same target.

    Returns the stored draft, carrying the ``updated`` timestamp the panel shows
    in its list. The caller validates the shape; this function enforces only the
    limits the store itself has to protect.
    """
    codes = draft.get("codes") or {}
    if len(codes) > MAX_DRAFT_CODES:
        raise DraftError(
            f"This draft holds {len(codes)} codes, exceeding the limit of "
            f"{MAX_DRAFT_CODES} for a single recording"
        )

    key = draft_key(draft["platform"], draft["device_code"])

    async with _lock(hass):
        store, drafts = await _async_drafts(hass)

        # Replacing an existing draft is always allowed, even at the limit.
        # Refusing to save progress on work already under way would lose it.
        if key not in drafts and len(drafts) >= MAX_DRAFTS:
            raise DraftError(
                f"There are already {MAX_DRAFTS} saved drafts. Delete one before "
                "starting another"
            )

        stored = {**draft, "updated": dt_util.utcnow().isoformat()}
        drafts[key] = stored
        await store.async_save({"drafts": drafts})

    return stored


async def async_delete_draft(
    hass: HomeAssistant, platform: str, device_code: int
) -> bool:
    """Drop one draft. Returns False when there was nothing to drop.

    Deleting an absent draft is not an error: the panel deletes a draft once its
    device file is saved, and two tabs doing so is a benign race.
    """
    key = draft_key(platform, device_code)

    async with _lock(hass):
        store, drafts = await _async_drafts(hass)
        if key not in drafts:
            return False
        del drafts[key]
        await store.async_save({"drafts": drafts})

    return True


def describe(draft: dict[str, Any]) -> str:
    """Return a display label for a draft: manufacturer and first model."""
    spec = draft.get("spec") or {}
    parts = [str(spec.get("manufacturer") or "").strip()]

    models = spec.get("supportedModels") or []
    if isinstance(models, list) and models:
        parts.append(str(models[0]).strip())

    return " ".join(part for part in parts if part)


def summarize(key: str, draft: dict[str, Any]) -> dict[str, Any]:
    """Return the row the panel lists a draft as.

    Summaries exist because the codes do not belong in a listing. One climate
    draft is tens of kilobytes of base64, and sending every draft in full to
    render a list of names would be the most expensive call the panel makes.

    The counts come from the stored dicts rather than from the capture plan,
    which the server has no reason to rebuild here. They are a progress hint;
    the exact figures appear once the draft is resumed and the panel has the
    plan to count against.
    """
    return {
        "key": key,
        "platform": draft.get("platform"),
        "device_code": draft.get("device_code"),
        "label": describe(draft),
        "done": len(draft.get("codes") or {}),
        "skipped": len(draft.get("skipped") or {}),
        "total": draft.get("total") or 0,
        "updated": draft.get("updated"),
    }
