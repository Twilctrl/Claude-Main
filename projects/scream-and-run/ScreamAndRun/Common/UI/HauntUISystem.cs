using System;
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using ReLogic.Content;
using ScreamAndRun.Common.Configs;
using ScreamAndRun.Common.Players;
using ScreamAndRun.Common.Systems;
using ScreamAndRun.Content.NPCs;
using Terraria;
using Terraria.GameContent;
using Terraria.Localization;
using Terraria.ModLoader;
using Terraria.UI;

namespace ScreamAndRun.Common.UI
{
	/// <summary>
	/// Everything drawn on screen: the vignette, distortion, timer, proximity warning,
	/// hiding view, banners, the blood over the map, and the jumpscare.
	/// Purely visual: it never changes vanilla UI except hiding the minimap during the event.
	/// </summary>
	public class HauntUISystem : ModSystem
	{
		private static Asset<Texture2D> vignette, bloodSplat, jumpscareFace;

		private static string bannerText;
		private static Color bannerColor;
		private static int bannerTicks, bannerDuration;
		private static int warningTicks;
		private static int jumpscareTicks;
		private const int JumpscareDuration = 70;

		private static readonly Color TextRed = new(230, 40, 60);

		public static void ShowBanner(string text, Color color, int ticks) {
			bannerText = text;
			bannerColor = color;
			bannerTicks = bannerDuration = ticks;
		}

		public static void FlashWarning(int ticks) => warningTicks = ticks;

		public static void TriggerJumpscare() => jumpscareTicks = JumpscareDuration;

		public override void Load() {
			if (Main.dedServ)
				return;
			vignette = ModContent.Request<Texture2D>("ScreamAndRun/Assets/Textures/UI/Vignette");
			bloodSplat = ModContent.Request<Texture2D>("ScreamAndRun/Assets/Textures/UI/BloodSplat");
			jumpscareFace = ModContent.Request<Texture2D>("ScreamAndRun/Assets/Textures/UI/Jumpscare");
		}

		public override void Unload() {
			vignette = bloodSplat = jumpscareFace = null;
		}

		public override void OnWorldUnload() {
			bannerTicks = warningTicks = jumpscareTicks = 0;
		}

		public override void UpdateUI(GameTime gameTime) {
			if (bannerTicks > 0)
				bannerTicks--;
			if (warningTicks > 0)
				warningTicks--;
			if (jumpscareTicks > 0)
				jumpscareTicks--;
		}

		// ---------------------------------------------------------------- layers
		public override void ModifyInterfaceLayers(List<GameInterfaceLayer> layers) {
			bool active = HauntEventSystem.Active;

			if (active) {
				// Blood covers the minimap and the map overlay.
				int mapIndex = layers.FindIndex(l => l.Name == "Vanilla: Map / Minimap");
				if (mapIndex >= 0)
					layers[mapIndex].Active = false;
			}

			int mouseText = layers.FindIndex(l => l.Name == "Vanilla: Mouse Text");
			if (mouseText < 0)
				mouseText = layers.Count;

			if (active || bannerTicks > 0) {
				layers.Insert(mouseText, new LegacyGameInterfaceLayer("ScreamAndRun: Atmosphere", () => {
					DrawAtmosphere(Main.spriteBatch);
					return true;
				}, InterfaceScaleType.None));
				layers.Insert(mouseText + 1, new LegacyGameInterfaceLayer("ScreamAndRun: HUD", () => {
					DrawHud(Main.spriteBatch);
					return true;
				}, InterfaceScaleType.UI));
			}

			if (jumpscareTicks > 0) {
				layers.Add(new LegacyGameInterfaceLayer("ScreamAndRun: Jumpscare", () => {
					DrawJumpscare(Main.spriteBatch);
					return true;
				}, InterfaceScaleType.None));
			}
		}

		// ---------------------------------------------------------------- full-screen effects
		private static void DrawAtmosphere(SpriteBatch sb) {
			if (!HauntEventSystem.Active)
				return;
			Player player = Main.LocalPlayer;
			HauntPlayer hp = player.GetModPlayer<HauntPlayer>();
			int w = Main.screenWidth, h = Main.screenHeight;
			Texture2D pixel = TextureAssets.MagicPixel.Value;
			float c = hp.SmoothCloseness;
			if (hp.KnowsLocker)
				c = Math.Max(c, 0.8f);

			if (hp.Hiding)
				DrawLockerView(sb, pixel, w, h);

			// Vignette: the clear hole shrinks and darkens as she gets closer.
			Texture2D vig = vignette.Value;
			float size = Math.Max(w, h) * MathHelper.Lerp(2.0f, 1.05f, c);
			Vector2 center = new(w / 2f, h / 2f);
			float vigScale = size / vig.Width;
			sb.Draw(vig, center, null, Color.Black * MathHelper.Lerp(0.3f, 0.97f, c), 0f, vig.Size() / 2f, vigScale, SpriteEffects.None, 0f);
			float pulse = 0.5f + 0.5f * (float)Math.Sin(Main.GlobalTimeWrappedHourly * MathHelper.Lerp(2f, 9f, c));
			sb.Draw(vig, center, null, new Color(140, 0, 10) * (c * c * 0.55f * pulse), 0f, vig.Size() / 2f, vigScale * 0.95f, SpriteEffects.None, 0f);

			// Distortion: torn scanlines, static and a faint red flicker.
			if (ScreamClientConfig.Instance.ScreenDistortion && c > 0.4f) {
				float k = (c - 0.4f) / 0.6f;
				int bands = (int)(k * 7f);
				for (int i = 0; i < bands; i++) {
					int y = Main.rand.Next(h);
					int bh = Main.rand.Next(2, 10);
					int dx = Main.rand.Next(-30, 30);
					Color col = Main.rand.NextBool(3) ? new Color(160, 0, 20) : Main.rand.NextBool() ? Color.Black : Color.White;
					sb.Draw(pixel, new Rectangle(dx, y, w, bh), col * Main.rand.NextFloat(0.05f, 0.22f) * k);
				}
				int dots = (int)(k * 160f);
				for (int i = 0; i < dots; i++) {
					int s = Main.rand.Next(1, 3);
					sb.Draw(pixel, new Rectangle(Main.rand.Next(w), Main.rand.Next(h), s, s), Color.White * Main.rand.NextFloat(0.1f, 0.4f) * k);
				}
				if (Main.rand.NextBool(30))
					sb.Draw(pixel, new Rectangle(0, 0, w, h), new Color(120, 0, 0) * 0.12f * k);
			}

			// Teleport wind-up: the screen strobes red.
			if (warningTicks > 0 && warningTicks / 6 % 2 == 0)
				sb.Draw(pixel, new Rectangle(0, 0, w, h), new Color(150, 0, 0) * 0.18f);
		}

		/// <summary>Inside a locker you only see out through the vents.</summary>
		private static void DrawLockerView(SpriteBatch sb, Texture2D pixel, int w, int h) {
			Color dark = Color.Black * 0.9f;
			int slitW = (int)(w * 0.45f);
			int slitH = Math.Max(6, h / 60);
			int gap = slitH * 2;
			const int slits = 4;
			int totalH = slits * slitH + (slits - 1) * gap;
			int x0 = (w - slitW) / 2;
			int y0 = (h - totalH) / 2 - h / 10;

			sb.Draw(pixel, new Rectangle(0, 0, w, y0), dark);
			sb.Draw(pixel, new Rectangle(0, y0 + totalH, w, h - y0 - totalH), dark);
			sb.Draw(pixel, new Rectangle(0, y0, x0, totalH), dark);
			sb.Draw(pixel, new Rectangle(x0 + slitW, y0, w - x0 - slitW, totalH), dark);
			for (int i = 0; i < slits - 1; i++)
				sb.Draw(pixel, new Rectangle(x0, y0 + slitH + i * (slitH + gap), slitW, gap), dark);
		}

		// ---------------------------------------------------------------- HUD
		private static void DrawHud(SpriteBatch sb) {
			float uiW = Main.screenWidth / Main.UIScale;
			float uiH = Main.screenHeight / Main.UIScale;

			if (bannerTicks > 0 && bannerText != null) {
				float t = bannerTicks / (float)bannerDuration;
				float alpha = Math.Min(1f, Math.Min(t * 4f, (1f - t) * 8f + 0.2f));
				Utils.DrawBorderStringBig(sb, bannerText, new Vector2(uiW / 2f, uiH * 0.3f), bannerColor * alpha, 1.1f, 0.5f, 0.5f);
			}

			if (!HauntEventSystem.Active)
				return;

			Player player = Main.LocalPlayer;
			HauntPlayer hp = player.GetModPlayer<HauntPlayer>();
			HauntPhase phase = HauntEventSystem.Phase;
			Texture2D pixel = TextureAssets.MagicPixel.Value;

			// Blood over the minimap frame.
			if (Main.mapEnabled && Main.mapStyle == 1)
				DrawBloodBlob(sb, new Rectangle(Main.miniMapX - 6, Main.miniMapY - 6, Main.miniMapWidth + 12, Main.miniMapHeight + 12));

			// Countdown.
			int seconds = (int)Math.Ceiling(HauntEventSystem.TicksRemaining / 60f);
			string time = $"{seconds / 60}:{seconds % 60:00}";
			Color timeColor = phase == HauntPhase.Frenzy ? (Main.GameUpdateCount / 15 % 2 == 0 ? TextRed : Color.White) : Color.White;
			float x = uiW / 2f;
			float y = 46f;
			Utils.DrawBorderStringBig(sb, time, new Vector2(x, y), timeColor, 0.9f, 0.5f, 0f);
			y += 44f;

			string phaseKey = phase switch {
				HauntPhase.Stalking => "PhaseStalking",
				HauntPhase.Frenzy => "PhaseFrenzy",
				_ => "PhaseHunting",
			};
			Utils.DrawBorderString(sb, Language.GetTextValue("Mods.ScreamAndRun.UI." + phaseKey), new Vector2(x, y), TextRed, 1f, 0.5f, 0f);
			y += 28f;

			// Proximity warning.
			float c = hp.SmoothCloseness;
			string distKey = c > 0.85f ? "Behind" : c > 0.6f ? "Close" : c > 0.3f ? "Near" : "Far";
			Color barColor = c > 0.6f ? TextRed : c > 0.3f ? new Color(240, 180, 40) : new Color(120, 200, 120);
			if (c > 0.85f && Main.GameUpdateCount / 8 % 2 == 0)
				barColor = Color.White;
			const int barW = 220, barH = 10;
			var bar = new Rectangle((int)(x - barW / 2f), (int)y, barW, barH);
			sb.Draw(pixel, new Rectangle(bar.X - 2, bar.Y - 2, bar.Width + 4, bar.Height + 4), Color.Black * 0.7f);
			sb.Draw(pixel, new Rectangle(bar.X, bar.Y, (int)(bar.Width * c), bar.Height), barColor);
			Utils.DrawBorderString(sb, Language.GetTextValue("Mods.ScreamAndRun.UI.Presence") + ": " + Language.GetTextValue("Mods.ScreamAndRun.UI." + distKey),
				new Vector2(x, y + 14f), barColor, 0.85f, 0.5f, 0f);
			y += 38f;

			// Awareness and noise.
			string awareKey = hp.KnowsLocker ? "LockerFound" : hp.SeenByHer ? "Seen" : hp.Tracked ? "Tracked" : "Unseen";
			Color awareColor = hp.KnowsLocker || hp.SeenByHer ? TextRed : hp.Tracked ? new Color(240, 180, 40) : new Color(170, 170, 190);
			Utils.DrawBorderString(sb, Language.GetTextValue("Mods.ScreamAndRun.UI." + awareKey), new Vector2(x, y), awareColor, 0.85f, 0.5f, 0f);
			y += 22f;

			const int noiseW = 120;
			var noiseBar = new Rectangle((int)(x - noiseW / 2f), (int)y + 4, noiseW, 6);
			sb.Draw(pixel, new Rectangle(noiseBar.X - 1, noiseBar.Y - 1, noiseBar.Width + 2, noiseBar.Height + 2), Color.Black * 0.7f);
			sb.Draw(pixel, new Rectangle(noiseBar.X, noiseBar.Y, (int)(noiseBar.Width * hp.Noise), noiseBar.Height), hp.Noise >= 0.5f ? TextRed : new Color(200, 200, 220));
			Utils.DrawBorderString(sb, Language.GetTextValue("Mods.ScreamAndRun.UI.Noise"), new Vector2(noiseBar.X - 8, y), Color.LightGray, 0.75f, 1f, 0f);
			y += 18f;

			var tags = new List<string>();
			if (hp.CrouchingStill)
				tags.Add(Language.GetTextValue("Mods.ScreamAndRun.UI.Crouched"));
			if (hp.InDarkness)
				tags.Add(Language.GetTextValue("Mods.ScreamAndRun.UI.Dark"));
			if (tags.Count > 0)
				Utils.DrawBorderString(sb, string.Join("  -  ", tags), new Vector2(x, y), new Color(150, 170, 220), 0.75f, 0.5f, 0f);

			if (hp.Hiding) {
				float danger = Math.Max(hp.HideTicks / (float)Tsubaki.MaxHideTicks, hp.SearchPressure / (float)(hp.SeenEntering ? Tsubaki.LockerSearchLimitSeen : Tsubaki.LockerSearchLimitUnseen));
				Utils.DrawBorderString(sb, Language.GetTextValue("Mods.ScreamAndRun.UI.Hiding"), new Vector2(x, uiH * 0.72f), Color.White, 0.9f, 0.5f, 0f);
				var hideBar = new Rectangle((int)(x - 100), (int)(uiH * 0.72f) + 26, 200, 6);
				sb.Draw(pixel, hideBar, Color.Black * 0.7f);
				sb.Draw(pixel, new Rectangle(hideBar.X, hideBar.Y, (int)(hideBar.Width * MathHelper.Clamp(danger, 0f, 1f)), hideBar.Height), TextRed);
			}

			if (warningTicks > 0 && warningTicks / 6 % 2 == 0)
				Utils.DrawBorderStringBig(sb, "!", new Vector2(x, uiH * 0.42f), TextRed, 2f, 0.5f, 0.5f);
		}

		private static void DrawBloodBlob(SpriteBatch sb, Rectangle area) {
			Texture2D pixel = TextureAssets.MagicPixel.Value;
			sb.Draw(pixel, area, new Color(70, 0, 6));
			Texture2D splat = bloodSplat.Value;
			sb.Draw(splat, area, Color.White);
		}

		// ---------------------------------------------------------------- fullscreen map
		public override void PostDrawFullscreenMap(ref string mouseText) {
			if (!HauntEventSystem.Active)
				return;
			mouseText = string.Empty;
			SpriteBatch sb = Main.spriteBatch;
			int w = Main.screenWidth, h = Main.screenHeight;
			sb.Draw(TextureAssets.MagicPixel.Value, new Rectangle(0, 0, w, h), new Color(58, 0, 6));

			// Layered splatters at fixed spots, so it reads as one painted-over sheet.
			Texture2D splat = bloodSplat.Value;
			var rng = new Random(4242);
			for (int i = 0; i < 26; i++) {
				var pos = new Vector2(rng.Next(-100, w), rng.Next(-100, h));
				float scale = 1f + (float)rng.NextDouble() * 2.5f;
				Color tint = Color.Lerp(Color.White, new Color(90, 0, 0), (float)rng.NextDouble() * 0.6f);
				sb.Draw(splat, pos, null, tint, (float)(rng.NextDouble() * MathHelper.TwoPi), splat.Size() / 2f, scale, SpriteEffects.None, 0f);
			}
			string text = Language.GetTextValue("Mods.ScreamAndRun.UI.MapBlood");
			Vector2 jitter = Main.rand.NextVector2Circular(2f, 2f);
			Utils.DrawBorderStringBig(sb, text, new Vector2(w / 2f, h / 2f) + jitter, new Color(255, 210, 210), 1.2f, 0.5f, 0.5f);
		}

		// ---------------------------------------------------------------- jumpscare
		private static void DrawJumpscare(SpriteBatch sb) {
			int w = Main.screenWidth, h = Main.screenHeight;
			Texture2D pixel = TextureAssets.MagicPixel.Value;
			Texture2D face = jumpscareFace.Value;
			int elapsed = JumpscareDuration - jumpscareTicks;
			float fade = jumpscareTicks < 20 ? jumpscareTicks / 20f : 1f;
			bool flash = ScreamClientConfig.Instance.JumpscareFlash;

			sb.Draw(pixel, new Rectangle(0, 0, w, h), Color.Black * fade);
			float scale = Math.Max(w / (float)face.Width, h / (float)face.Height) * (1.05f + elapsed * 0.006f);
			Vector2 shake = flash ? Main.rand.NextVector2Circular(14f, 14f) * fade : Vector2.Zero;
			sb.Draw(face, new Vector2(w / 2f, h / 2f) + shake, null, Color.White * fade, 0f, face.Size() / 2f, scale, SpriteEffects.None, 0f);
			if (flash && elapsed < 5)
				sb.Draw(pixel, new Rectangle(0, 0, w, h), Color.White * (1f - elapsed / 5f));
		}

		// ---------------------------------------------------------------- camera shake
		public override void ModifyScreenPosition() {
			if (!ScreamClientConfig.Instance.ScreenDistortion || Main.gamePaused)
				return;
			float c = HauntEventSystem.Active ? Main.LocalPlayer.GetModPlayer<HauntPlayer>().SmoothCloseness : 0f;
			float strength = c > 0.6f ? (c - 0.6f) / 0.4f * 3f : 0f;
			if (jumpscareTicks > 20)
				strength = Math.Max(strength, 8f);
			if (strength > 0f)
				Main.screenPosition += Main.rand.NextVector2Circular(strength, strength);
		}
	}
}
