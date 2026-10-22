"""Stand-in for Picoware's C Vector type (mutable x, y)."""


class Vector:
    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y
