# Map rendering

The plot uses the Overstat replay transform from
`https://overstat.gg/js/9107.be2636c4.js` and the `MAP_SIZE` table in
`https://overstat.gg/js/9090.a1b18f4e.js`. Bounds are not estimated from
player positions.

`mapCordX` / `mapCordY` use `parseInt` on the coordinate, then
`1024 * ((value + offset) / size) + 1024`. `mapCordY` negates the
coordinate. Radius is `1024 * (radius / size.x)` and is not truncated.
The image is the 2048 view: `/maps_2048/<telemetry map id>.webp`.

The current ring follows `currentRingLocation`: while a stage is
closing, center and radius lerp from start to end by
`(t - start) / (end - start)`. While waiting, the next stage's start
center and start size are shown. After the last stage, no circle is
drawn. Future rings stay hidden.

Cached images, all 2048×2048, from `https://overstat.gg/maps_2048/`:

- `mp_rr_desertlands_mu5` (Worlds Edge size 45000, offset 0, 0)
- `mp_rr_district_mu1` (42500 × 43000, offset 0, 0)
- `mp_rr_olympus_mu3` (45000 × 44000, offset 8000, −2000)
- `mp_rr_tropic_island_mu2_landscape` (51000, offset −500, −1000)

`mp_rr_district_mu1_night` has no `MAP_SIZE` entry and no image.
The UI says the map is unsupported and does not stretch another image.

Game 123349 checked against the recorded rings: at t=100 the stage-0
ring is waiting (125s, radius 67999.9453); at t=355 it is halfway
closed (radius 47249.97265); at t=550 stage 1 is waiting.

The same camera draws the image, player marks, labels, and the ring.
Zoom is 1×–8×, centered on the pointer, and drag pans. Marks and the
ring stroke stay a constant screen size. Reset returns to the full
map. A different map id clears the camera. Seeking does not.

## Facing arrows

An arrow is the player's yaw as held at that replay second (`angles.y`).
It is degrees in [-180, 180], 0 along replay +x, increasing
counter-clockwise. The arrow uses the same projection as the mark, so zoom
and pan turn it with the map. Alive and downed players can show an arrow;
an eliminated player, or one without a reliable yaw, is a dot.

### Why a yaw is held between writes

The replay is a jsondiffpatch stream. A yaw is written only when it
changes, and each modify delta records `[old, new]`, so every write says
which value it replaces. Checked over all 100 local replays (51 original,
49 expansion; 1,383,401 yaw writes):

| Check | Result |
| --- | --- |
| Writes whose `old` equals the held value, after the player's first write | 1,381,933 of 1,381,933 |
| ...of which the gap since the previous write spans seconds with no delta | 8,556, all matched |
| ...of which are the first write after a death | 2,826, all matched |
| Writes that repeat an unchanged value | 0 |
| Writes whose `old` differs from the held value | 1,468, all the player's first write (974 in the first 5 s of play, 46 more by 60 s, 448 before play) |

So a yaw that is not rewritten has not changed, and it is held with no
time limit. The 5-second expiry used before showed a dot for 26% of
alive or downed player-seconds that had a continuous yaw. The mismatches
come from snapshots: a yaw that arrived inside a newly added player object
disagreed with that player's first real write 28% of the time, and the
same deltas also disagree on position and health.

### When a held yaw is dropped

`tactical.FacingTrack` updates once per frame, using only frames up to that
second. A yaw becomes reliable at the player's first modify write. It is
dropped, and the player shows a dot until the next modify write, when:

- the player, its `angles` object or `angles.y` is added, replaced or
  removed (an added value is a snapshot and is not trusted);
- the player returns to alive or downed from any other status (a respawn),
  unless the same frame writes the yaw;
- the patch fails, or the `players` object itself is replaced;
- the value is not a number in [-180, 180].

The arrow resumes at the next reliable yaw update, which is a modify write
whose new value is a number in [-180, 180]. Nothing else brings it back:
not time passing, not movement and not a status change. The respawn rule is
deliberately cautious. Yaw is written while a player is dead, and the first
write after a death matched the held value in every case measured, but the
game resets the player on respawn, so the old direction is not shown
until the stream confirms a new one.

The analysis scripts and their outputs are kept, gitignored, in
`data/facing_analysis/` (see its README).

Under this rule 99.83% of alive or downed player-seconds have an arrow
(73.52% under the 5-second rule). A dot usually lasts 1 second (90th
percentile 1 s). The longer ones are mostly one player per game in the first
16–23 s of play, before that player's first write.

`age_s` (seconds since the last write) is still sent with each yaw for
reference, but it does not expire it.

The arrow is not verified visibility. The replay does not establish what
that player can see, and yaw is not a line of sight. Viewing cones stay
off: a cone would need a field of view and map geometry, and this data
has neither. Nothing here infers facing from movement.
