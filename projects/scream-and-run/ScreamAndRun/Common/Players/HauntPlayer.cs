using System;
using Microsoft.Xna.Framework;
using ScreamAndRun.Common.Audio;
using ScreamAndRun.Common.Configs;
using ScreamAndRun.Common.Systems;
using ScreamAndRun.Common.UI;
using ScreamAndRun.Content.Tiles;
using Terraria;
using Terraria.Audio;
using Terraria.DataStructures;
using Terraria.GameInput;
using Terraria.Localization;
using Terraria.ModLoader;

namespace ScreamAndRun.Common.Players
{
	/// <summary>
	/// The player's side of the event: noise, how visible they are, hiding in lockers,
	/// the heartbeat, and dying properly when she catches them.
	/// </summary>
	public class HauntPlayer : ModPlayer
	{
		private const float BaseDetectionTiles = 30f;

		// ---- perception (read by Tsubaki and the UI)
		/// <summary>0 to 1. Rises when you run, attack or mine, and fades over a few seconds.</summary>
		public float Noise { get; private set; }
		/// <summary>How far away she can notice you, in pixels.</summary>
		public float DetectionRadius { get; private set; } = BaseDetectionTiles * 16f;
		public bool CrouchingStill { get; private set; }
		public bool InDarkness { get; private set; }

		// ---- written by Tsubaki each tick
		/// <summary>0 when she is 60+ tiles away, 1 when she's on top of you.</summary>
		public float Closeness;
		public bool SeenByHer;
		public bool Tracked;
		public bool KnowsLocker;
		public int SearchPressure;

		/// <summary>Closeness, eased so effects don't pop.</summary>
		public float SmoothCloseness { get; private set; }

		// ---- hiding
		public bool Hiding { get; private set; }
		public Point HidePoint { get; private set; }
		public Vector2 HideCenter { get; private set; }
		public bool SeenEntering { get; private set; }
		public int HideTicks;

		private int prevItemAnimation;
		private int heartbeatTimer;
		private int secondBeatTimer;
		private PlayerDeathReason pendingKillReason;
		private int pendingKillTicks;

		public override void OnEnterWorld() {
			ExitHiding(playSound: false);
			Noise = 0f;
			HideTicks = 0;
			SearchPressure = 0;
			pendingKillTicks = 0;
		}

		// ---------------------------------------------------------------- noise
		public void AddNoise(float amount) {
			if (HauntEventSystem.Active)
				Noise = MathHelper.Clamp(Noise + amount, 0f, 1f);
		}

		private void UpdateNoiseAndVisibility() {
			if (!HauntEventSystem.Active) {
				Noise = 0f;
				DetectionRadius = BaseDetectionTiles * 16f;
				return;
			}

			// A new swing or shot started this tick.
			bool startedUse = Player.itemAnimation > prevItemAnimation;
			prevItemAnimation = Player.itemAnimation;
			Item held = Player.HeldItem;
			if (startedUse && (held.damage > 0 || held.pick > 0 || held.axe > 0 || held.hammer > 0))
				AddNoise(0.07f);

			if (Math.Abs(Player.velocity.X) > 3.5f && Player.velocity.Y == 0f)
				AddNoise(0.008f); // running
			else if (Player.velocity.Y < -3f)
				AddNoise(0.002f); // jumping / flying

			Noise = Math.Max(0f, Noise - 0.004f);

			CrouchingStill = !Hiding && Player.controlDown && Math.Abs(Player.velocity.X) < 0.2f && Player.velocity.Y == 0f;
			float brightness = Lighting.Brightness((int)(Player.Center.X / 16f), (int)(Player.Center.Y / 16f));
			InDarkness = brightness < 0.25f;

			float tiles = BaseDetectionTiles;
			if (CrouchingStill)
				tiles *= 0.5f;
			tiles *= MathHelper.Lerp(0.45f, 1f, MathHelper.Clamp(brightness / 0.5f, 0f, 1f));
			tiles *= 1f + Noise * 1.5f;
			DetectionRadius = Math.Max(tiles, 5f) * 16f;
		}

		// ---------------------------------------------------------------- hiding
		public void ToggleHiding(Point lockerTopLeft) {
			if (Hiding)
				ExitHiding(playSound: true);
			else
				EnterHiding(lockerTopLeft);
		}

		public void EnterHiding(Point lockerTopLeft) {
			if (Player.dead)
				return;
			Player.mount.Dismount(Player);
			Player.RemoveAllGrapplingHooks();
			Hiding = true;
			HidePoint = lockerTopLeft;
			HideCenter = new Vector2(lockerTopLeft.X * 16 + 16, lockerTopLeft.Y * 16 + 26);
			// If she was watching you when you got in, she knows roughly where to look.
			SeenEntering = HauntEventSystem.Boss?.CanSeePlayer ?? false;
			SoundEngine.PlaySound(ScreamAudio.LockerClose, HideCenter);
		}

		public void ExitHiding(bool playSound) {
			if (!Hiding)
				return;
			Hiding = false;
			SeenEntering = false;
			if (playSound)
				SoundEngine.PlaySound(ScreamAudio.LockerOpen, HideCenter);
		}

		private bool LockerStillThere() {
			Tile t = Framing.GetTileSafely(HidePoint.X, HidePoint.Y);
			return t.HasTile && t.TileType == ModContent.TileType<HidingLocker>();
		}

		public override void ProcessTriggers(TriggersSet triggersSet) {
			if (Hiding && PlayerInput.Triggers.JustPressed.Jump)
				ExitHiding(playSound: true);
		}

		public override void SetControls() {
			if (!Hiding)
				return;
			// controlUseTile stays on so right-clicking the locker lets you out.
			Player.controlLeft = false;
			Player.controlRight = false;
			Player.controlUp = false;
			Player.controlDown = false;
			Player.controlJump = false;
			Player.controlUseItem = false;
			Player.controlHook = false;
			Player.controlMount = false;
			Player.controlThrow = false;
		}

		public override bool CanUseItem(Item item) => !Hiding;

		public override void PreUpdateMovement() {
			if (!Hiding)
				return;
			Player.velocity = Vector2.Zero;
			Player.position = HideCenter - Player.Size / 2f;
			Player.fallStart = (int)(Player.position.Y / 16f);
		}

		public override void HideDrawLayers(PlayerDrawSet drawInfo) {
			if (!Hiding)
				return;
			foreach (PlayerDrawLayer layer in PlayerDrawLayerLoader.Layers)
				layer.Hide();
		}

		// ---------------------------------------------------------------- being caught
		/// <summary>Called by Tsubaki on contact. Returns true if the player was killed.</summary>
		public bool CaughtBy(NPC npc, bool inLocker) {
			if (Player.dead)
				return true;
			int dir = npc.Center.X < Player.Center.X ? 1 : -1;

			string key = inLocker ? "Locker" : Main.rand.NextBool() ? "Caught1" : "Caught2";
			string text = Language.GetTextValue("Mods.ScreamAndRun.Death." + key, Player.name);
			PlayerDeathReason reason = PlayerDeathReason.ByCustomReason(text);

			if (inLocker || ScreamConfig.Instance.OneShot) {
				HauntUISystem.TriggerJumpscare();
				SoundEngine.PlaySound(ScreamAudio.Jumpscare);
				// KillMe skips defense, armor, i-frames and dodges. If something still revives
				// the player, we try again for a few ticks.
				pendingKillReason = reason;
				pendingKillTicks = 15;
				Player.KillMe(reason, 9999.0, dir);
				return true;
			}

			// One-shot off: a heavy but survivable hit that respects defense and i-frames.
			int damage = (int)(Player.statLifeMax2 * 0.4f) + 20;
			Player.Hurt(reason, damage, dir);
			return Player.dead;
		}

		public override void Kill(double damage, int hitDirection, bool pvp, PlayerDeathReason damageSource) {
			pendingKillTicks = 0;
			ExitHiding(playSound: false);
			HauntEventSystem.End(HauntEndReason.PlayerDied);
		}

		// ---------------------------------------------------------------- per tick
		public override void PostUpdate() {
			if (pendingKillTicks > 0) {
				pendingKillTicks--;
				if (!Player.dead && pendingKillReason != null)
					Player.KillMe(pendingKillReason, 9999.0, 0);
			}

			if (Hiding) {
				if (Player.dead || !LockerStillThere())
					ExitHiding(playSound: false);
				else if (HauntEventSystem.Active)
					HideTicks++;
			}
			else if (HideTicks > 0) {
				HideTicks = Math.Max(0, HideTicks - 2); // hopping in and out doesn't reset the clock
			}

			UpdateNoiseAndVisibility();

			if (!HauntEventSystem.Active) {
				Closeness = 0f;
				SeenByHer = false;
				Tracked = false;
				KnowsLocker = false;
				SearchPressure = 0;
			}
			SmoothCloseness = MathHelper.Lerp(SmoothCloseness, Closeness, 0.08f);

			if (Player.whoAmI == Main.myPlayer)
				UpdateHeartbeat();
		}

		private void UpdateHeartbeat() {
			if (Main.dedServ || !HauntEventSystem.Active || Player.dead)
				return;

			float c = SmoothCloseness;
			if (Hiding)
				c = Math.Max(c, 0.35f);
			if (KnowsLocker)
				c = 1f;
			if (c < 0.03f)
				return;

			if (secondBeatTimer > 0 && --secondBeatTimer == 0)
				SoundEngine.PlaySound(ScreamAudio.Heartbeat with { Volume = 0.25f + 0.55f * c, Pitch = ScreamAudio.Heartbeat.Pitch + 0.15f });

			if (--heartbeatTimer <= 0) {
				heartbeatTimer = (int)MathHelper.Lerp(75f, 17f, c);
				secondBeatTimer = Math.Max(4, heartbeatTimer / 4);
				SoundEngine.PlaySound(ScreamAudio.Heartbeat with { Volume = 0.3f + 0.7f * c });
			}
		}
	}
}
