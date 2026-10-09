# Scream and Run

A tModLoader mod: a horror survival "boss" you can't kill. Read the Love Letter and **Onryo**,
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
    │   ├── NPCs/   Onryo
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
- **Phase 1, Stalking** (first quarter, max 90 s): she pretends not to know where you are. She roams like a
  normal NPC (walks a stretch, stops and looks around, picks a new spot), and each spot drifts toward your
  rough area. She keeps her distance, from ~30 tiles down to ~12 by the end of the phase. If she can see you
  inside that distance, she just stands and stares. Touching her still kills you.
- **Phase 2, Hunting:** she heads for the **last place she saw or heard you**, faster than you can
  run without speed gear. If you're out of her line of sight for **4 s**, she loses you, searches
  around that spot for 8 s, then goes back to roaming toward your rough area until she spots you again.
- **Phase 3, Frenzy** (last 60 s, or the last third on short runs): faster, keeps track 6 s, and every
  8-11 s she **screams (loud audio cue + red strobe + "!")**, winds up for 1.25 s, and **teleports
  9-13 tiles behind you**.
- **Movement:** she never phases through blocks. A grid pathfinder lets her walk floors, step up ledges, and
  **crawl up walls and along ceilings**, through open space only. She drops through platforms. If she
  stops making progress, she **jumps** after 1 s, picks another route and jumps again after 2 s, and after
  3 s (or if you get 120+ tiles away, e.g. Magic Mirror) she reappears out of sight closer to you.
- **Can't be killed:** no damage, no knockback, immune to every debuff, minions ignore her, and she
  never despawns until the event ends. If something deletes her anyway, she respawns.
- **Death:** contact kills via `KillMe`, skipping defense, armor, i-frames and dodges, with a custom
  death message. If another mod's revive effect saves you, she retries for 15 ticks.
  With **One-shot** off, a touch takes 40% max life and she backs off.
- **Win:** the timer hits 0, she fades away ("...Fine. I'll find you tomorrow.") and drops
  **Onryo's Torn Ribbon** (vanity hat). If you die, she vanishes and the event ends; read the
  letter to try again.

### Detection

Her notice radius starts at **45 tiles**, then:

- **Crouching still** (hold Down, not moving, on the ground): × 0.5
- **Light**: × 0.6 in full darkness, scaling up to × 1 in bright light. Your own torch counts!
- **Noise** (0-100%, fades over ~4 s): × (1 + 1.5 × noise). Mining, swinging or shooting, and running
  all add noise. At **50%+ noise she hears you through walls** within her radius.
- In Hunting she needs line of sight *and* range to spot you. Once tracking, her sight range is 1.5×.

### Hiding Locker

- **Right-click** to get in, **Jump** or right-click to get out. Inside you're invisible to her
  and can't move or use items.
- **You trade sight for safety.** The screen goes solid black except for four thin vent slits, and through
  them you see **only sky** (or dark rock underground), never the area around the locker. The HUD shows
  only the clock: no presence bar, no awareness, no noise meter. You have to listen and watch the vents:
  - **Footsteps** when she walks within ~16 tiles, positioned left/right so you can tell her side.
  - **Her shadow** crosses the vents on the side she's on as she passes the locker.
  - **Knocking**: when she stops right outside, a burst of 3-5 loud knocks and then a rattle of the door,
    each with a jolt. Sometimes her **red eyes** show in a vent.
  - The heartbeat never drops below a nervous pace while you're inside, but it's quieter so you can hear her.
- If she's within 7 tiles of your locker, she builds suspicion. It takes **6 s if she saw you get in, 12 s if
  not**. When it fills, or once you've been inside **40 s total** (getting out and back in doesn't reset that quickly),
  she knows. She walks straight to the locker, and when she reaches it she rips it open (jumpscare and death,
  even with one-shot off). There's no meter for this: footsteps getting louder and not passing are your warning.

### Atmosphere and UI

- A vignette that tightens and pulses red as she gets closer (the presence range is 60 tiles).
- A heartbeat that speeds up from ~0.8 to ~3.5 beats/s with proximity, and races when she's found your locker.
- Distortion when she's near: torn scanlines, static, red flicker and camera shake (client config toggle).
- **Jumpscare** when she catches you: a guro-kawaii "album cover" fills the screen. She's drawn anime-style,
  with heart pupils, a fanged grin, blood drool, stitches, a bandaid and a dashed "cut here" line on her neck.
  It has a drippy ONRYO logo and ずっと一緒だよ ("we'll be together forever") down the side. Blood splatters
  burst across the screen and spread, and the screen pulses red. Turn off the white flash and shake in the
  client config.
- HUD at the top center: countdown, phase, a **presence bar** (far / near / close / RIGHT BEHIND YOU),
  her awareness (lost you / following your trail / sees you), a noise meter, and crouch/darkness tags.
  All of it except the clock is hidden while you're in a locker.
- **Map:** during the event the minimap is covered in blood (the overlay map style switches to the minimap),
  and the fullscreen map is painted over completely: no hover text, and no map icons, so no pylon teleports.

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
- **Sky tint, minimap blood and hidden map icons:** only while the event is active.
- **Music:** uses `BossHigh` priority, so it overrides boss music (including Calamity's) while the event runs.

Onryo is not flagged as a boss (no boss bar, no boss-alive effects), deals no contact damage
through the normal damage pipeline (so difficulty scaling can't touch her), and isn't counted for spawn caps.

## What's been verified, and known limitations

**Verified** against tModLoader **v2026.08.3.0** (latest stable, Terraria 1.4.4.9), on Linux with the .NET 8 SDK:

- The mod **compiles with zero warnings** and **packages to `ScreamAndRun.tmod`** through tModLoader's own build.
- It **loads on a headless tModLoader server** that generates a world: content, configs, recipes and localization
  all register, with no warnings or errors in the log.
- Runtime assumptions checked against decompiled tModLoader:
  - the vanilla minimap layer name;
  - the sprite batch state in the fullscreen-map hook;
  - that packaging keeps audio file names, so sound drop-ins are found;
  - that anything under a `/Music/` folder is registered as music.
- The pathfinder is unit-tested against a fake tile map: climbing walls, shafts, platforms, sealed rooms,
  falling, and ~4 ms worst case.

**Not verified:**

- **Nothing has been played in the game client.** The client needs Terraria's own art and audio, which come with your
  Steam copy and aren't available here. So nothing visual or interactive has been seen working: the boss
  moving, the HUD, the vignette, the locker, the jumpscare, or the event start to finish.
- **Gameplay tuning:** speeds, detection numbers, timings, and whether the HUD overlaps other UI (e.g. Calamity's
  meters) at your resolution. All the numbers are constants at the top of `Onryo.cs` / `HauntPlayer.cs`.
- **Calamity:** it wasn't loaded alongside this mod. The mod doesn't reference it, though.

**Known limitations:**

- **Multiplayer is unsupported.** Noise and hiding are client-side, so the event is single-player only.
- **The map overlay is switched off during the event.** It's drawn into the world itself, where it can't be
  covered, so it's swapped for the (blood-covered) minimap and your setting is restored when the event ends. If you
  save settings mid-event, "minimap" is what gets saved.
- **Some death cancels still work.** Journey mode god mode and mods that cancel death in `PreKill` may still save
  you. She retries for 15 ticks, but a mod that always cancels death wins.
- **Lockers don't protect you from other enemies.** They can still hit you inside one.
