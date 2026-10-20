using System;
using System.IO;
using Microsoft.Xna.Framework;
using ScreamAndRun.Common.Audio;
using ScreamAndRun.Common.Configs;
using ScreamAndRun.Common.Net;
using ScreamAndRun.Common.UI;
using ScreamAndRun.Content.Items;
using ScreamAndRun.Content.NPCs;
using Terraria;
using Terraria.Audio;
using Terraria.ID;
using Terraria.Localization;
using Terraria.ModLoader;
using Terraria.ModLoader.IO;

namespace ScreamAndRun.Common.Systems
{
	public enum HauntPhase
	{
		None,
		Stalking,
		Hunting,
		Frenzy,
	}

	public enum HauntEndReason
	{
		Survived,
		PlayerDied,
		Cancelled,
	}

	/// <summary>
	/// Owns the event: the countdown, the phases, spawning Onryo and ending everything.
	/// In multiplayer the server runs all of this and keeps clients in sync; she hunts the
	/// player who read the Love Letter (the target). Single-player runs the same code locally.
	/// </summary>
	public class HauntEventSystem : ModSystem
	{
		public static readonly Color MessageColor = new(220, 30, 50);
		private static readonly Color VictoryColor = new(255, 200, 210);

		public static bool Active { get; private set; }
		public static int TicksRemaining { get; private set; }
		public static int TotalTicks { get; private set; }
		/// <summary>The player she's hunting.</summary>
		public static int TargetIndex { get; private set; } = -1;
		/// <summary>Someone has survived her in this world (saved). Boss Checklist's "downed" check.</summary>
		public static bool SurvivedOnryo { get; private set; }

		/// <summary>0 to 1: how dark the sky is. Fades in and out rather than snapping.</summary>
		public static float Dim { get; private set; }

		private static int bossIndex = -1;
		/// <summary>The player's map style before we swapped "overlay" for the (blood-covered) minimap.</summary>
		private static int savedMapStyle = -1;
		private static HauntPhase lastPhase;
		private static int syncTimer;

		public static int StalkTicks => Math.Min(TotalTicks / 4, 90 * 60);
		public static int FrenzyTicks => Math.Min(60 * 60, TotalTicks / 3);
		public static int ElapsedTicks => TotalTicks - TicksRemaining;

		public static Player Target => TargetIndex >= 0 && TargetIndex < Main.maxPlayers ? Main.player[TargetIndex] : null;

		/// <summary>True on the machine of the player she's hunting: they get the full horror treatment.</summary>
		public static bool LocalIsTarget => Active && !Main.dedServ && TargetIndex == Main.myPlayer;

		public static HauntPhase Phase {
			get {
				if (!Active)
					return HauntPhase.None;
				if (TicksRemaining <= FrenzyTicks)
					return HauntPhase.Frenzy;
				if (ElapsedTicks < StalkTicks)
					return HauntPhase.Stalking;
				return HauntPhase.Hunting;
			}
		}

		/// <summary>0 at the start of the event, 1 once stalking ends.</summary>
		public static float StalkProgress => StalkTicks <= 0 ? 1f : MathHelper.Clamp(ElapsedTicks / (float)StalkTicks, 0f, 1f);

		public static Onryo Boss {
			get {
				if (bossIndex < 0 || bossIndex >= Main.maxNPCs)
					return null;
				NPC npc = Main.npc[bossIndex];
				return npc.active && npc.ModNPC is Onryo t ? t : null;
			}
		}

		public static bool CanStart(Player player) =>
			!Active
			&& player.active
			&& !player.dead
			&& !NPC.AnyNPCs(ModContent.NPCType<Onryo>());

		/// <summary>Called by the Love Letter on the reader's machine.</summary>
		public static void RequestStart(Player player) {
			if (ScreamNet.IsClient) {
				ModPacket packet = ScreamNet.Create(ScreamPacket.RequestStart);
				packet.Write((byte)player.whoAmI);
				packet.Send();
			}
			else {
				Start(player);
			}
		}

		/// <summary>Server / single-player only.</summary>
		public static void Start(Player player) {
			if (!ScreamNet.Authoritative || !CanStart(player))
				return;

			TotalTicks = Math.Max(30, ScreamConfig.Instance.SurvivalSeconds) * 60;
			TicksRemaining = TotalTicks;
			TargetIndex = player.whoAmI;
			Active = true;
			lastPhase = HauntPhase.Stalking;

			SpawnBoss(player, 40, 60);
			SyncState();
			Announce("Messages.Noticed", "Messages.Noticed", 210, AnnounceSound.Noticed, MessageColor);
		}

		/// <summary>Server / single-player only.</summary>
		public static void End(HauntEndReason reason) {
			if (!ScreamNet.Authoritative || !Active)
				return;
			Active = false;
			Player player = Target;
			Onryo boss = Boss;
			bossIndex = -1;

			switch (reason) {
				case HauntEndReason.Survived:
					boss?.BeginGiveUp();
					Announce("Messages.GaveUp", "UI.Victory", 240, AnnounceSound.GiveUp, VictoryColor);
					if (player != null && player.active && !player.dead)
						player.QuickSpawnItem(player.GetSource_FromThis(), ModContent.ItemType<TornRibbon>());
					if (!SurvivedOnryo) {
						SurvivedOnryo = true;
						if (ScreamNet.IsServer)
							NetMessage.SendData(MessageID.WorldData);
					}
					break;
				case HauntEndReason.PlayerDied:
					boss?.Vanish();
					Announce("Messages.Waiting", null, 0, AnnounceSound.None, MessageColor);
					break;
				default:
					boss?.Vanish();
					break;
			}
			TargetIndex = -1;
			SyncState();
		}

		/// <summary>Spawn her out of the player's sight, roughly minTiles to maxTiles away.</summary>
		private static void SpawnBoss(Player player, int minTiles, int maxTiles) {
			Vector2 pos;
			if (!Onryo.TryFindSpot(player, player.Center, minTiles, maxTiles, requireHidden: true, out pos)
				&& !Onryo.TryFindSpot(player, player.Center, minTiles / 2, maxTiles, requireHidden: false, out pos)) {
				// Nowhere sensible (e.g. a solid-block test world): drop her in to the side anyway.
				pos = player.Center + new Vector2(Main.rand.NextBool() ? 50 * 16 : -50 * 16, -64);
			}
			// pos is the top-left of her hitbox; NewNPC wants the bottom-center. NewNPC syncs to clients.
			int x = (int)pos.X + Onryo.HitboxWidth / 2;
			int y = (int)pos.Y + Onryo.HitboxHeight;
			bossIndex = NPC.NewNPC(player.GetSource_FromThis(), x, y, ModContent.NPCType<Onryo>());
			if (bossIndex >= Main.maxNPCs)
				bossIndex = -1;
		}

		public override void PostUpdateWorld() {
			// Runs on the server and in single-player only.
			if (!Active)
				return;

			Player player = Target;
			if (player == null || !player.active || player.dead) {
				End(HauntEndReason.PlayerDied); // died, or left the server
				return;
			}

			// Something (another mod, a butcher command) removed her: she comes back.
			if (Boss == null) {
				SpawnBoss(player, 45, 65);
				SyncState();
			}

			TicksRemaining--;

			HauntPhase phase = Phase;
			if (phase != lastPhase) {
				lastPhase = phase;
				OnPhaseChanged(phase, player);
			}

			if (TicksRemaining <= 0) {
				End(HauntEndReason.Survived);
				return;
			}

			if (++syncTimer >= 60) {
				syncTimer = 0;
				SyncState();
			}
		}

		private static void OnPhaseChanged(HauntPhase phase, Player player) {
			string key = phase == HauntPhase.Frenzy ? "Messages.Frenzy" : "Messages.StoppedPretending";
			Announce(key, key, 150, AnnounceSound.PhaseChange, MessageColor);
			Boss?.OnPhaseChanged(phase, player);
			SyncState();
		}

		// ---------------------------------------------------------------- networking
		/// <summary>Server: tell every client the event's state. No-op in single-player.</summary>
		public static void SyncState() {
			if (!ScreamNet.IsServer)
				return;
			ModPacket packet = ScreamNet.Create(ScreamPacket.EventState);
			WriteState(packet);
			packet.Send();
		}

		private static void WriteState(BinaryWriter writer) {
			writer.Write(Active);
			writer.Write((short)TargetIndex);
			writer.Write(TicksRemaining);
			writer.Write(TotalTicks);
			writer.Write((short)bossIndex);
			writer.Write(SurvivedOnryo);
		}

		public static void ReceiveState(BinaryReader reader) {
			Active = reader.ReadBoolean();
			TargetIndex = reader.ReadInt16();
			TicksRemaining = reader.ReadInt32();
			TotalTicks = reader.ReadInt32();
			bossIndex = reader.ReadInt16();
			SurvivedOnryo = reader.ReadBoolean();
		}

		/// <summary>
		/// A chat line plus an optional banner and sound, shown everywhere. Keys are relative to
		/// Mods.ScreamAndRun. Server: broadcast. Single-player: show locally.
		/// </summary>
		private static void Announce(string chatKey, string bannerKey, int bannerTicks, AnnounceSound sound, Color color) {
			if (ScreamNet.IsServer) {
				ModPacket packet = ScreamNet.Create(ScreamPacket.Announce);
				packet.Write(chatKey ?? "");
				packet.Write(bannerKey ?? "");
				packet.Write(bannerTicks);
				packet.Write((byte)sound);
				packet.Write(color.PackedValue);
				packet.Send();
			}
			else {
				ShowAnnounce(chatKey, bannerKey, bannerTicks, sound, color);
			}
		}

		public static void ReceiveAnnounce(BinaryReader reader) {
			string chatKey = reader.ReadString();
			string bannerKey = reader.ReadString();
			int bannerTicks = reader.ReadInt32();
			var sound = (AnnounceSound)reader.ReadByte();
			var color = new Color { PackedValue = reader.ReadUInt32() };
			ShowAnnounce(chatKey, bannerKey, bannerTicks, sound, color);
		}

		private static void ShowAnnounce(string chatKey, string bannerKey, int bannerTicks, AnnounceSound sound, Color color) {
			if (Main.dedServ)
				return;
			if (!string.IsNullOrEmpty(chatKey))
				Main.NewText(Language.GetTextValue("Mods.ScreamAndRun." + chatKey), color);
			if (!string.IsNullOrEmpty(bannerKey))
				HauntUISystem.ShowBanner(Language.GetTextValue("Mods.ScreamAndRun." + bannerKey), color, bannerTicks);
			switch (sound) {
				case AnnounceSound.Noticed:
					SoundEngine.PlaySound(ScreamAudio.Noticed);
					break;
				case AnnounceSound.PhaseChange:
					SoundEngine.PlaySound(ScreamAudio.PhaseChange);
					break;
				case AnnounceSound.GiveUp:
					SoundEngine.PlaySound(ScreamAudio.GiveUp);
					break;
			}
		}

		// World data: the survived flag is saved; the live event state is sent to players as they join.
		public override void NetSend(BinaryWriter writer) => WriteState(writer);

		public override void NetReceive(BinaryReader reader) => ReceiveState(reader);

		public override void SaveWorldData(TagCompound tag) {
			if (SurvivedOnryo)
				tag["survivedOnryo"] = true;
		}

		public override void LoadWorldData(TagCompound tag) {
			SurvivedOnryo = tag.ContainsKey("survivedOnryo");
		}

		// ---------------------------------------------------------------- every machine
		public override void PostUpdateEverything() {
			// Clients count down between the server's once-a-second syncs, so the timer is smooth.
			if (ScreamNet.IsClient && Active && TicksRemaining > 0)
				TicksRemaining--;

			Dim = MathHelper.Clamp(Dim + (Active ? 1f / 120f : -1f / 90f), 0f, 1f);

			// The overlay map is drawn in the world pass where no UI layer can cover it,
			// so for the hunted player it is swapped for the minimap, which is covered in blood.
			if (LocalIsTarget && Main.mapStyle == 2) {
				savedMapStyle = 2;
				Main.mapStyle = 1;
			}
			else if (!LocalIsTarget) {
				RestoreMapStyle();
			}
		}

		private static void RestoreMapStyle() {
			if (savedMapStyle >= 0) {
				Main.mapStyle = savedMapStyle;
				savedMapStyle = -1;
			}
		}

		public override void ModifySunLightColor(ref Color tileColor, ref Color backgroundColor) {
			if (Dim <= 0f)
				return;
			tileColor = Color.Lerp(tileColor, new Color(40, 22, 30), Dim * 0.8f);
			backgroundColor = Color.Lerp(backgroundColor, new Color(22, 6, 12), Dim * 0.85f);
		}

		public override void OnWorldLoad() => Reset();

		public override void OnWorldUnload() {
			Reset();
			SurvivedOnryo = false;
		}

		private static void Reset() {
			Active = false;
			TicksRemaining = 0;
			TotalTicks = 0;
			TargetIndex = -1;
			bossIndex = -1;
			Dim = 0f;
			lastPhase = HauntPhase.None;
			syncTimer = 0;
			RestoreMapStyle();
		}
	}
}
