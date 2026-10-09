using System;
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using Terraria;

namespace ScreamAndRun.Common.AI
{
	/// <summary>
	/// Breadth-first search over the tile grid for a body <see cref="W"/>x<see cref="H"/> tiles big.
	/// A node is the top-left tile of that body. She moves only through open space and
	/// clings to surfaces: from a node touching a floor, wall or ceiling she can move in any
	/// direction (so she crawls up walls and along ceilings), and from a node touching
	/// nothing she can only fall. That means she never passes through solid tiles.
	/// If the goal can't be reached, the path leads to the closest reachable node.
	/// </summary>
	public static class HuntPathfinder
	{
		public const int W = 2;
		public const int H = 3;
		private const int Radius = 70;
		private const int Size = Radius * 2 + 1;
		private const int MaxExpansions = 14000;

		private static readonly int[] parent = new int[Size * Size];
		private static readonly int[] visitStamp = new int[Size * Size];
		private static readonly int[] fitsStamp = new int[Size * Size];
		private static readonly bool[] fitsValue = new bool[Size * Size];
		private static readonly int[] supStamp = new int[Size * Size];
		private static readonly bool[] supValue = new bool[Size * Size];
		private static readonly int[] queue = new int[Size * Size];
		private static int stamp;
		private static int originX, originY;

		private static readonly Point[] Moves = {
			new(1, 0), new(-1, 0), new(0, 1), new(0, -1),
			new(1, 1), new(-1, 1), new(1, -1), new(-1, -1),
		};

		/// <summary>A tile that blocks movement (platforms and actuated tiles don't).</summary>
		public static bool IsSolid(int x, int y) {
			if (!WorldGen.InWorld(x, y, 1))
				return true;
			Tile t = Main.tile[x, y];
			return t.HasTile && !t.IsActuated && Main.tileSolid[t.TileType] && !Main.tileSolidTop[t.TileType];
		}

		/// <summary>A tile she can stand on, including platforms.</summary>
		public static bool IsStandable(int x, int y) {
			if (!WorldGen.InWorld(x, y, 1))
				return true;
			Tile t = Main.tile[x, y];
			return t.HasTile && !t.IsActuated && (Main.tileSolid[t.TileType] || Main.tileSolidTop[t.TileType]);
		}

		public static bool Fits(int x, int y) {
			for (int dx = 0; dx < W; dx++) {
				for (int dy = 0; dy < H; dy++) {
					if (IsSolid(x + dx, y + dy))
						return false;
				}
			}
			return true;
		}

		public static bool OnFloor(int x, int y) {
			for (int dx = 0; dx < W; dx++) {
				if (IsStandable(x + dx, y + H))
					return true;
			}
			return false;
		}

		public static bool Supported(int x, int y) {
			if (OnFloor(x, y))
				return true;
			for (int dy = 0; dy < H; dy++) {
				if (IsSolid(x - 1, y + dy) || IsSolid(x + W, y + dy))
					return true;
			}
			for (int dx = 0; dx < W; dx++) {
				if (IsSolid(x + dx, y - 1))
					return true;
			}
			// Ledge corners just below her feet on either side: lets her pull herself up a step
			// even with no wall to climb, which is most of the surface.
			return IsStandable(x - 1, y + H) || IsStandable(x + W, y + H);
		}

		/// <summary>From a node, fall straight down to the first node with a floor under it.</summary>
		public static bool TryDropToFloor(ref Point node, int maxDrop) {
			if (!Fits(node.X, node.Y))
				return false;
			for (int i = 0; i < maxDrop; i++) {
				if (OnFloor(node.X, node.Y))
					return true;
				if (!Fits(node.X, node.Y + 1))
					return false;
				node.Y++;
			}
			return OnFloor(node.X, node.Y);
		}

		/// <summary>Searches a small square around <paramref name="node"/> for one her body fits in.</summary>
		public static bool TryNudgeToFit(ref Point node, int range) {
			if (Fits(node.X, node.Y))
				return true;
			for (int r = 1; r <= range; r++) {
				for (int dy = -r; dy <= r; dy++) {
					for (int dx = -r; dx <= r; dx++) {
						if (Math.Abs(dx) != r && Math.Abs(dy) != r)
							continue;
						if (Fits(node.X + dx, node.Y + dy)) {
							node = new Point(node.X + dx, node.Y + dy);
							return true;
						}
					}
				}
			}
			return false;
		}

		/// <summary>
		/// Fills <paramref name="path"/> with nodes from start (inclusive) toward goal.
		/// Returns false if there was nowhere to go.
		/// </summary>
		public static bool FindPath(Point start, Point goal, List<Point> path) {
			path.Clear();
			if (!TryNudgeToFit(ref start, 2))
				return false;

			stamp++;
			if (stamp == int.MaxValue) {
				Array.Clear(visitStamp);
				Array.Clear(fitsStamp);
				Array.Clear(supStamp);
				stamp = 1;
			}
			originX = start.X - Radius;
			originY = start.Y - Radius;

			int startIdx = Index(start.X, start.Y);
			int head = 0, tail = 0;
			queue[tail++] = startIdx;
			visitStamp[startIdx] = stamp;
			parent[startIdx] = -1;

			int bestIdx = startIdx;
			int bestScore = Score(start.X, start.Y, goal);
			int expansions = 0;

			while (head < tail && expansions < MaxExpansions) {
				int cur = queue[head++];
				expansions++;
				int cx = cur % Size + originX;
				int cy = cur / Size + originY;

				int score = Score(cx, cy, goal);
				if (score < bestScore) {
					bestScore = score;
					bestIdx = cur;
					if (score == 0)
						break;
				}

				bool curSupported = CachedSupported(cx, cy, cur);
				foreach (Point m in Moves) {
					int nx = cx + m.X, ny = cy + m.Y;
					if (nx < originX || ny < originY || nx >= originX + Size || ny >= originY + Size)
						continue;
					int ni = Index(nx, ny);
					if (visitStamp[ni] == stamp || !CachedFits(nx, ny, ni))
						continue;
					// no cutting corners on diagonals
					if (m.X != 0 && m.Y != 0) {
						if (!CachedFits(cx + m.X, cy, Index(cx + m.X, cy)) || !CachedFits(cx, cy + m.Y, Index(cx, cy + m.Y)))
							continue;
					}
					if (!curSupported) {
						if (m.Y != 1)
							continue; // airborne: only falling
					}
					else if (m.Y < 0 && !CachedSupported(nx, ny, ni)) {
						continue; // climbing needs something to cling to
					}

					visitStamp[ni] = stamp;
					parent[ni] = cur;
					queue[tail++] = ni;
				}
			}

			for (int i = bestIdx; i != -1; i = parent[i])
				path.Add(new Point(i % Size + originX, i / Size + originY));
			path.Reverse();
			return path.Count > 1;
		}

		private static int Index(int x, int y) => (y - originY) * Size + (x - originX);

		private static int Score(int x, int y, Point goal) => Math.Abs(x - goal.X) + Math.Abs(y - goal.Y);

		private static bool CachedFits(int x, int y, int idx) {
			if (fitsStamp[idx] != stamp) {
				fitsStamp[idx] = stamp;
				fitsValue[idx] = Fits(x, y);
			}
			return fitsValue[idx];
		}

		private static bool CachedSupported(int x, int y, int idx) {
			if (supStamp[idx] != stamp) {
				supStamp[idx] = stamp;
				supValue[idx] = Supported(x, y);
			}
			return supValue[idx];
		}
	}
}
