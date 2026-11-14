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
