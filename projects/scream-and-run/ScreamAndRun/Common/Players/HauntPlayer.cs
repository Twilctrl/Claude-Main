using System;
using System.IO;
using Microsoft.Xna.Framework;
using ScreamAndRun.Common.Audio;
using ScreamAndRun.Common.Configs;
using ScreamAndRun.Common.Net;
using ScreamAndRun.Common.Systems;
using ScreamAndRun.Common.UI;
using ScreamAndRun.Content.NPCs;
using ScreamAndRun.Content.Tiles;
using Terraria;
using Terraria.Audio;
using Terraria.DataStructures;
using Terraria.GameInput;
using Terraria.ID;
using Terraria.Localization;
using Terraria.ModLoader;

namespace ScreamAndRun.Common.Players
{
	/// <summary>
	/// The player's side of the event: noise, how visible they are, hiding in lockers,
	/// the heartbeat, and dying properly when she catches them.
	///
	/// Multiplayer: each client measures its own player's light, noise and hiding and sends them to
	/// the server (which has no lighting), and the server forwards them so everyone hides the player
	/// inside a locker. Proximity, her awareness and the locker cues are worked out locally from
	/// Onryo's synced state. Contact deaths are checked on the hunted player's own client.
	/// </summary>
	public class HauntPlayer : ModPlayer
	{
		private const float BaseDetectionTiles = 45f;
		private const int PresenceRangeTiles = 60;

		// ---- perception: measured on the owning client, synced to the server
		/// <summary>0 to 1. Rises when you run, attack or mine, and fades over a few seconds.</summary>
		public float Noise { get; private set; }
		/// <summary>How far away she can notice you, in pixels.</summary>
		public float DetectionRadius { get; private set; } = BaseDetectionTiles * 16f;
		public bool CrouchingStill { get; private set; }
		public bool InDarkness { get; private set; }

		// ---- worked out locally from Onryo (only for the player she's hunting)
		/// <summary>0 when she is 60+ tiles away, 1 when she's on top of you.</summary>
		public float Closeness;
		public bool SeenByHer;
		public bool Tracked;
		public bool KnowsLocker;
		/// <summary>While hiding: 0-1, how much of her shadow falls across the vents.</summary>
		public float LockerShadow;
		/// <summary>While hiding: -1 (she's to the left) to 1 (to the right).</summary>
		public float LockerShadowSide;

		// ---- server-side locker search (single-player: local)
		public int SearchPressure;

		/// <summary>Closeness, eased so effects don't pop.</summary>
		public float SmoothCloseness { get; private set; }

		// ---- hiding (synced)
		public bool Hiding { get; private set; }
		public Point HidePoint { get; private set; }
		public Vector2 HideCenter => new(HidePoint.X * 16 + 16, HidePoint.Y * 16 + 26);
		public bool SeenEntering { get; private set; }
		public int HideTicks;

		private int prevItemAnimation;
		private int heartbeatTimer;
		private int secondBeatTimer;
		private PlayerDeathReason pendingKillReason;
		private int pendingKillTicks;
		private int catchCooldown;

		private bool IsLocal => Player.whoAmI == Main.myPlayer && !Main.dedServ;
		private bool IsHunted => HauntEventSystem.Active && HauntEventSystem.TargetIndex == Player.whoAmI;

		public override void OnEnterWorld() {
			ExitHiding(playSound: false);
			Noise = 0f;
			HideTicks = 0;
			SearchPressure = 0;
			pendingKillTicks = 0;
		}

		// ---------------------------------------------------------------- noise
		public void AddNoise(float amount) {
			if (IsHunted)
				Noise = MathHelper.Clamp(Noise + amount, 0f, 1f);
		}

		/// <summary>Owning client only: lighting doesn't exist on a server.</summary>
		private void UpdateNoiseAndVisibility() {
			if (!IsHunted) {
				Noise = 0f;
				DetectionRadius = BaseDetectionTiles * 16f;
				CrouchingStill = InDarkness = false;
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
			tiles *= MathHelper.Lerp(0.6f, 1f, MathHelper.Clamp(brightness / 0.5f, 0f, 1f));
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
			// If she was watching you when you got in, she knows roughly where to look.
			SeenEntering = IsHunted && (HauntEventSystem.Boss?.CanSeePlayer ?? false);
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
		/// <summary>Owning client: she got you, from <paramref name="from"/>. Returns true if you died.</summary>
		public bool CaughtBy(Vector2 from, bool inLocker) {
			if (Player.dead)
				return true;
			int dir = from.X < Player.Center.X ? 1 : -1;

			string key = inLocker ? "Locker" : Main.rand.NextBool() ? "Caught1" : "Caught2";
			PlayerDeathReason reason = PlayerDeathReason.ByCustomReason(NetworkText.FromKey("Mods.ScreamAndRun.Death." + key, Player.name));

			if (inLocker || ScreamConfig.Instance.OneShot) {
				HauntUISystem.TriggerJumpscare();
				SoundEngine.PlaySound(ScreamAudio.Jumpscare);
				// KillMe skips defense, armor, i-frames and dodges, and syncs the death in multiplayer.
				// If something still revives the player, we try again for a few ticks.
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

		/// <summary>Owning client: the server says she opened your locker.</summary>
		public void OnLockerOpened() {
			SoundEngine.PlaySound(ScreamAudio.LockerOpen, Player.Center);
			ExitHiding(playSound: false);
			// Doesn't need her NPC here: the packet can arrive before her sync does.
			CaughtBy(HauntEventSystem.Boss?.NPC.Center ?? Player.Center, inLocker: true);
		}

		/// <summary>Touch check, on the hunted player's own client (like vanilla contact damage).</summary>
		private void CheckContact(Onryo boss) {
			if (catchCooldown > 0) {
				catchCooldown--;
				return;
			}
			if (Hiding || Player.dead || boss.GivingUp)
				return;
			// Her hitbox is a little smaller than the sprite, to be fair.
			Rectangle hitbox = boss.NPC.Hitbox;
			hitbox.Inflate(-4, -4);
			if (!hitbox.Intersects(Player.Hitbox))
				return;

			if (!CaughtBy(boss.NPC.Center, inLocker: false)) {
				// One-shot is off and you survived: she backs off instead of chain-hitting you.
				catchCooldown = 60;
				if (ScreamNet.IsClient)
					ScreamNet.Create(ScreamPacket.BackOff).Send();
				else
					boss.BackOff();
			}
		}

		public override void Kill(double damage, int hitDirection, bool pvp, PlayerDeathReason damageSource) {
			pendingKillTicks = 0;
			ExitHiding(playSound: false);
			// The server also notices the death on its own; in single-player end straight away.
			if (ScreamNet.Authoritative && IsHunted)
				HauntEventSystem.End(HauntEndReason.PlayerDied);
		}

		// ---------------------------------------------------------------- per tick
		public override void PostUpdate() {
			if (pendingKillTicks > 0) {
				pendingKillTicks--;
				if (!Player.dead && pendingKillReason != null && IsLocal)
					Player.KillMe(pendingKillReason, 9999.0, 0);
			}

			if (Hiding) {
				if (IsLocal && (Player.dead || !LockerStillThere()))
					ExitHiding(playSound: false);
				else if (IsHunted)
					HideTicks++;
			}
			else if (HideTicks > 0) {
				HideTicks = Math.Max(0, HideTicks - 2); // hopping in and out doesn't reset the clock
			}

			if (!IsLocal)
				return; // remote players: their own client measures and syncs these

			UpdateNoiseAndVisibility();
			UpdateFromBoss();
			SmoothCloseness = MathHelper.Lerp(SmoothCloseness, Closeness, 0.08f);
			UpdateHeartbeat();
		}

		/// <summary>Local player: what she's doing, as far as you can tell.</summary>
		private void UpdateFromBoss() {
			Onryo boss = HauntEventSystem.Boss;
			if (!IsHunted || boss == null) {
				Closeness = 0f;
				SeenByHer = Tracked = KnowsLocker = false;
				LockerShadow = 0f;
				if (!HauntEventSystem.Active)
					SearchPressure = 0;
				return;
			}

			float dist = Vector2.Distance(boss.NPC.Center, Player.Center);
			Closeness = MathHelper.Clamp(1f - dist / (PresenceRangeTiles * 16f), 0f, 1f);
			SeenByHer = boss.CanSeePlayer;
			Tracked = boss.HasTrack && HauntEventSystem.Phase != HauntPhase.Stalking;
			KnowsLocker = Hiding && boss.KnowsLocker;

			if (Hiding) {
				// Her shadow over the vents: strongest when she's level with the locker and close.
				Vector2 offset = boss.NPC.Center - HideCenter;
				bool level = Math.Abs(offset.Y) < 4 * 16;
				LockerShadow = level ? MathHelper.Clamp(1f - (Math.Abs(offset.X) - 16) / (6 * 16f), 0f, 1f) : 0f;
				LockerShadowSide = MathHelper.Clamp(offset.X / (6 * 16f), -1f, 1f);
			}
			else {
				LockerShadow = 0f;
				CheckContact(boss);
			}
		}

		private void UpdateHeartbeat() {
			if (!IsHunted || Player.dead)
				return;

			float c = SmoothCloseness;
			if (Hiding)
				c = Math.Max(c, 0.35f);
			if (KnowsLocker)
				c = 1f;
			if (c < 0.03f)
				return;
			// Quieter inside the locker, so her footsteps and knocks carry over it.
			float hidingDuck = Hiding ? 0.55f : 1f;

			if (secondBeatTimer > 0 && --secondBeatTimer == 0)
				SoundEngine.PlaySound(ScreamAudio.Heartbeat with { Volume = (0.25f + 0.55f * c) * hidingDuck, Pitch = ScreamAudio.Heartbeat.Pitch + 0.15f });

			if (--heartbeatTimer <= 0) {
				heartbeatTimer = (int)MathHelper.Lerp(75f, 17f, c);
				secondBeatTimer = Math.Max(4, heartbeatTimer / 4);
				SoundEngine.PlaySound(ScreamAudio.Heartbeat with { Volume = (0.3f + 0.7f * c) * hidingDuck });
			}
		}

		// ---------------------------------------------------------------- multiplayer sync
		// Noise and radius are quantized; they're only sent when they change enough to matter.
		private bool sentHiding;
		private Point sentHidePoint;
		private byte sentNoise;
		private byte sentRadius;

		private byte NoiseByte => (byte)(Noise * 255f);
		private byte RadiusByte => (byte)Math.Min(255f, DetectionRadius / 16f);

		public override void SyncPlayer(int toWho, int fromWho, bool newPlayer) => SendState(toWho, fromWho);

		public override void CopyClientState(ModPlayer targetCopy) {
			var copy = (HauntPlayer)targetCopy;
			copy.sentHiding = Hiding;
			copy.sentHidePoint = HidePoint;
			copy.sentNoise = NoiseByte;
			copy.sentRadius = RadiusByte;
		}

		public override void SendClientChanges(ModPlayer clientPlayer) {
			var last = (HauntPlayer)clientPlayer;
			bool changed = last.sentHiding != Hiding
				|| last.sentHidePoint != HidePoint
				|| Math.Abs(last.sentNoise - NoiseByte) >= 10
				|| (last.sentNoise >= 128) != (NoiseByte >= 128) // crossing the "she can hear you" line
				|| Math.Abs(last.sentRadius - RadiusByte) >= 2;
			if (changed)
				SendState(-1, Main.myPlayer);
		}

		public void SendState(int toWho, int ignoreWho) {
			if (Main.netMode == NetmodeID.SinglePlayer)
				return;
			ModPacket packet = ScreamNet.Create(ScreamPacket.PlayerState);
			packet.Write((byte)Player.whoAmI);
			packet.Write(Hiding);
			packet.Write((short)HidePoint.X);
			packet.Write((short)HidePoint.Y);
			packet.Write(SeenEntering);
			packet.Write(NoiseByte);
			packet.Write(RadiusByte);
			packet.Send(toWho, ignoreWho);
		}

		public void ReadState(BinaryReader reader) {
			Hiding = reader.ReadBoolean();
			HidePoint = new Point(reader.ReadInt16(), reader.ReadInt16());
			SeenEntering = reader.ReadBoolean();
			Noise = reader.ReadByte() / 255f;
			DetectionRadius = reader.ReadByte() * 16f;
		}
	}
}
