using ScreamAndRun.Common.Audio;
using Terraria.ModLoader;

namespace ScreamAndRun
{
	public class ScreamAndRun : Mod
	{
		public override void Load() {
			ScreamAudio.Load(this);
		}
	}
}
