#!/usr/bin/env python3
"""Show the actual inputs behind one held-out checkpoint's top squads.

    python explain_checkpoint.py --game 123633 --elapsed 0:00 [--top 4]

Uses the v2 export bundle (held-out probabilities) and its evaluation model
(train + validation fit). ``--elapsed`` picks the most recent checkpoint at
or before that time. Per-feature ``w*z`` terms show how the linear score is
assembled. They are not causal effects: several features are tied together
(status counts sum to squad size; ``no_standing`` moves with the ``*_missing``
flags), so weight can shift between them without changing predictions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from collect_matches import stats_game_for
from dashboard_data import HELDOUT_BUNDLE, checkpoint_at
from features import game_examples, load_accepted
from train_model import vectorize


def _seconds(text: str) -> int:
    if ":" in text:
        minutes, seconds = text.split(":")
        return int(minutes) * 60 + int(seconds)
    return int(text)


def explain(data_dir: Path, game_id: int, elapsed: int, top: int) -> str:
    root = data_dir / "exports" / HELDOUT_BUNDLE
    model = json.loads((root / "evaluation_model.json").read_text())
    entries = [e for e in json.loads((root / "heldout_predictions.json").read_text()) if e["game_id"] == game_id]
    if not entries:
        raise SystemExit(f"game {game_id} has no held-out predictions (not a test game)")
    entries.sort(key=lambda e: e["t"])
    chosen = checkpoint_at([{"t": e["t"], "elapsed": e["elapsed_s"]} for e in entries], elapsed)
    if chosen is None:
        raise SystemExit("no checkpoint at or before that time")
    entry = entries[chosen["index"]]
    record = next(r for r in load_accepted(data_dir)[0] if r["game_id"] == game_id)
    payload = json.loads(Path(record["raw_path"]).read_text(encoding="utf-8-sig"))
    group = next(g for g in game_examples(payload, record, stats_game_for(record, data_dir))["groups"] if g["t"] == entry["t"])
    rows = {row["team"]: row for row in group["rows"]}
    squads = sorted(entry["squads"], key=lambda s: -s["probabilities"]["model_constrained"])[:top]
    names = model["features"]
    weights = dict(zip(names, model["weights"]))

    lines = [
        f"game {game_id}, prediction time {entry['elapsed_s'] // 60:02d}:{entry['elapsed_s'] % 60:02d} "
        f"(t {entry['t']}), {entry['in_contention']} of {entry['lobby_size']} squads in contention, eligibility {entry['eligibility']}",
        "",
        "squad".ljust(26) + "".join(f"{(s['team_name'] or str(s['team_id']))[:12]:>14}" for s in squads),
        "P(model, constrained)".ljust(26) + "".join(f"{s['probabilities']['model_constrained']:>14.4f}" for s in squads),
        "P(contention uniform)".ljust(26) + "".join(f"{s['probabilities']['uniform_contention']:>14.4f}" for s in squads),
        "score w.z (sum)".ljust(26)
        + "".join(f"{sum(w * z for w, z in zip(model['weights'], vectorize(rows[s['team_id']], model['scaler']))):>14.3f}" for s in squads),
        "",
        "raw feature values".ljust(26) + "   (weight)",
    ]
    for name in names:
        values = []
        for squad in squads:
            value = rows[squad["team_id"]]["features"][name]
            values.append("unknown" if value is None else f"{value:.3f}")
        lines.append(f"  {name:24s}" + "".join(f"{v:>14}" for v in values) + f"   ({weights[name]:+.3f})")
    lines.append("")
    lines.append("w*z terms (standardized value times weight; descriptive, not causal)")
    for index, name in enumerate(names):
        terms = [weights[name] * vectorize(rows[s["team_id"]], model["scaler"])[index] for s in squads]
        lines.append(f"  {name:24s}" + "".join(f"{v:>14.3f}" for v in terms))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--game", type=int, required=True)
    parser.add_argument("--elapsed", default="0:00", help="MM:SS or seconds since Playing start")
    parser.add_argument("--top", type=int, default=4)
    args = parser.parse_args(argv)
    print(explain(args.data_dir, args.game, _seconds(args.elapsed), args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
