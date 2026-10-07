using ScreamAndRun.Common.Systems;
using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace ScreamAndRun.Content.Items
{
	/// <summary>Starts the event. Not consumed: she'll always read another one.</summary>
	public class LoveLetter : ModItem
	{
		public override string Texture => "ScreamAndRun/Assets/Textures/Items/LoveLetter";

		public override void SetStaticDefaults() {
			Item.ResearchUnlockCount = 1;
		}

		public override void SetDefaults() {
			Item.width = 28;
			Item.height = 20;
			Item.maxStack = 1;
			Item.rare = ItemRarityID.Pink;
			Item.useStyle = ItemUseStyleID.HoldUp;
			Item.useTime = 45;
			Item.useAnimation = 45;
			Item.consumable = false;
			Item.UseSound = SoundID.Item44;
			Item.value = Item.sellPrice(silver: 5);
		}

		public override bool CanUseItem(Player player) => HauntEventSystem.CanStart(player);

		public override bool? UseItem(Player player) {
			if (player.whoAmI == Main.myPlayer)
				HauntEventSystem.Start(player);
			return true;
		}

		public override void AddRecipes() {
			CreateRecipe()
				.AddIngredient(ItemID.Silk, 3)
				.AddIngredient(ItemID.Lens)
				.AddTile(TileID.WorkBenches)
				.Register();
		}
	}
}
