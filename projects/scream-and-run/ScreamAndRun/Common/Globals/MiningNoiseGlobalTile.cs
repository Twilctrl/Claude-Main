using Microsoft.Xna.Framework;
using ScreamAndRun.Common.Players;
using ScreamAndRun.Common.Systems;
using Terraria;
using Terraria.ModLoader;

namespace ScreamAndRun.Common.Globals
{
	/// <summary>Mining near the player is noisy. Only reads state; never changes any tile.</summary>
	public class MiningNoiseGlobalTile : GlobalTile
	{
		public override void KillTile(int i, int j, int type, ref bool fail, ref bool effectOnly, ref bool noItem) {
			if (!HauntEventSystem.Active || effectOnly || Main.dedServ)
				return;
			Player player = Main.LocalPlayer;
			if (player.itemAnimation <= 0)
				return;
			if (Vector2.DistanceSquared(player.Center, new Vector2(i * 16 + 8, j * 16 + 8)) > 16f * 16f * 16f * 16f)
				return;
			player.GetModPlayer<HauntPlayer>().AddNoise(0.06f);
		}
	}
}
