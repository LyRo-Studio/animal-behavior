from assist.phase import Phase
from assist.camera import Camera

class OutputFilename:

    def get(testnumber: int, camera: Camera, phase: Phase):
        return f"{testnumber}_{camera}_{phase.name}.mp4"