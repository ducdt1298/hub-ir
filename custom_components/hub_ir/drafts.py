"""Half-finished recordings, kept between sittings.

A real air conditioner is 120 to 180 codes, and the largest file in the
repository holds 2689. Nobody records that in one go. Until now the panel's
progress lived only in the browser tab: a reload, a crash, or a phone going
flat lost every code captured that evening, and the only way back was to save a
half-empty device file into ``codes/`` and reopen it as a template — which lost
the skip marks and the position along with it.

A draft is that session, parked on the server. It holds what the panel cannot
recompute: the spec, the codes captured so far, which cells were deliberately
skipped, and where the cursor was. It deliberately does **not** hold the capture
plan. The plan is whatever ``capture_plan()`` derives from the spec at the
moment of resuming, so a draft written by an older version can never revive a
stale list of cells.

Drafts live in ``.storage/hub_ir.drafts``, not under ``codes/``. Two reasons:
a draft is not a device file and nothing that scans ``codes/`` should have to
learn to ignore it, and ``.storage`` is untouched by a HACS update without
needing ``persistent_directory`` to cover it.

This module keeps the same discipline as ``device_file.py`` about its imports:
Home Assistant and the standard library, nothing from this package. It is the
first store this integration owns — ``learn.py`` only reads Broadlink's — so
the version constant below starts its own lineage, and at version 1 there is
nothing to migrate from.
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

# A ceiling on how much unfinished work accumulates. Twenty half-learned
# devices is already more than anyone is really juggling, and without a limit a
# store that is never tidied grows without anything ever noticing.
MAX_DRAFTS = 20

# Comfortably above the largest real device file (2689 codes), so this only
# ever catches something that has gone wrong rather than a genuine recording.
MAX_DRAFT_CODES = 4000


class DraftError(HomeAssistantError):
    """A draft was refused, with a sentence explaining why."""


def draft_key(platform: str, device_code: int) -> str:
    """Return the key a draft is filed under.

    Platform and device code together, because that pair is exactly what the
    draft will be saved as, and starting a second recording for the same target
    is a continuation of the first rather than a new thing.
    """
    return f"{platform}/{device_code}"


async def _async_drafts(hass: HomeAssistant) -> tuple[Store, dict[str, Any]]:
    """Return the store and the live dict of drafts, loading it once.

    The dict is cached rather than re-read on every call, so a save is a
    modification of what is already in hand. Reading afresh each time would
    open a window in which two panels, or two tabs, each load the same state
    and the second write silently drops the first one's draft.
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

    Returns the stored draft, which carries the ``updated`` stamp the panel
    shows in its list. The caller is expected to have validated the shape; what
    is enforced here is only what the store itself has to protect.
    """
    codes = draft.get("codes") or {}
    if len(codes) > MAX_DRAFT_CODES:
        raise DraftError(
            f"This draft holds {len(codes)} codes, more than the {MAX_DRAFT_CODES} "
            "a single recording is expected to need"
        )

    key = draft_key(draft["platform"], draft["device_code"])

    async with _lock(hass):
        store, drafts = await _async_drafts(hass)

        # Replacing an existing draft is always allowed, even at the limit:
        # refusing to save progress on work already under way would be exactly
        # the wrong moment to start enforcing tidiness.
        if key not in drafts and len(drafts) >= MAX_DRAFTS:
            raise DraftError(
                f"There are already {MAX_DRAFTS} saved drafts. Finish one or "
                "delete one before starting another"
            )

        stored = {**draft, "updated": dt_util.utcnow().isoformat()}
        drafts[key] = stored
        await store.async_save({"drafts": drafts})

    return stored


async def async_delete_draft(
    hass: HomeAssistant, platform: str, device_code: int
) -> bool:
    """Drop one draft. Returns False when there was nothing to drop.

    Deleting something that is already gone is not an error: the panel deletes
    a draft after its device file is saved, and two tabs doing that is a race
    nobody should have to see a message about.
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
    """Return a human label for a draft: manufacturer and first model."""
    spec = draft.get("spec") or {}
    parts = [str(spec.get("manufacturer") or "").strip()]

    models = spec.get("supportedModels") or []
    if isinstance(models, list) and models:
        parts.append(str(models[0]).strip())

    return " ".join(part for part in parts if part)


def summarize(key: str, draft: dict[str, Any]) -> dict[str, Any]:
    """Return the row the panel lists a draft as.

    Summaries exist because the codes do not belong in a listing. One climate
    draft is tens of kilobytes of base64, and sending every draft in full just
    to draw a list of names would be the most expensive thing the panel does on
    open.

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
