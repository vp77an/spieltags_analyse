class RotationTracker:

    def __init__(self, lineup):

        self.rotation = lineup[:]

    def rotate(self):

        self.rotation = [
            self.rotation[5],
            self.rotation[0],
            self.rotation[1],
            self.rotation[2],
            self.rotation[3],
            self.rotation[4]
        ]

    def position(self, player):

        return self.rotation.index(player) + 1

    def current_rotation(self):

        return self.rotation.copy()

    def setter_position(self):

        if 8 in self.rotation:
            return self.position(8)

        return self.position(11)
