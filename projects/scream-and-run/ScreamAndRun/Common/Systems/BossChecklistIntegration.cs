using System.Collections.Generic;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using ReLogic.Content;
using ScreamAndRun.Content.Items;
using ScreamAndRun.Content.NPCs;
using Terraria;
using Terraria.Localization;
using Terraria.ModLoader;

namespace ScreamAndRun.Common.Systems
{
	/// <summary>
	/// Registers Onryo with Boss Checklist when it's installed (no hard dependency). She can't be
	/// killed, so she counts as "defeated" once anyone survives her in the world.
	/// API: https://github.com/JavidPack/BossChecklist/wiki/Support-using-Mod-Call
	/// </summary>
	public class BossChecklistIntegration : ModSystem
	{
		// Just before King Slime (1.0): the summon is craftable from early-game materials.
		private const float Progression = 0.9f;

		public override void PostSetupContent() {
			if (!ModLoader.TryGetMod("BossChecklist", out Mod bossChecklist))
				return;

			int letter = ModContent.ItemType<LoveLetter>();
			int locker = ModContent.ItemType<HidingLockerItem>();

			object result = bossChecklist.Call(
				"LogBoss",
				Mod,
				"Onryo",
				Progression,
				() => HauntEventSystem.SurvivedOnryo,
				ModContent.NPCType<Onryo>(),
				new Dictionary<string, object> {
					["spawnItems"] = letter,
					["collectibles"] = new List<int> { ModContent.ItemType<TornRibbon>() },
					["spawnInfo"] = Mod.GetLocalization("BossChecklist.SpawnInfo").WithFormatArgs(letter, locker),
					["despawnMessage"] = Mod.GetLocalization("BossChecklist.Despawn"),
					// A head icon that isn't her map boss-head (that would show where she is on the map).
					["overrideHeadTextures"] = "ScreamAndRun/Assets/Textures/NPCs/OnryoChecklistHead",
					["customPortrait"] = new System.Action<SpriteBatch, Rectangle, Color>(DrawPortrait),
				});
			if (result as string != "Success")
				Mod.Logger.Warn($"Boss Checklist didn't accept Onryo's entry (returned \"{result}\").");
		}

		/// <summary>Boss Log portrait: her idle frame, scaled up crisply and centred.</summary>
		private static void DrawPortrait(SpriteBatch spriteBatch, Rectangle rect, Color color) {
			Texture2D texture = ModContent.Request<Texture2D>("ScreamAndRun/Assets/Textures/NPCs/Onryo", AssetRequestMode.ImmediateLoad).Value;
			int frameHeight = texture.Height / Main.npcFrameCount[ModContent.NPCType<Onryo>()];
			var frame = new Rectangle(0, 0, texture.Width, frameHeight);
			float scale = System.Math.Min(rect.Width / (float)frame.Width, rect.Height / (float)frame.Height) * 0.9f;
			scale = System.Math.Max(1f, (float)System.Math.Floor(scale)); // whole-number scale keeps the pixels square
			Vector2 position = rect.Center.ToVector2() - frame.Size() * scale / 2f;
			spriteBatch.Draw(texture, position, frame, color, 0f, Vector2.Zero, scale, SpriteEffects.None, 0f);
		}
	}
}
