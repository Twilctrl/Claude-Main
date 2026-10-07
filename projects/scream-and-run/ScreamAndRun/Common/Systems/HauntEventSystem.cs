using System;
using Microsoft.Xna.Framework;
using ScreamAndRun.Common.Audio;
using ScreamAndRun.Common.Configs;
using ScreamAndRun.Common.UI;
using ScreamAndRun.Content.Items;
using ScreamAndRun.Content.NPCs;
using Terraria;
using Terraria.Audio;
using Terraria.ID;
using Terraria.Localization;
using Terraria.ModLoader;

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
	/// Owns the event: the countdown, the phases, spawning Tsubaki and ending everything.
	/// Single-player only (the Love Letter refuses to work in multiplayer).
	/// </summary>
	public class HauntEventSystem : ModSystem
	{
		public static readonly Color MessageColor = new(220, 30, 50);

		public static bool Active { get; private set; }
		public static int TicksRemaining { get; private set; }
		public static int TotalTicks { get; private set; }

		/// <summary>0 to 1: how dark the sky is. Fades in and out rather than snapping.</summary>
		public static float Dim { get; private set; }

		private static int bossIndex = -1;
		/// <summary>The player's map style before we swapped "overlay" for the (blood-covered) minimap.</summary>
		private static int savedMapStyle = -1;
		private static HauntPhase lastPhase;

		public static int StalkTicks => Math.Min(TotalTicks / 4, 90 * 60);
		public static int FrenzyTicks => Math.Min(60 * 60, TotalTicks / 3);
		public static int ElapsedTicks => TotalTicks - TicksRemaining;

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

		public static Tsubaki Boss {
			get {
				if (bossIndex < 0 || bossIndex >= Main.maxNPCs)
					return null;
				NPC npc = Main.npc[bossIndex];
				return npc.active && npc.ModNPC is Tsubaki t ? t : null;
			}
		}

		public static bool CanStart(Player player) =>
			!Active
			&& Main.netMode == NetmodeID.SinglePlayer
			&& !player.dead
			&& !NPC.AnyNPCs(ModContent.NPCType<Tsubaki>());

		public static void Start(Player player) {
			if (!CanStart(player))
				return;

			TotalTicks = Math.Max(30, ScreamConfig.Instance.SurvivalSeconds) * 60;
			TicksRemaining = TotalTicks;
			Active = true;
			lastPhase = HauntPhase.Stalking;

			SpawnBoss(player, 40, 60);

			string text = Language.GetTextValue("Mods.ScreamAndRun.Messages.Noticed");
			Main.NewText(text, MessageColor);
			HauntUISystem.ShowBanner(text, MessageColor, 210);
			SoundEngine.PlaySound(ScreamAudio.Noticed);
		}

		public static void End(HauntEndReason reason) {
			if (!Active)
				return;
			Active = false;
			Player player = Main.LocalPlayer;
			Tsubaki boss = Boss;
			bossIndex = -1;

			switch (reason) {
				case HauntEndReason.Survived: {
					boss?.BeginGiveUp();
					string text = Language.GetTextValue("Mods.ScreamAndRun.Messages.GaveUp");
					Main.NewText(text, MessageColor);
					HauntUISystem.ShowBanner(Language.GetTextValue("Mods.ScreamAndRun.UI.Victory"), new Color(255, 200, 210), 240);
					SoundEngine.PlaySound(ScreamAudio.GiveUp);
					if (player.active && !player.dead)
						player.QuickSpawnItem(player.GetSource_FromThis(), ModContent.ItemType<TornRibbon>());
					break;
				}
				case HauntEndReason.PlayerDied:
					boss?.Vanish();
					Main.NewText(Language.GetTextValue("Mods.ScreamAndRun.Messages.Waiting"), MessageColor);
					break;
				default:
					boss?.Vanish();
					break;
			}
		}

		/// <summary>Spawn her out of the player's sight, roughly minTiles to maxTiles away.</summary>
		private static void SpawnBoss(Player player, int minTiles, int maxTiles) {
			Vector2 pos;
			if (!Tsubaki.TryFindSpot(player, player.Center, minTiles, maxTiles, requireHidden: true, out pos)
				&& !Tsubaki.TryFindSpot(player, player.Center, minTiles / 2, maxTiles, requireHidden: false, out pos)) {
				// Nowhere sensible (e.g. a solid-block test world): drop her in to the side anyway.
				pos = player.Center + new Vector2(Main.rand.NextBool() ? 50 * 16 : -50 * 16, -64);
			}
			// pos is the top-left of her hitbox; NewNPC wants the bottom-center.
			int x = (int)pos.X + Tsubaki.HitboxWidth / 2;
			int y = (int)pos.Y + Tsubaki.HitboxHeight;
			bossIndex = NPC.NewNPC(player.GetSource_FromThis(), x, y, ModContent.NPCType<Tsubaki>());
			if (bossIndex >= Main.maxNPCs)
				bossIndex = -1;
		}

		public override void PostUpdateWorld() {
			if (!Active)
				return;

			Player player = Main.LocalPlayer;
			if (!player.active || player.dead) {
				End(HauntEndReason.PlayerDied);
				return;
			}

			// Something (another mod, a butcher command) removed her: she comes back.
			if (Boss == null)
				SpawnBoss(player, 45, 65);

			TicksRemaining--;

			HauntPhase phase = Phase;
			if (phase != lastPhase) {
				lastPhase = phase;
				OnPhaseChanged(phase, player);
			}

			if (TicksRemaining <= 0)
				End(HauntEndReason.Survived);
		}

		private static void OnPhaseChanged(HauntPhase phase, Player player) {
			string key = phase == HauntPhase.Frenzy ? "Frenzy" : "StoppedPretending";
			string text = Language.GetTextValue("Mods.ScreamAndRun.Messages." + key);
			Main.NewText(text, MessageColor);
			HauntUISystem.ShowBanner(text, MessageColor, 150);
			SoundEngine.PlaySound(ScreamAudio.PhaseChange);
			Boss?.OnPhaseChanged(phase, player);
		}

		public override void PostUpdateEverything() {
			Dim = MathHelper.Clamp(Dim + (Active ? 1f / 120f : -1f / 90f), 0f, 1f);

			// The overlay map is drawn in the world pass where no UI layer can cover it,
			// so during the event it is swapped for the minimap, which is covered in blood.
			if (Active && Main.mapStyle == 2) {
				savedMapStyle = 2;
				Main.mapStyle = 1;
			}
			else if (!Active) {
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

		public override void OnWorldUnload() => Reset();

		private static void Reset() {
			Active = false;
			TicksRemaining = 0;
			TotalTicks = 0;
			bossIndex = -1;
			Dim = 0f;
			lastPhase = HauntPhase.None;
			RestoreMapStyle();
		}
	}
}
