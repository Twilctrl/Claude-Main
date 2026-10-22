"""Stand-in for Picoware's Menu (title + selectable list), rendered into the simulator's text grid."""

from picoware.system.vector import Vector


class Menu:
    def __init__(self, draw, title, y, height, text_color=0xFFFF, background_color=0x0000,
                 selected_color=0x001F, border_color=0xFFFF, border_width=2):
        self.display = draw
        self.title = title
        self.items = []
        self.selected_index = 0

    @property
    def current_item(self):
        return self.items[self.selected_index]

    @property
    def item_count(self):
        return len(self.items)

    def add_item(self, item):
        self.items.append(item)

    def clear(self):
        self.items = []
        self.selected_index = 0
        self.display.fill_screen()

    def set_selected(self, index):
        self.selected_index = max(0, min(index, len(self.items) - 1))
        self.draw()

    def scroll_up(self):
        if self.selected_index > 0:
            self.selected_index -= 1
        self.draw()

    def scroll_down(self):
        if self.selected_index < len(self.items) - 1:
            self.selected_index += 1
        self.draw()

    def draw(self):
        d = self.display
        d.fill_screen()
        d.text(Vector(0, 0), "== " + self.title + " ==")
        for i, item in enumerate(self.items):
            d.text(Vector(0, (i + 2) * d.font_size.y), ("> " if i == self.selected_index else "  ") + item)
        d.swap()
