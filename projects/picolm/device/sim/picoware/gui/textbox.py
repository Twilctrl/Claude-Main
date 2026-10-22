"""Stand-in for Picoware's TextBox: keeps the text; the simulator prints it."""


class TextBox:
    def __init__(self, draw, y, height, foreground_color=0xFFFF, background_color=0x0000,
                 show_scrollbar=True, show_cursor=False):
        self.text = ""

    def set_text(self, text):
        self.text = text

    def jump_to_bottom(self):
        pass

    def scroll_up(self):
        pass

    def scroll_down(self):
        pass
