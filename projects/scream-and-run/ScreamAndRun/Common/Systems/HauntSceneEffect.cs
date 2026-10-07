using ScreamAndRun.Common.Audio;
using Terraria;
using Terraria.ModLoader;

namespace ScreamAndRun.Common.Systems
{
	/// <summary>Swaps the music while the event runs. Wins over boss music from any mod.</summary>
	public class HauntSceneEffect : ModSceneEffect
	{
		public override int Music => ScreamAudio.Music;

		public override SceneEffectPriority Priority => SceneEffectPriority.BossHigh;

		public override bool IsSceneEffectActive(Player player) => HauntEventSystem.Active;
	}
}
