import ffmpeg

class Slicer:
    def __init__(self, input_directory: str, output_directory: str):
        self.input_directory = input_directory
        self.output_directory = output_directory

    def slice(self, input_file: str, start_time: float, end_time: float, output_file: str):
        assert end_time > start_time
        duration = end_time - start_time

        # print(f"slicing {input_file} from {start_time}s to {end_time}s for {duration} seconds to {self.output_directory}")
        
        (
            ffmpeg
            .input(input_file, ss=start_time, t=duration)
            .filter('yadif')
            .output(
                output_file,
                r=30,
                vcodec='libx264',
                video_bitrate='32M',
                g=12,                    # Keyframe every 0.5s
                movflags='faststart',
                acodec='aac',
                audio_bitrate='128k',
                **{'map': '0:v', 'map': '0:a'}
            )
            .run(overwrite_output=True, quiet=True)
        )
