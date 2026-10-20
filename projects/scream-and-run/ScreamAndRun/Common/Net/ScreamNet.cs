using System.IO;
using ScreamAndRun.Common.Players;
using ScreamAndRun.Common.Systems;
using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace ScreamAndRun.Common.Net
{
	/// <summary>
	/// Every packet the mod sends. The server runs the event and Onryo's AI; clients report what only
	/// they can know (their light level, noise and hiding state) and play every sound and screen effect.
	/// In single-player nothing is sent: the same calls run locally.
	/// </summary>
	public enum ScreamPacket : byte
	{
		/// <summary>Client → server: this player read the Love Letter.</summary>
		RequestStart,
		/// <summary>Server → clients: event active, target, timers, Onryo's index, survived flag.</summary>
		EventState,
		/// <summary>Server → clients: a chat line, optional banner and sound.</summary>
		Announce,
		/// <summary>Server → the hiding player: she opened your locker.</summary>
		LockerOpened,
		/// <summary>Client → server: she touched you and you survived (one-shot off), so she backs off.</summary>
		BackOff,
		/// <summary>Both ways: a player's hiding state, noise and detection radius.</summary>
		PlayerState,
	}

	public enum AnnounceSound : byte
	{
		None,
		Noticed,
		PhaseChange,
		GiveUp,
	}

	public static class ScreamNet
	{
		public static bool IsClient => Main.netMode == NetmodeID.MultiplayerClient;
		public static bool IsServer => Main.netMode == NetmodeID.Server;
		/// <summary>True where the event's rules run: the server, or single-player.</summary>
		public static bool Authoritative => Main.netMode != NetmodeID.MultiplayerClient;

		public static ModPacket Create(ScreamPacket type) {
			ModPacket packet = ModContent.GetInstance<ScreamAndRun>().GetPacket();
			packet.Write((byte)type);
			return packet;
		}

		public static void Handle(BinaryReader reader, int whoAmI) {
			var type = (ScreamPacket)reader.ReadByte();
			switch (type) {
				case ScreamPacket.RequestStart: {
					byte player = reader.ReadByte();
					if (IsServer && player == whoAmI)
						HauntEventSystem.Start(Main.player[player]);
					break;
				}
				case ScreamPacket.EventState:
					HauntEventSystem.ReceiveState(reader);
					break;
				case ScreamPacket.Announce:
					HauntEventSystem.ReceiveAnnounce(reader);
					break;
				case ScreamPacket.LockerOpened:
					if (IsClient)
						Main.LocalPlayer.GetModPlayer<HauntPlayer>().OnLockerOpened();
					break;
				case ScreamPacket.BackOff:
					if (IsServer && whoAmI == HauntEventSystem.TargetIndex)
						HauntEventSystem.Boss?.BackOff();
					break;
				case ScreamPacket.PlayerState: {
					byte player = reader.ReadByte();
					// A client may only speak for itself.
					if (IsServer)
						player = (byte)whoAmI;
					HauntPlayer hp = Main.player[player].GetModPlayer<HauntPlayer>();
					hp.ReadState(reader);
					if (IsServer)
						hp.SendState(-1, whoAmI); // forward to everyone else
					break;
				}
			}
		}
	}
}
