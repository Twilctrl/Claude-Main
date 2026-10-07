using System.ComponentModel;
using Terraria.ModLoader;
using Terraria.ModLoader.Config;

namespace ScreamAndRun.Common.Configs
{
	/// <summary>Gameplay settings. Changes apply immediately, even mid-event.</summary>
	public class ScreamConfig : ModConfig
	{
		public override ConfigScope Mode => ConfigScope.ServerSide;

		public static ScreamConfig Instance => ModContent.GetInstance<ScreamConfig>();

		[DefaultValue(300)]
		[Range(30, 1800)]
		public int SurvivalSeconds;

		[DefaultValue(1f)]
		[Range(0.25f, 3f)]
		[Increment(0.05f)]
		[Slider]
		public float SpeedMultiplier;

		[DefaultValue(true)]
		public bool OneShot;
	}

	/// <summary>Per-player presentation settings.</summary>
	public class ScreamClientConfig : ModConfig
	{
		public override ConfigScope Mode => ConfigScope.ClientSide;

		public static ScreamClientConfig Instance => ModContent.GetInstance<ScreamClientConfig>();

		[DefaultValue(true)]
		public bool ScreenDistortion;

		[DefaultValue(true)]
		public bool JumpscareFlash;
	}
}
