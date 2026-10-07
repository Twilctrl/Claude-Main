using ScreamAndRun.Content.Tiles;
using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace ScreamAndRun.Content.Items
{
	public class HidingLockerItem : ModItem
	{
		public override string Texture => "ScreamAndRun/Assets/Textures/Items/HidingLockerItem";

		public override void SetStaticDefaults() {
			Item.ResearchUnlockCount = 1;
		}

		public override void SetDefaults() {
			Item.DefaultToPlaceableTile(ModContent.TileType<HidingLocker>());
			Item.width = 20;
			Item.height = 30;
			Item.maxStack = 99;
			Item.value = Item.sellPrice(silver: 20);
		}

		public override void AddRecipes() {
			CreateRecipe()
				.AddRecipeGroup(RecipeGroupID.IronBar, 3)
				.AddRecipeGroup(RecipeGroupID.Wood, 8)
				.AddTile(TileID.WorkBenches)
				.Register();
		}
	}
}
