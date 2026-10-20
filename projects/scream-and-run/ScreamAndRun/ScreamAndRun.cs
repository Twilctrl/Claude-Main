using System.IO;
using ScreamAndRun.Common.Audio;
using ScreamAndRun.Common.Net;
using Terraria.ModLoader;

namespace ScreamAndRun
{
	public class ScreamAndRun : Mod
	{
		public override void Load() {
			ScreamAudio.Load(this);
		}

		public override void HandlePacket(BinaryReader reader, int whoAmI) => ScreamNet.Handle(reader, whoAmI);
	}
}
