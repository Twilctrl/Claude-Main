using Terraria.Audio;
using Terraria.ID;
using Terraria.ModLoader;

namespace ScreamAndRun.Common.Audio
{
	/// <summary>
	/// Every sound and the music track the mod plays. Each one uses a file from
	/// Assets/Sounds (or Assets/Music) when you've added one, and otherwise falls back
	/// to a vanilla placeholder. See Assets/README.md for the file names.
	/// </summary>
	public static class ScreamAudio
	{
		public static SoundStyle Heartbeat { get; private set; }
		public static SoundStyle Noticed { get; private set; }
		public static SoundStyle Spotted { get; private set; }
		public static SoundStyle PhaseChange { get; private set; }
		public static SoundStyle TeleportCue { get; private set; }
		public static SoundStyle Teleport { get; private set; }
		public static SoundStyle Jumpscare { get; private set; }
		public static SoundStyle LockerOpen { get; private set; }
		public static SoundStyle LockerClose { get; private set; }
		public static SoundStyle GiveUp { get; private set; }
		public static SoundStyle Footstep { get; private set; }
		public static SoundStyle Knock { get; private set; }

		private static readonly string[] AudioExtensions = { ".ogg", ".wav", ".mp3" };
		private static bool hasCustomMusic;
		private static int musicSlot = -1;

		internal static void Load(Mod mod) {
			Heartbeat = Pick(mod, "Heartbeat", SoundID.Dig with { Pitch = -0.9f, Volume = 0.8f });
			Noticed = Pick(mod, "Noticed", SoundID.Roar with { Pitch = -0.6f, Volume = 0.7f });
			Spotted = Pick(mod, "Spotted", SoundID.Roar with { Pitch = 0.6f, Volume = 0.5f });
			PhaseChange = Pick(mod, "PhaseChange", SoundID.ForceRoarPitched with { Volume = 0.7f });
			TeleportCue = Pick(mod, "TeleportCue", SoundID.ScaryScream with { Volume = 1f, Pitch = 0.2f });
			Teleport = Pick(mod, "Teleport", SoundID.Item8 with { Pitch = -0.5f });
			Jumpscare = Pick(mod, "Jumpscare", SoundID.ScaryScream with { Volume = 1f, Pitch = 0.5f, MaxInstances = 3 });
			LockerOpen = Pick(mod, "LockerOpen", SoundID.DoorOpen);
			LockerClose = Pick(mod, "LockerClose", SoundID.DoorClosed);
			GiveUp = Pick(mod, "GiveUp", SoundID.Item6 with { Pitch = -0.4f });
			Footstep = Pick(mod, "Footstep", SoundID.Run with { Pitch = -0.3f, MaxInstances = 4 });
			Knock = Pick(mod, "Knock", SoundID.Dig with { Pitch = -0.2f, Volume = 1f });

			hasCustomMusic = HasAudioFile(mod, "Assets/Music/Theme");
		}

		/// <summary>The event music slot: Assets/Music/Theme if present, otherwise vanilla "Eerie".</summary>
		public static int Music {
			get {
				if (!hasCustomMusic)
					return MusicID.Eerie;
				if (musicSlot < 0)
					musicSlot = MusicLoader.GetMusicSlot(ModContent.GetInstance<ScreamAndRun>(), "Assets/Music/Theme");
				return musicSlot;
			}
		}

		private static SoundStyle Pick(Mod mod, string name, SoundStyle fallback) {
			string path = "Assets/Sounds/" + name;
			return HasAudioFile(mod, path) ? new SoundStyle($"{mod.Name}/{path}") : fallback;
		}

		private static bool HasAudioFile(Mod mod, string pathWithoutExtension) {
			foreach (string ext in AudioExtensions) {
				if (mod.FileExists(pathWithoutExtension + ext))
					return true;
			}
			return false;
		}
	}
}
