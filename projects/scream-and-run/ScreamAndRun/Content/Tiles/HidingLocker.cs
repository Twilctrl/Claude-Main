using Microsoft.Xna.Framework;
using ScreamAndRun.Common.Players;
using ScreamAndRun.Content.Items;
using Terraria;
using Terraria.DataStructures;
using Terraria.ID;
using Terraria.ModLoader;
using Terraria.ObjectData;

namespace ScreamAndRun.Content.Tiles
{
	/// <summary>A 2x3 school locker. Right-click to climb in or out (Jump also gets you out).</summary>
	public class HidingLocker : ModTile
	{
		public override string Texture => "ScreamAndRun/Assets/Textures/Tiles/HidingLocker";

		public override void SetStaticDefaults() {
			Main.tileFrameImportant[Type] = true;
			Main.tileNoAttach[Type] = true;
			Main.tileLavaDeath[Type] = true;

			TileObjectData.newTile.CopyFrom(TileObjectData.Style2xX);
			TileObjectData.newTile.Height = 3;
			TileObjectData.newTile.CoordinateHeights = new[] { 16, 16, 16 };
			TileObjectData.newTile.Origin = new Point16(0, 2);
			TileObjectData.addTile(Type);

			DustType = DustID.Iron;
			AddMapEntry(new Color(92, 104, 122), CreateMapEntryName());
		}

		public override bool RightClick(int i, int j) {
			Tile tile = Main.tile[i, j];
			int left = i - tile.TileFrameX % 36 / 18;
			int top = j - tile.TileFrameY % 54 / 18;
			Main.LocalPlayer.GetModPlayer<HauntPlayer>().ToggleHiding(new Point(left, top));
			return true;
		}

		public override void MouseOver(int i, int j) {
			Player player = Main.LocalPlayer;
			player.noThrow = 2;
			player.cursorItemIconEnabled = true;
			player.cursorItemIconID = ModContent.ItemType<HidingLockerItem>();
		}
	}
}
