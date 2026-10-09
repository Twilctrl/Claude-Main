using System;
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using ScreamAndRun.Common.AI;
using ScreamAndRun.Common.Audio;
using ScreamAndRun.Common.Configs;
using ScreamAndRun.Common.Players;
using ScreamAndRun.Common.Systems;
using ScreamAndRun.Common.UI;
using Terraria;
using Terraria.Audio;
using Terraria.ID;
using Terraria.ModLoader;

namespace ScreamAndRun.Content.NPCs
{
	/// <summary>
	/// Onryo, the transfer student. She cannot be hurt, can't be knocked back, ignores
	/// debuffs and never despawns on her own. HauntEventSystem spawns and removes her.
	/// </summary>
	public class Onryo : ModNPC
	{
		public const int HitboxWidth = 28;
		public const int HitboxHeight = 44;
		// Offset of her hitbox inside the 2x3-tile pathfinding body.
		private static readonly Vector2 NodeOffset = new((HuntPathfinder.W * 16 - HitboxWidth) / 2f, HuntPathfinder.H * 16 - HitboxHeight);

		private const float Tile = 16f;
		private const int PresenceRangeTiles = 60;

		// Base speeds in pixels per tick, before the config multiplier.
		private const float StalkSpeed = 1.1f;
		private const float RoamSpeed = 1.7f;
		private const float SearchSpeed = 2.1f;
		private const float HuntSpeed = 3.4f;
		private const float FrenzySpeed = 4.4f;

		public const int LockerSearchLimitSeen = 6 * 60;
		public const int LockerSearchLimitUnseen = 12 * 60;
		public const int MaxHideTicks = 40 * 60;

		public override string Texture => "ScreamAndRun/Assets/Textures/NPCs/Onryo";

		/// <summary>True while she has direct sight of the player (and isn't pretending).</summary>
		public bool CanSeePlayer { get; private set; }
		/// <summary>True while she is following the player's trail.</summary>
		public bool HasTrack { get; private set; }
		public bool Telegraphing => telegraphTicks > 0;
		public bool KnowsLocker { get; private set; }

		private Vector2 lastKnown;
		private int lostSightTicks;
		private int searchTicks;
		private Vector2 wanderPoint;
		private int wanderTimer;
		private int pauseTicks;
		private int wanderAge;
		private Vector2? currentGoal;

		private int footstepTimer;
		private int knockTimer = 120;
		private int knocksLeft;
		private int knockGap;

		private int hopTicks;

		private readonly List<Point> path = new();
		private int pathIndex;
		private int repathTimer;
		private Vector2 pathGoal;

		private Vector2 stuckAnchor;
		private int stuckTimer;
		private int stuckStrikes;
		private int farTicks;

		private int telegraphTicks;
		private int teleportCooldown = 8 * 60;
		private bool givingUp;

		public override void SetStaticDefaults() {
			Main.npcFrameCount[Type] = 6;
		}

		public override void SetDefaults() {
			NPC.width = HitboxWidth;
			NPC.height = HitboxHeight;
			NPC.aiStyle = -1;
			NPC.lifeMax = 9999;
			NPC.damage = 0; // contact is handled in AI so it can bypass defense and i-frames
			NPC.defense = 0;
			NPC.knockBackResist = 0f;
			NPC.dontTakeDamage = true;
			NPC.dontCountMe = true;
			NPC.chaseable = false;
			NPC.noGravity = true;
			NPC.noTileCollide = false;
			NPC.lavaImmune = true;
			NPC.friendly = false;
			NPC.npcSlots = 0f;
			NPC.value = 0f;
			NPC.HitSound = null;
			NPC.DeathSound = null;
			NPC.netAlways = true;
			for (int i = 0; i < NPC.buffImmune.Length; i++)
				NPC.buffImmune[i] = true;
		}

		// ---------------------------------------------------------------- invulnerability
		public override bool CheckActive() => false;
		public override bool CheckDead() {
			NPC.life = NPC.lifeMax;
			return false;
		}
		public override bool? CanBeHitByItem(Player player, Item item) => false;
		public override bool? CanBeHitByProjectile(Projectile projectile) => false;
		public override bool CanBeHitByNPC(NPC attacker) => false;
		public override bool CanHitPlayer(Player target, ref int cooldownSlot) => false;
		public override bool? CanFallThroughPlatforms() => true;

		private void ClearBuffs() {
			for (int i = 0; i < NPC.maxBuffs; i++) {
				NPC.buffType[i] = 0;
				NPC.buffTime[i] = 0;
			}
			NPC.life = NPC.lifeMax;
		}

		// ---------------------------------------------------------------- lifecycle hooks
		public void BeginGiveUp() {
			givingUp = true;
			telegraphTicks = 0;
		}

		/// <summary>Removes her immediately, with a puff.</summary>
		public void Vanish() {
			Puff(30);
			NPC.active = false;
		}

		public void OnPhaseChanged(HauntPhase phase, Player player) {
			if (phase == HauntPhase.Hunting || phase == HauntPhase.Frenzy) {
				// She always knew where you were.
				HasTrack = !player.GetModPlayer<HauntPlayer>().Hiding;
				lastKnown = player.Center;
				lostSightTicks = 0;
				repathTimer = 0;
			}
			if (phase == HauntPhase.Frenzy)
				teleportCooldown = 4 * 60;
		}

		// ---------------------------------------------------------------- AI
		public override void AI() {
			ClearBuffs();
			NPC.timeLeft = NPC.activeTime;

			if (givingUp) {
				NPC.velocity *= 0.85f;
				NPC.alpha = Math.Min(255, NPC.alpha + 4);
				if (Main.rand.NextBool(3))
					Dust.NewDust(NPC.position, NPC.width, NPC.height, DustID.Shadowflame, 0f, -1f);
				if (NPC.alpha >= 255)
					NPC.active = false;
				return;
			}

			if (!HauntEventSystem.Active) {
				NPC.active = false;
				return;
			}

			NPC.target = Main.myPlayer;
			Player player = Main.player[Main.myPlayer];
			if (!player.active || player.dead) {
				NPC.velocity = Vector2.Zero;
				return;
			}

			HauntPlayer hp = player.GetModPlayer<HauntPlayer>();
			HauntPhase phase = HauntEventSystem.Phase;
			ScreamConfig config = ScreamConfig.Instance;
			float dist = Vector2.Distance(NPC.Center, player.Center);
			hp.Closeness = MathHelper.Clamp(1f - dist / (PresenceRangeTiles * Tile), 0f, 1f);

			UpdatePerception(player, hp, phase, dist);

			if (UpdateLocker(player, hp))
				return; // she opened the locker

			// Contact. Her hitbox is a little smaller than the sprite, to be fair.
			Rectangle hitbox = NPC.Hitbox;
			hitbox.Inflate(-4, -4);
			if (!hp.Hiding && hitbox.Intersects(player.Hitbox)) {
				Catch(player, hp);
				return;
			}

			if (UpdateTelegraph(player, hp, phase, dist))
				return; // standing still, about to teleport

			if (UpdateFarAndStuck(player, hp, dist))
				return; // relocated this tick

			Vector2? goal = ChooseGoal(player, hp, phase, dist, out float speed);
			currentGoal = goal;
			MoveToward(goal, speed * config.SpeedMultiplier, player);
			UpdateLockerCues(hp);
			NPC.alpha = 0;
		}

		private void UpdatePerception(Player player, HauntPlayer hp, HauntPhase phase, float dist) {
			bool los = !hp.Hiding && Collision.CanHitLine(NPC.position, NPC.width, NPC.height, player.position, player.width, player.height);
			float sightRange = hp.DetectionRadius * (HasTrack ? 1.5f : 1f);
			bool sees = los && dist < sightRange;
			bool hears = !hp.Hiding && hp.Noise >= 0.5f && dist < hp.DetectionRadius;

			CanSeePlayer = sees && phase != HauntPhase.Stalking;

			if (sees || hears) {
				if (!HasTrack && phase != HauntPhase.Stalking) {
					SoundEngine.PlaySound(ScreamAudio.Spotted, NPC.Center);
					searchTicks = 0;
				}
				HasTrack = true;
				lastKnown = player.Center;
				lostSightTicks = 0;
			}
			else if (HasTrack) {
				int loseTrackTicks = phase == HauntPhase.Frenzy ? 6 * 60 : 4 * 60;
				if (++lostSightTicks > loseTrackTicks) {
					HasTrack = false;
					searchTicks = 8 * 60;
					wanderTimer = 0;
				}
			}

			hp.SeenByHer = CanSeePlayer;
			hp.Tracked = HasTrack && phase != HauntPhase.Stalking;
		}

		/// <summary>Locker logic. Returns true if she opened the locker this tick.</summary>
		private bool UpdateLocker(Player player, HauntPlayer hp) {
			if (!hp.Hiding) {
				KnowsLocker = false;
				hp.KnowsLocker = false;
				if (hp.SearchPressure > 0)
					hp.SearchPressure--;
				return false;
			}

			float lockerDist = Vector2.Distance(NPC.Center, hp.HideCenter);
			if (lockerDist < 7 * Tile)
				hp.SearchPressure++;
			else if (hp.SearchPressure > 0 && !KnowsLocker)
				hp.SearchPressure--;

			int limit = hp.SeenEntering ? LockerSearchLimitSeen : LockerSearchLimitUnseen;
			if (!KnowsLocker && (hp.SearchPressure >= limit || hp.HideTicks >= MaxHideTicks)) {
				KnowsLocker = true;
				repathTimer = 0;
			}
			hp.KnowsLocker = KnowsLocker;

			if (KnowsLocker && lockerDist < 2.5f * Tile) {
				SoundEngine.PlaySound(ScreamAudio.LockerOpen, hp.HideCenter);
				hp.ExitHiding(playSound: false);
				hp.CaughtBy(NPC, inLocker: true);
				return true;
			}
			return false;
		}

		/// <summary>
		/// What you notice from inside a locker: her footsteps nearby (positioned, so you can tell
		/// which side), her shadow across the vents, and knocking when she stops right outside.
		/// </summary>
		private void UpdateLockerCues(HauntPlayer hp) {
			if (!hp.Hiding) {
				hp.LockerShadow = 0f;
				return;
			}
			Vector2 offset = NPC.Center - hp.HideCenter;
			float lockerDist = offset.Length();

			// Shadow over the vents: strongest when she's level with the locker and close.
			bool level = Math.Abs(offset.Y) < 4 * Tile;
			hp.LockerShadow = level ? MathHelper.Clamp(1f - (Math.Abs(offset.X) - Tile) / (6 * Tile), 0f, 1f) : 0f;
			hp.LockerShadowSide = MathHelper.Clamp(offset.X / (6 * Tile), -1f, 1f);

			float moving = NPC.velocity.Length();
			if (lockerDist < 16 * Tile && moving > 0.4f) {
				if (--footstepTimer <= 0) {
					footstepTimer = (int)MathHelper.Clamp(40f / moving, 9f, 30f);
					float volume = MathHelper.Lerp(1f, 0.25f, lockerDist / (16 * Tile));
					SoundEngine.PlaySound(ScreamAudio.Footstep with { Volume = volume, Pitch = ScreamAudio.Footstep.Pitch + Main.rand.NextFloat(-0.1f, 0.1f) }, NPC.Center);
				}
			}

			// Standing right outside: a burst of knocks, then a rattle of the door, each one felt.
			if (knocksLeft > 0) {
				if (--knockGap <= 0) {
					knocksLeft--;
					if (knocksLeft == 0) {
						SoundEngine.PlaySound(ScreamAudio.Rattle, hp.HideCenter);
						HauntUISystem.Shake(22);
					}
					else {
						SoundEngine.PlaySound(ScreamAudio.Knock, hp.HideCenter);
						if (ScreamAudio.KnockLayer is SoundStyle layer)
							SoundEngine.PlaySound(layer, hp.HideCenter);
						HauntUISystem.Shake(16);
						knockGap = Main.rand.Next(9, 15);
					}
				}
			}
			else if (lockerDist < 3 * Tile && moving < 0.5f) {
				if (--knockTimer <= 0) {
					knockTimer = Main.rand.Next(170, 280);
					knocksLeft = Main.rand.Next(3, 6) + 1; // 3-5 knocks, then the rattle
					knockGap = 0;
				}
			}
			else {
				knockTimer = Math.Max(knockTimer, 60);
			}
		}

		/// <summary>Frenzy teleport: a loud cue, a short wind-up, then she appears behind you.</summary>
		private bool UpdateTelegraph(Player player, HauntPlayer hp, HauntPhase phase, float dist) {
			if (telegraphTicks > 0) {
				NPC.velocity *= 0.5f;
				if (Main.rand.NextBool(2))
					Dust.NewDust(NPC.position, NPC.width, NPC.height, DustID.Blood);
				if (--telegraphTicks == 0) {
					if (!hp.Hiding && TryTeleportBehind(player)) {
						HasTrack = true;
						lastKnown = player.Center;
						lostSightTicks = 0;
					}
					teleportCooldown = Main.rand.Next(8 * 60, 11 * 60);
				}
				return true;
			}

			if (phase != HauntPhase.Frenzy || hp.Hiding)
				return false;

			if (--teleportCooldown <= 0 && dist > 10 * Tile) {
				telegraphTicks = 75;
				SoundEngine.PlaySound(ScreamAudio.TeleportCue);
				HauntUISystem.FlashWarning(75);
				return true;
			}
			return false;
		}

		/// <summary>Brings her back if the player got far away, or if she's wedged somewhere.</summary>
		private bool UpdateFarAndStuck(Player player, HauntPlayer hp, float dist) {
			if (dist > 120 * Tile) {
				if (++farTicks > 3 * 60) {
					farTicks = 0;
					Relocate(player, 35, 55);
					return true;
				}
			}
			else {
				farTicks = 0;
			}

			// Stuck = trying to get somewhere and not moving, whether or not a path was found.
			// Escalates once a second: jump, then a new route plus another jump, then relocate.
			bool wantsToMove = currentGoal is Vector2 g && Vector2.Distance(NPC.Center, g) > 3 * Tile;
			if (++stuckTimer >= 60) {
				stuckTimer = 0;
				if (wantsToMove && Vector2.Distance(NPC.position, stuckAnchor) < Tile && dist > 4 * Tile) {
					stuckStrikes++;
					if (stuckStrikes == 1) {
						Hop(currentGoal.Value);
					}
					else if (stuckStrikes == 2) {
						wanderTimer = 0;
						repathTimer = 0;
						Hop(currentGoal.Value);
					}
					else {
						stuckStrikes = 0;
						Relocate(player, 20, 35);
						return true;
					}
				}
				else {
					stuckStrikes = 0;
				}
				stuckAnchor = NPC.position;
			}
			return false;
		}

		private Vector2? ChooseGoal(Player player, HauntPlayer hp, HauntPhase phase, float dist, out float speed) {
			bool frenzy = phase == HauntPhase.Frenzy;

			if (KnowsLocker) {
				speed = frenzy ? FrenzySpeed : HuntSpeed;
				return hp.HideCenter;
			}

			if (phase == HauntPhase.Stalking) {
				// She pretends not to know where you are: roam, drifting closer over time, never charging.
				speed = StalkSpeed;
				float keepAway = MathHelper.Lerp(30f, 12f, HauntEventSystem.StalkProgress) * Tile;
				if (dist < keepAway && CanSeeIgnoringPhase(player, hp)) {
					// Close enough. Stand still and stare.
					NPC.direction = NPC.spriteDirection = player.Center.X > NPC.Center.X ? 1 : -1;
					return null;
				}
				return Roam(player, dist, pull: 0.35f, keepAway);
			}

			if (HasTrack) {
				speed = frenzy ? FrenzySpeed : HuntSpeed;
				return lastKnown;
			}

			if (searchTicks > 0) {
				searchTicks--;
				speed = SearchSpeed;
				if (--wanderTimer <= 0) {
					wanderTimer = 90;
					wanderPoint = lastKnown + new Vector2(Main.rand.NextFloat(-10f, 10f) * Tile, Main.rand.NextFloat(-3f, 3f) * Tile);
				}
				return wanderPoint;
			}

			// Lost you: roam, drifting toward where you probably are.
			speed = frenzy ? RoamSpeed * 1.4f : RoamSpeed;
			return Roam(player, dist, pull: frenzy ? 0.65f : 0.5f, keepAway: 0f);
		}

		/// <summary>
		/// Wanders like a normal NPC: walks to a spot, sometimes stops to look around, then picks
		/// another. Each spot is pulled toward a rough guess of where the player is (never the exact
		/// position), so she drifts your way without homing in. Returns null while she's paused.
		/// </summary>
		private Vector2? Roam(Player player, float dist, float pull, float keepAway) {
			if (pauseTicks > 0) {
				pauseTicks--;
				if (pauseTicks % 50 == 0 && Main.rand.NextBool())
					NPC.direction = NPC.spriteDirection = -NPC.direction; // looks around
				return null;
			}

			// "Arrived" also covers a target she can't get any closer to, but only after giving the path
			// half a second, so an unreachable target can't make her re-plan every tick.
			wanderAge++;
			bool pathDone = pathIndex >= path.Count && wanderAge > 30;
			bool arrived = Vector2.Distance(NPC.Center, wanderPoint) < 2 * Tile || pathDone;
			if (--wanderTimer <= 0 || arrived) {
				if (arrived && Main.rand.NextBool(3)) {
					pauseTicks = Main.rand.Next(40, 130);
					wanderTimer = 0;
					return null;
				}
				wanderTimer = Main.rand.Next(3 * 60, 6 * 60);
				// Far away: she heads your way more firmly so she never loses the thread entirely.
				float p = dist > 50 * Tile ? Math.Max(pull, 0.75f) : pull;
				Vector2 roughGuess = player.Center + new Vector2(Main.rand.NextFloat(-14f, 14f), Main.rand.NextFloat(-6f, 6f)) * Tile;
				Vector2 point = Vector2.Lerp(NPC.Center, roughGuess, p);
				// Plus a sideways meander, so she doesn't walk a straight line at you.
				point += new Vector2(Main.rand.NextFloat(-8f, 8f), Main.rand.NextFloat(-3f, 3f)) * Tile;
				if (keepAway > 0f && Vector2.Distance(point, player.Center) < keepAway)
					point = player.Center + Vector2.Normalize(point - player.Center + new Vector2(0.01f, 0f)) * keepAway;
				wanderPoint = point;
				wanderAge = 0;
				repathTimer = 0;
			}
			return wanderPoint;
		}

		private bool CanSeeIgnoringPhase(Player player, HauntPlayer hp) =>
			!hp.Hiding && Collision.CanHitLine(NPC.position, NPC.width, NPC.height, player.position, player.width, player.height);

		// ---------------------------------------------------------------- movement
		private static Point PositionToNode(Vector2 position) =>
			new((int)Math.Round((position.X - NodeOffset.X) / Tile), (int)Math.Round((position.Y - NodeOffset.Y) / Tile));

		private static Vector2 NodeToPosition(Point node) => node.ToVector2() * Tile + NodeOffset;

		private static Point GoalToNode(Vector2 worldPoint) =>
			new((int)Math.Round(worldPoint.X / Tile) - HuntPathfinder.W / 2, (int)Math.Floor(worldPoint.Y / Tile) - HuntPathfinder.H + 2);

		/// <summary>A real jump with gravity and block collision, used to shake loose when stuck.</summary>
		private void Hop(Vector2 toward) {
			hopTicks = 45;
			int dir = toward.X >= NPC.Center.X ? 1 : -1;
			NPC.velocity = new Vector2(dir * 2.5f, -7.5f);
			NPC.direction = NPC.spriteDirection = dir;
			path.Clear();
		}

		private void MoveToward(Vector2? goal, float speed, Player player) {
			NPC.noTileCollide = false;

			if (hopTicks > 0) {
				hopTicks--;
				NPC.velocity.Y = Math.Min(NPC.velocity.Y + 0.35f, 10f);
				// Landed (vanilla collision zeroed the fall) or timed out: go back to planning.
				if (NPC.velocity.Y == 0f && hopTicks < 40 || hopTicks == 0) {
					hopTicks = 0;
					repathTimer = 0;
				}
				return;
			}

			if (goal is not Vector2 target) {
				path.Clear();
				NPC.velocity *= 0.8f;
				ApplyFallIfFloating();
				return;
			}

			bool goalJumped = Vector2.Distance(target, pathGoal) > 6 * Tile;
			if (--repathTimer <= 0 || goalJumped) {
				repathTimer = HauntEventSystem.Phase == HauntPhase.Frenzy ? 8 : HasTrack ? 12 : 20;
				pathGoal = target;
				HuntPathfinder.FindPath(PositionToNode(NPC.position), GoalToNode(target), path);
				pathIndex = 1;
				// Start from exactly the first node, so every move begins on the grid.
				if (path.Count > 1 && Vector2.Distance(NPC.position, NodeToPosition(path[0])) < 6f)
					NPC.position = NodeToPosition(path[0]);
			}

			// Reached a waypoint: snap onto it exactly. Arriving a few pixels low is what used to snag
			// her hitbox on the corner of a step.
			while (pathIndex < path.Count && Vector2.Distance(NPC.position, NodeToPosition(path[pathIndex])) < 3f) {
				NPC.position = NodeToPosition(path[pathIndex]);
				pathIndex++;
			}

			if (pathIndex < path.Count) {
				Point next = path[pathIndex];
				if (!HuntPathfinder.Fits(next.X, next.Y)) {
					// The world changed under the plan (a block was placed): stop and re-plan next tick.
					NPC.velocity = Vector2.Zero;
					repathTimer = 0;
					return;
				}
				Vector2 to = NodeToPosition(next) - NPC.position;
				float sp = speed;
				if (to.Y < -2f)
					sp *= 0.8f; // climbing is slower
				else if (to.Y > 2f && Math.Abs(to.X) < 2f)
					sp *= 1.6f; // dropping is faster
				NPC.velocity = to.Length() > sp ? Vector2.Normalize(to) * sp : to;
				// Every cell on the path was checked open (diagonals included), so vanilla collision
				// would only snag her on corners.
				NPC.noTileCollide = true;
			}
			else if (CanSeePlayer && Vector2.Distance(NPC.Center, player.Center) < 5 * Tile) {
				// End of the path but you're right there: lunge straight at you (tile collision still applies).
				NPC.velocity = Vector2.Normalize(player.Center - NPC.Center) * speed;
			}
			else {
				NPC.velocity *= 0.8f;
				ApplyFallIfFloating();
			}

			if (Math.Abs(NPC.velocity.X) > 0.1f)
				NPC.direction = NPC.spriteDirection = NPC.velocity.X > 0 ? 1 : -1;
		}

		private void ApplyFallIfFloating() {
			Point node = PositionToNode(NPC.position);
			if (!HuntPathfinder.Supported(node.X, node.Y))
				NPC.velocity.Y = Math.Min(NPC.velocity.Y + 0.35f, 10f);
		}

		private void MoveTo(Vector2 topLeft) {
			Puff(20);
			NPC.position = topLeft;
			NPC.velocity = Vector2.Zero;
			NPC.oldPosition = topLeft;
			hopTicks = 0;
			path.Clear();
			repathTimer = 0;
			stuckAnchor = topLeft;
			stuckStrikes = 0;
			Puff(20);
			SoundEngine.PlaySound(ScreamAudio.Teleport, NPC.Center);
		}

		private void Relocate(Player player, int minTiles, int maxTiles) {
			if (TryFindSpot(player, player.Center, minTiles, maxTiles, requireHidden: true, out Vector2 pos)
				|| TryFindSpot(player, player.Center, minTiles, maxTiles, requireHidden: false, out pos)) {
				MoveTo(pos);
			}
		}

		private bool TryTeleportBehind(Player player) {
			int dir = player.direction == 0 ? 1 : player.direction;
			for (int attempt = 0; attempt < 30; attempt++) {
				float d = Main.rand.NextFloat(9f, 13f);
				Point node = new((int)(player.Center.X / Tile - dir * d) - 1, (int)(player.position.Y / Tile) + Main.rand.Next(-3, 3));
				if (!HuntPathfinder.TryNudgeToFit(ref node, 3) || !HuntPathfinder.TryDropToFloor(ref node, 12))
					continue;
				Vector2 pos = NodeToPosition(node);
				if (Vector2.Distance(pos + new Vector2(HitboxWidth, HitboxHeight) / 2f, player.Center) < 7 * Tile)
					continue;
				MoveTo(pos);
				return true;
			}
			if (TryFindSpot(player, player.Center, 10, 18, requireHidden: false, out Vector2 fallback)) {
				MoveTo(fallback);
				return true;
			}
			return false;
		}

		/// <summary>Finds a top-left position with floor under it, minTiles to maxTiles from <paramref name="around"/>.</summary>
		public static bool TryFindSpot(Player player, Vector2 around, int minTiles, int maxTiles, bool requireHidden, out Vector2 topLeft) {
			for (int attempt = 0; attempt < 80; attempt++) {
				float d = Main.rand.NextFloat(minTiles, maxTiles);
				int sign = Main.rand.NextBool() ? 1 : -1;
				Point node = new((int)(around.X / Tile + sign * d), (int)(around.Y / Tile) + Main.rand.Next(-20, 12));
				if (!HuntPathfinder.TryNudgeToFit(ref node, 3) || !HuntPathfinder.TryDropToFloor(ref node, 40))
					continue;
				Vector2 pos = NodeToPosition(node);
				if (Vector2.Distance(pos, around) < minTiles * Tile * 0.8f)
					continue;
				if (requireHidden && Collision.CanHitLine(pos, HitboxWidth, HitboxHeight, player.position, player.width, player.height))
					continue;
				topLeft = pos;
				return true;
			}
			topLeft = default;
			return false;
		}

		private void Catch(Player player, HauntPlayer hp) {
			bool died = hp.CaughtBy(NPC, inLocker: false);
			if (!died) {
				// One-shot is off and you survived: she backs off instead of chain-hitting you.
				HasTrack = false;
				searchTicks = 0;
				Relocate(player, 25, 40);
			}
		}

		private void Puff(int count) {
			for (int i = 0; i < count; i++) {
				Dust d = Dust.NewDustDirect(NPC.position, NPC.width, NPC.height, DustID.Shadowflame, 0f, 0f, 100, default, 1.4f);
				d.velocity *= 1.5f;
				d.noGravity = true;
			}
		}

		// ---------------------------------------------------------------- visuals
		public override void FindFrame(int frameHeight) {
			int frame;
			if (Telegraphing) {
				frame = 5;
			}
			else if (NPC.velocity.Length() > 0.3f) {
				NPC.frameCounter += Math.Max(1.0, NPC.velocity.Length() * 0.6);
				if (NPC.frameCounter >= 8.0 * 4)
					NPC.frameCounter = 0;
				frame = 1 + (int)(NPC.frameCounter / 8.0);
			}
			else {
				NPC.frameCounter = 0;
				frame = 0;
			}
			NPC.frame.Y = frame * frameHeight;
		}

		public override Color? GetAlpha(Color drawColor) {
			// Always faintly visible, even in total darkness. You should be able to see her coming.
			Color c = Color.Lerp(drawColor, new Color(95, 90, 105), 0.35f);
			float opacity = (255 - NPC.alpha) / 255f;
			if (Telegraphing)
				opacity *= Main.rand.NextFloat(0.2f, 1f);
			return c * opacity;
		}
	}
}
