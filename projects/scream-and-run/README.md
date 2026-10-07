# Scream and Run

A tModLoader mod: a horror survival "boss" you can't kill. Read the Love Letter and **Tsubaki**,
the transfer student, notices you. One touch from her is death. Survive 5 minutes (configurable)
and she gives up. Original character, no existing IP.

```
scream-and-run/
├── README.md                     <- you are here
├── tools/generate_placeholders.py   regenerates every placeholder texture (Pillow)
└── ScreamAndRun/                 <- the mod itself; goes in ModSources/ScreamAndRun
    ├── build.txt, description.txt, icon.png, ScreamAndRun.csproj
    ├── ScreamAndRun.cs                 Mod class
    ├── Assets/                         ALL art + audio. Drop real files here (see Assets/README.md)
    │   ├── Textures/{NPCs,Items,Tiles,UI}/
    │   ├── Sounds/                     empty: vanilla placeholders until you add files
    │   └── Music/                      empty: vanilla "Eerie" until you add Theme.ogg
    ├── Common/
    │   ├── AI/HuntPathfinder.cs        tile-grid BFS: she walks and crawls through open space only
    │   ├── Audio/ScreamAudio.cs        sound registry, custom file or vanilla fallback
    │   ├── Configs/ScreamConfig.cs     server config (time, speed, one-shot) + client effects config
    │   ├── Globals/MiningNoiseGlobalTile.cs
    │   ├── Players/HauntPlayer.cs      noise, visibility, hiding, heartbeat, forced death
    │   ├── Systems/HauntEventSystem.cs timer, phases, start/end, sky dimming
    │   ├── Systems/HauntSceneEffect.cs music
    │   └── UI/HauntUISystem.cs         vignette, distortion, HUD, map blood, jumpscare
    ├── Content/
    │   ├── Items/  LoveLetter, HidingLockerItem, TornRibbon (reward)
    │   ├── NPCs/   Tsubaki
    │   └── Tiles/  HidingLocker
    └── Localization/en-US_Mods.ScreamAndRun.hjson   all text
```

## Build and install

You need **tModLoader** (free on Steam, latest stable branch). The .NET SDK it builds with comes
with tModLoader, so you don't need to install .NET separately for an in-game build.

1. **Run tModLoader once** so it creates its folders, then quit.
2. **Find your ModSources folder:**
   - Windows: `Documents\My Games\Terraria\tModLoader\ModSources`
   - Linux / Steam Deck: `~/.local/share/Terraria/tModLoader/ModSources`
   - macOS: `~/Library/Application Support/Terraria/tModLoader/ModSources`

   (In game: Workshop → Develop Mods → "Open Sources" opens it.)
3. **Copy the `ScreamAndRun` folder into ModSources**, so you have `ModSources/ScreamAndRun/build.txt`.
   The folder name must stay `ScreamAndRun`. A symlink works too if you want to keep editing it in
   this repo:
   - Linux/macOS: `ln -s /path/to/repo/projects/scream-and-run/ScreamAndRun ~/.local/share/Terraria/tModLoader/ModSources/ScreamAndRun`
   - Windows (admin cmd): `mklink /D "%USERPROFILE%\Documents\My Games\Terraria\tModLoader\ModSources\ScreamAndRun" C:\path\to\repo\projects\scream-and-run\ScreamAndRun`
4. **Build:** in game, Workshop → Develop Mods → **Build + Reload** next to "Scream and Run".
   - Or from a terminal, if you have the .NET 8 SDK: `cd ModSources/ScreamAndRun && dotnet build`.
     That works because tModLoader puts `tModLoader.targets` in ModSources, and it writes
     `ScreamAndRun.tmod` to the `Mods` folder.
   - To build in an IDE (Visual Studio / Rider), open `ScreamAndRun.csproj` from inside ModSources.
5. **Enable** it under Workshop → Manage Mods (Build + Reload enables it for you) and load a world.

### Playing

| Item | Recipe |
| --- | --- |
| Love Letter (starts the event; not consumed) | 3 Silk + 1 Lens @ Work Bench |
| Hiding Locker (furniture, 2x3) | 3 Iron/Lead Bars + 8 any Wood @ Work Bench |

Place a few lockers first, then read the letter.

## How it plays

- **Start.** The sky goes dark red, the music changes, and "She noticed you." appears. She spawns
  40-60 tiles away, out of sight. Only one event runs at a time. **Single-player only**: the letter
  won't work in multiplayer.
- **Phase 1, Stalking** (first quarter, max 90 s): she paces at walking speed on one side of you,
  gradually closing from ~55 to ~16 tiles. When she's within 12 tiles she just stands and stares.
  Touching her still kills you.
- **Phase 2, Hunting:** she heads for the **last place she saw or heard you**, faster than you can
  run without speed gear. If you're out of her line of sight for **4 s**, she loses you, searches
  around that spot for 8 s, then drifts around your general area until she finds you again.
- **Phase 3, Frenzy** (last 60 s, or the last third on short runs): faster, keeps track 6 s, and every
  8-11 s she **screams (loud audio cue + red strobe + "!")**, winds up for 1.25 s, and **teleports
  9-13 tiles behind you**.
- **Movement:** she never phases through blocks. A grid pathfinder lets her walk floors and
  **crawl up walls and along ceilings**, through open space only. She drops through platforms. If she's
  stuck for ~4 s, or you get 120+ tiles away (Magic Mirror, etc.), she reappears out of sight closer to you.
- **Can't be killed:** no damage, no knockback, immune to every debuff, minions ignore her, and she
  never despawns until the event ends. If something deletes her anyway, she respawns.
- **Death:** contact kills via `KillMe`, skipping defense, armor, i-frames and dodges, with a custom
  death message. If another mod's revive effect saves you, she retries for 15 ticks.
  With **One-shot** off, a touch takes 40% max life and she backs off.
- **Win:** the timer hits 0, she fades away ("...Fine. I'll find you tomorrow.") and drops
  **Tsubaki's Torn Ribbon** (vanity hat). If you die, she vanishes and the event ends; read the
  letter to try again.

### Detection

Her notice radius starts at **30 tiles**, then:

- **Crouching still** (hold Down, not moving, on the ground): × 0.5
- **Light**: × 0.45 in full darkness, scaling up to × 1 in bright light. Your own torch counts!
- **Noise** (0-100%, fades over ~4 s): × (1 + 1.5 × noise). Mining, swinging or shooting, and running
  all add noise. At **50%+ noise she hears you through walls** within her radius.
- In Hunting she needs line of sight *and* range to spot you. Once tracking, her sight range is 1.5×.

### Hiding Locker

- **Right-click** to get in, **Jump** or right-click to get out. Inside you're invisible to her
  and can't move or use items. Your view shrinks to the vent slits.
- If she's within 7 tiles of your locker, she builds suspicion. It takes **6 s if she saw you get in, 12 s if
  not**. When it fills, or once you've been inside **40 s total** (getting out and back in doesn't reset that quickly),
  she knows. She walks straight to the locker, and when she reaches it she rips it open (jumpscare and death,
  even with one-shot off). The HUD bar shows how close you are to that.

### Atmosphere and UI

- A vignette that tightens and pulses red as she gets closer (the presence range is 60 tiles).
- A heartbeat that speeds up from ~0.8 to ~3.5 beats/s with proximity, and races when she's found your locker.
- Distortion when she's near: torn scanlines, static, red flicker and camera shake (client config toggle).
- HUD at the top center: countdown, phase, a **presence bar** (far / near / close / RIGHT BEHIND YOU),
  her awareness (lost you / following your trail / sees you), a noise meter, and crouch/darkness tags.
- **Map:** during the event the minimap and map overlay are hidden under blood, and the fullscreen map
  is painted over completely, including hover text.

## Config (Settings → Mod Configuration)

**Scream and Run: Gameplay** (server-side; changes apply immediately):

| Option | Default | Range |
| --- | --- | --- |
| Survival time (seconds) | 300 | 30-1800 |
| Her speed multiplier | 1.0 | 0.25-3.0 |
| One-shot kills | On | |

**Scream and Run: Effects** (client): screen distortion and shake; jumpscare flash.

## Calamity and other mods

The mod has no reference to Calamity or any other mod and changes no content outside its own. The only
global hooks are:

- **Mining noise:** reads tile breaks near you and changes nothing.
- **Sky tint and minimap hiding:** only while the event is active.
- **Music:** uses `BossHigh` priority, so it overrides boss music (including Calamity's) while the event runs.

Tsubaki is not flagged as a boss (no boss bar, no boss-alive effects), deals no contact damage
through the normal damage pipeline (so difficulty scaling can't touch her), and isn't counted for spawn caps.

## Not tested, and known limitations

I wrote this in a cloud container without tModLoader, because the network policy blocks GitHub, where
tModLoader is distributed. So:

- **It has not been compiled against tModLoader, or run in game.** Every C# file passes a Roslyn
  syntax check, and the pathfinder was unit-tested against a fake tile map: climbing walls, shafts,
  platforms, sealed rooms, falling, and ~4 ms worst case. Everything else was written from my knowledge of the
  tModLoader 1.4.4 API. If your first build fails, these are the most likely spots, all API names
  that tModLoader has renamed or changed between versions:
  - `PlayerDeathReason.ByCustomReason(string)` in `HauntPlayer.CaughtBy`. Newer versions may want
    `ByCustomReason(NetworkText.FromLiteral(text))`.
  - `PlayerDrawLayerLoader.Layers` in `HauntPlayer.HideDrawLayers`.
  - `Mod.FileExists` in `ScreamAudio`.
  - `SoundID.ForceRoarPitched` / `SoundID.ScaryScream` placeholders. Swap for any other `SoundID` if missing.
  - `ModNPC.CanFallThroughPlatforms` / `CanBeHitByNPC` signatures.
- **Gameplay tuning is untested:** speeds, detection numbers, timings and whether the HUD overlaps
  other UI (e.g. Calamity's meters) at your resolution. All the numbers are constants at the top of
  `Tsubaki.cs` / `HauntPlayer.cs`.
- **Map pylon teleport:** the fullscreen map is covered, but a blind click on a pylon's position may
  still teleport you. Teleporting far away just makes her relocate near you anyway.
- **Multiplayer is unsupported:** noise and hiding are client-side, so the event is single-player only.
- Journey mode god-mode and mods that cancel death in `PreKill` may still save you. She retries for
  15 ticks, but a mod that always cancels death wins.
- Other enemies can still hit you inside a locker.
