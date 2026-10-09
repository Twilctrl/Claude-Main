using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace ScreamAndRun.Content.Items
{
	/// <summary>The survival reward: Onryo's hair ribbon, as a vanity hat.</summary>
	[AutoloadEquip(EquipType.Head)]
	public class TornRibbon : ModItem
	{
		public override string Texture => "ScreamAndRun/Assets/Textures/Items/TornRibbon";

		public override void SetStaticDefaults() {
			Item.ResearchUnlockCount = 1;
			// Keep the wearer's hair visible under the ribbon.
			int slot = EquipLoader.GetEquipSlot(Mod, Name, EquipType.Head);
			if (slot > 0)
				ArmorIDs.Head.Sets.DrawHatHair[slot] = true;
		}

		public override void SetDefaults() {
			Item.width = 26;
			Item.height = 18;
			Item.vanity = true;
			Item.rare = ItemRarityID.Red;
			Item.value = Item.sellPrice(gold: 1);
		}
	}
}
