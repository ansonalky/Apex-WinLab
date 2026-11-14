"""Replay one Overstat timeseries the way the public replay frontend does.

``patchData`` lives in the replay bundle and delegates the ``d`` object to
jsondiffpatch's ``patch``. This module follows that bundled patcher. It does
not merge dictionaries, and it does not decide what an array means by length
unless the value is a jsondiffpatch delta array (the same test the bundle uses).

Sources, retrieved with the replay page
https://overstat.gg/tournament/octanecollegiate/23231.S5_Premier_Lobby1_Week_6/replay/3

- Shell: https://overstat.gg/js/app.88b57234.js
  Route ``tournament.replay`` loads chunk 9107.
- Caller: https://overstat.gg/js/9107.be2636c4.js
  ``playCurrentFrame`` does ``patchData(currentState, entry.d, entry.f)``
  starting from ``currentState = {feed: []}``.
  ``loadGameTimeline`` expands ``series`` with
  ``new Array(series[series.length - 1].t)``, so the frame whose ``t`` equals
  that length is never applied.
- ``patchData`` and the bundled patcher: https://overstat.gg/js/8333.38781711.js
  Webpack module 2003 is ``patchData``. Webpack module 3121 is jsondiffpatch.
  The default patcher is constructed with no options, so it has no
  diff-match-patch library.
"""

from __future__ import annotations

import copy
from typing import Any

# jsondiffpatch's patch result for a removed object key is JavaScript
# ``undefined``, and the object collector deletes that key. A removed array
# slot is also ``undefined``, but the slot stays in the array.
DELETED = object()
JS_UNDEFINED = object()

TEXT_DIFF = 2
MOVE = 3


class PatchError(Exception):
    """A delta the bundled default patcher rejects or cannot apply."""


def initial_state() -> dict:
    """The replay component's ``data().currentState``."""
    return {"feed": []}


def _stamp(value: Any, observed_at: int) -> Any:
    if isinstance(value, dict):
        return {key: _stamp(inner, observed_at) for key, inner in value.items()}
    if isinstance(value, list):
        return [_stamp(inner, observed_at) for inner in value]
    return observed_at


def _array_index(key: str) -> bool:
    """JavaScript ordinary object keys that are array indexes, in for-in order."""
    if not key.isdigit():
        return False
    if key != "0" and key.startswith("0"):
        return False
    index = int(key)
    return 0 <= index < 2**32 - 1


def _js_object_keys(delta: dict) -> list[str]:
    indexes = [key for key in delta if _array_index(key)]
    rest = [key for key in delta if not _array_index(key)]
    indexes.sort(key=int)
    return indexes + rest


def _atomic(delta: list, observed_at: int) -> tuple[Any, Any]:
    length = len(delta)
    if length == 1:
        value = copy.deepcopy(delta[0])
        return value, _stamp(value, observed_at)
    if length == 2:
        value = copy.deepcopy(delta[1])
        return value, _stamp(value, observed_at)
    if length == 3 and delta[2] == 0:
        return DELETED, DELETED
    if length == 3 and delta[2] == TEXT_DIFF:
        raise PatchError(
            "text diff rejected: the bundled default patcher has no diff-match-patch"
        )
    raise PatchError(f"unrecognized delta array of length {length}")


def _patch_object(left: Any, delta: dict, observed: Any, observed_at: int) -> tuple[Any, Any]:
    if not delta:
        raise PatchError("empty object delta")
    if not isinstance(left, dict):
        raise PatchError("object delta applied to a non-object")
    if not isinstance(observed, dict):
        observed = {}
    # Child results are written after every sibling has succeeded. Nested
    # deltas still mutate an existing child object while that child runs,
    # which is when the bundled collector writes them. A later sibling that
    # throws therefore keeps those nested edits and drops unassigned
    # replacements, same as the bundle's parent collector.
    pending = []
    for key in _js_object_keys(delta):
        present = key in left
        new_value, new_observed = patch_observing(
            left[key] if present else DELETED,
            delta[key],
            observed.get(key) if present else None,
            observed_at,
        )
        pending.append((key, present, new_value, new_observed))
    for key, present, new_value, new_observed in pending:
        if new_value is DELETED:
            if present:
                del left[key]
            observed.pop(key, None)
        else:
            left[key] = new_value
            observed[key] = new_observed
    return left, observed


def _patch_array(left: Any, delta: dict, observed: Any, observed_at: int) -> tuple[Any, Any]:
    if not isinstance(left, list):
        raise PatchError("array delta applied to a non-array")
    if not isinstance(observed, list):
        observed = [None] * len(left)
    if len(observed) < len(left):
        observed.extend([None] * (len(left) - len(observed)))
    elif len(observed) > len(left):
        del observed[len(left) :]

    removals: list[int] = []
    inserts: list[tuple[int, Any, Any]] = []
    modifications: list[tuple[int, Any]] = []
    for key, child in delta.items():
        if key == "_t":
            continue
        if key.startswith("_"):
            if not (
                isinstance(child, list)
                and len(child) == 3
                and child[2] in (0, MOVE)
            ):
                raise PatchError(
                    "only removal or move can be applied at original array indices"
                )
            removals.append(int(key[1:]))
            continue
        index = int(key)
        if isinstance(child, list) and len(child) == 1:
            value = copy.deepcopy(child[0])
            inserts.append((index, value, _stamp(value, observed_at)))
        else:
            modifications.append((index, child))

    for index in sorted(removals, reverse=True):
        removed = left.pop(index)
        removed_observed = observed.pop(index)
        marker = delta[f"_{index}"]
        if marker[2] == MOVE:
            inserts.append((marker[1], removed, removed_observed))

    for index, value, node in sorted(inserts, key=lambda item: item[0]):
        left.insert(index, value)
        observed.insert(index, node)

    for index, child in modifications:
        new_value, new_observed = patch_observing(
            left[index], child, observed[index], observed_at
        )
        if new_value is DELETED:
            left[index] = JS_UNDEFINED
            observed[index] = None
        else:
            left[index] = new_value
            observed[index] = new_observed
    return left, observed


def patch_observing(
    left: Any, delta: Any, observed: Any, observed_at: int
) -> tuple[Any, Any]:
    """Apply one jsondiffpatch delta and record which leaves it wrote.

    ``observed`` mirrors ``left``. A leaf holds the ``t`` of the delta that
    wrote it. ``None`` means that leaf has no recorded write.
    """
    if isinstance(delta, list):
        return _atomic(delta, observed_at)
    if isinstance(delta, dict):
        if delta.get("_t") == "a":
            return _patch_array(left, delta, observed, observed_at)
        if "_t" in delta:
            raise PatchError(f"unsupported delta marker {delta.get('_t')!r}")
        return _patch_object(left, delta, observed, observed_at)
    raise PatchError(f"delta must be a list or object, got {type(delta).__name__}")


def patch(left: Any, delta: Any) -> Any:
    """``jsondiffpatch.patch`` for one delta. Object and array inputs are mutated."""
    value, _observed = patch_observing(left, delta, None, 0)
    if value is DELETED:
        return JS_UNDEFINED
    return value


def apply_frame(
    state: dict,
    delta: Any,
    events: list | None,
    observed: dict,
    observed_at: int,
) -> tuple[dict, dict, str | None]:
    """``patchData(currentState, entry.d, entry.f)`` plus leaf observation times.

    A thrown patch keeps the state object from before this call's assignments.
    Nested edits that the bundled collector already wrote stay written.
    ``events`` are appended to ``state["feed"]`` even when the patch throws.
    """
    error = None
    # JavaScript treats {} and [] as truthy. Only null/undefined skips the patch.
    if delta is not None:
        try:
            patched, observed = patch_observing(state, delta, observed, observed_at)
        except PatchError as exc:
            error = str(exc)
        else:
            if not isinstance(patched, dict):
                raise PatchError("root state stopped being an object")
            state = patched
    if state.get("feed") is None:
        state["feed"] = []
    if events:
        state["feed"].extend(events)
    return state, observed, error


def frame_entries(series: list[dict]) -> list[dict]:
    """Expand raw ``series`` the way ``loadGameTimeline`` does.

    The frontend allocates ``new Array(series[series.length - 1].t)`` and
    fills index ``s`` with the entry whose ``t == s``, or ``{t: s, f: []}``
    when that second is missing. The entry at ``t == last t`` is not included.
    """
    if not series:
        raise ValueError("series is empty")
    last = series[-1].get("t")
    if not isinstance(last, int) or isinstance(last, bool) or last < 0:
        raise ValueError("series[-1].t must be a non-negative integer")
    first_by_t: dict[int, dict] = {}
    for entry in series:
        t = entry.get("t")
        if isinstance(t, int) and not isinstance(t, bool):
            first_by_t.setdefault(t, entry)
    frames = []
    for second in range(last):
        frames.append(first_by_t.get(second) or {"t": second, "f": []})
    return frames


def replay(series: list[dict], checkpoints: set[int] | None = None) -> dict:
    """Apply frames from the frontend's initial state.

    Returns the final replay state, observation tree, per-frame ``state``
    name, patch failures, and player snapshots for ``checkpoints``.
    """
    frames = frame_entries(series)
    state = initial_state()
    observed: dict = {}
    state_names: list[Any] = []
    failures: list[dict] = []
    snapshots: dict[int, list[dict]] = {}
    checkpoint_meta: dict[int, dict] = {}
    wanted = checkpoints or set()
    for frame in frames:
        t = frame["t"]
        events = frame.get("f") or []
        if not isinstance(events, list):
            events = []
        state, observed, error = apply_frame(state, frame.get("d"), events, observed, t)
        if error:
            failures.append({"t": t, "error": error})
        state_names.append(state.get("state"))
        if t in wanted:
            snapshots[t] = export_players(state, observed, t)
            checkpoint_meta[t] = {
                "teamsAlive": state.get("teamsAlive"),
                "totalTeams": state.get("totalTeams"),
            }
    return {
        "state": state,
        "observed": observed,
        "state_names": state_names,
        "failures": failures,
        "snapshots": snapshots,
        "checkpoint_meta": checkpoint_meta,
        "frame_count": len(frames),
        "omitted_t": series[-1].get("t") if series else None,
    }


def _leaf(player: Any, observed: Any, t: int, *path: str) -> dict:
    value = player
    seen = observed
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return {"value": None, "observed_at": None, "gap": None}
        value = value[part]
        if isinstance(seen, dict) and part in seen:
            seen = seen[part]
        else:
            seen = None
    if isinstance(seen, int) and not isinstance(seen, bool):
        observed_at = seen
        gap = t - observed_at
    else:
        observed_at = None
        gap = None
    if value is JS_UNDEFINED:
        value = None
        observed_at = None
        gap = None
    return {"value": value, "observed_at": observed_at, "gap": gap}


def _coords(player: Any, observed: Any, t: int) -> dict:
    axes = {}
    observed_at = {}
    gap = {}
    for axis in ("x", "y", "z"):
        leaf = _leaf(player, observed, t, "pos", axis)
        axes[axis] = leaf["value"]
        observed_at[axis] = leaf["observed_at"]
        gap[axis] = leaf["gap"]
    return {"value": axes, "observed_at": observed_at, "gap": gap}


def export_players(state: dict, observed: dict, t: int) -> list[dict]:
    """Compact player rows. Carried values keep the ``t`` of their last write."""
    players = state.get("players")
    if not isinstance(players, dict):
        return []
    player_obs = observed.get("players") if isinstance(observed, dict) else None
    if not isinstance(player_obs, dict):
        player_obs = {}
    rows = []
    for player_id, player in players.items():
        if not isinstance(player, dict):
            continue
        obs = player_obs.get(player_id)
        if not isinstance(obs, dict):
            obs = {}
        health_current = _leaf(player, obs, t, "currentHealth")
        health_max = _leaf(player, obs, t, "maxHealth")
        shield_current = _leaf(player, obs, t, "shieldHealth")
        shield_max = _leaf(player, obs, t, "shieldMaxHealth")
        status = _leaf(player, obs, t, "status")
        name = _leaf(player, obs, t, "name")
        team_id = _leaf(player, obs, t, "teamId")
        team_name = _leaf(player, obs, t, "teamName")
        rows.append(
            {
                "nucleusHash": player.get("nucleusHash", player_id),
                "name": name,
                "teamId": team_id,
                "teamName": team_name,
                "pos": _coords(player, obs, t),
                "health": {"current": health_current, "max": health_max},
                "shields": {"current": shield_current, "max": shield_max},
                "status": status,
            }
        )
    rows.sort(
        key=lambda row: (
            row["teamId"]["value"] is None,
            row["teamId"]["value"] if isinstance(row["teamId"]["value"], int) else 10**9,
            row["name"]["value"] or "",
            row["nucleusHash"] or "",
        )
    )
    return rows


def playing_bounds(state_names: list[Any]) -> tuple[int, int] | None:
    """First and last frame index whose reconstructed ``state`` is ``Playing``.

    Frame index equals ``t`` because the frontend's expanded timeline is
    indexed by ``t``.
    """
    indexes = [index for index, name in enumerate(state_names) if name == "Playing"]
    if not indexes:
        return None
    return indexes[0], indexes[-1]


def default_checkpoints(state_names: list[Any]) -> list[int]:
    bounds = playing_bounds(state_names)
    if bounds is None:
        raise ValueError("reconstructed state never entered Playing")
    start, end = bounds
    middle = start + (end - start) // 2
    return [start, middle, end]


def unsupported_deltas(delta: Any, path: str = "$", found: list[str] | None = None, limit: int = 10) -> list[str]:
    """Return paths of delta nodes the bundled patcher cannot apply.

    Atomic one- and two-element arrays, object deletes ``[old, 0, 0]``,
    and array moves ``[value, index, 3]`` are supported. Text diffs and
    anything else are not. Values inside an atomic delta are data, not
    further deltas.
    """
    if found is None:
        found = []
    if len(found) >= limit:
        return found
    if isinstance(delta, list):
        if len(delta) in (1, 2):
            return found
        if len(delta) == 3 and delta[2] in (0, 3):
            return found
        found.append(path)
        return found
    if isinstance(delta, dict):
        if not delta or ("_t" in delta and delta.get("_t") != "a"):
            found.append(path)
            return found
        for key, child in delta.items():
            if key == "_t":
                continue
            unsupported_deltas(child, f"{path}.{key}", found, limit)
        return found
    found.append(path)
    return found
