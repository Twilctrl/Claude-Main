# Assets: drop your art and audio here

All the art and audio the mod uses is in this folder. Everything here now is a placeholder made by
`tools/generate_placeholders.py`. To replace something, overwrite the file with the same name and
size, then rebuild the mod.

## Textures (`Textures/`)

| File | Size | Notes |
| --- | --- | --- |
| `NPCs/Onryo.png` | 40 x 336 | 6 frames stacked vertically, 40x56 each. **Faces left** (Terraria flips it). Frames: 0 idle, 1-4 walk cycle, 5 wind-up before a teleport. Her hitbox is 28x44, bottom-centered in the frame. |
| `Items/LoveLetter.png` | 28 x 20 | Summon item. |
| `Items/HidingLockerItem.png` | 20 x 30 | Locker in the inventory. |
| `Items/TornRibbon.png` | 26 x 18 | Reward item icon. |
| `Items/TornRibbon_Head.png` | 40 x 1120 | Worn ribbon: the vanilla head-armor sheet layout (20 frames of 40x56). |
| `Tiles/HidingLocker.png` | 36 x 54 | 2x3 tile sheet: 16x16 tiles with 2px padding. |
| `UI/Vignette.png` | any square | White with alpha. Transparent center, opaque edges. It's tinted when drawn. |
| `UI/BloodSplat.png` | any | Splatter with transparency. Used on the map and minimap. |
| `UI/Jumpscare.png` | 960 x 540 (any 16:9) | Full-screen jumpscare image, scaled to cover the screen. 16:9 avoids cropping on widescreen. |
| `../icon.png` | 80 x 80 | Mod icon in the mod list. The generator crops it from the jumpscare face. |

If you change a frame size or count, update `Main.npcFrameCount` / hitbox in `Content/NPCs/Onryo.cs`.

## Sounds (`Sounds/`)

No sound files ship with the mod. Each sound falls back to a vanilla placeholder until you add a file
with one of these names (`.ogg`, `.wav` or `.mp3`):

| Name | When it plays |
| --- | --- |
| `Heartbeat` | Each heartbeat, faster and louder as she gets closer (played twice per beat, the second slightly higher). |
| `Noticed` | The event starts ("She noticed you."). |
| `Spotted` | She spots you after having lost you. |
| `PhaseChange` | Stalking -> Hunting, and Hunting -> Frenzy. |
| `TeleportCue` | The loud warning ~1.25 s before a frenzy teleport. Make it loud and distinctive. |
| `Teleport` | She vanishes / reappears. |
| `Jumpscare` | She catches you. |
| `LockerOpen` / `LockerClose` | Getting in and out of a locker, and when she rips it open. |
| `GiveUp` | You survived. |
| `Footstep` | Each of her steps while she's near the locker you're hiding in (played positionally). |
| `Knock` | **One** knock on your locker. It's played 3-5 times in a burst when she stands outside. |
| `Rattle` | The door rattle that ends each knock burst. |

Example: `Sounds/Heartbeat.ogg`.

## Music (`Music/`)

Add `Music/Theme.ogg` (or `.wav` / `.mp3`) and it plays during the event. Until then the vanilla
"Eerie" track plays. tModLoader registers any audio under a `Music/` folder as music.
