
import matplotlib.pyplot as plt
import io
from PIL import Image
import ffmpeg

class FrameGrabber:

    def extract_frames(self, file1: str, file2: str, test_id: str, desynchronisation_delay: float):
        print(f"   Extracting frames for test {test_id}...")

        camera1_start_time = self.__get_start_time(file1)
        camera2_start_time = self.__get_start_time(file2) + desynchronisation_delay

        print(f"      Camera 1: {file1} {camera1_start_time}")
        print(f"      Camera 2: {file2} {camera2_start_time}")

        camera1_frame = self.__extract_frame(file1, camera1_start_time )
        camera2_frame = self.__extract_frame(file2, camera2_start_time )

        camera1_image = self.__load_image_from_bytes(camera1_frame)
        camera2_image = self.__load_image_from_bytes(camera2_frame)

        self.__show_images(camera1_image, camera2_image, test_id, desynchronisation_delay)


    def get_start_time(self, file_path):
        probe = ffmpeg.probe(file_path)
        format_info = probe['format']
        start_time = float(format_info.get('start_time', 0.0))
        return start_time

    def __extract_frame(self, video_path, timestamp):
        # Extract frame from video as raw data
        out, _ = (
            ffmpeg
            .input(video_path, ss=timestamp)
            .output('pipe:', vframes=1, format='image2', vcodec='mjpeg')
            .run(capture_stdout=True, capture_stderr=True)
        )
        return out

    def __load_image_from_bytes(self, image_bytes):
        # Load image from raw bytes
        return Image.open(io.BytesIO(image_bytes))
    
    def __show_images(self, frame1, frame2, test_id, desynchronisation_delay):
        print(f"Test ID: {test_id}; Lag: {desynchronisation_delay} seconds")
        
        fig, axes = plt.subplots(1, 2, figsize=(10, 5))
        axes[0].imshow(frame1)
        axes[0].set_title('Frame from Video 1')
        axes[0].axis('off')
        
        axes[1].imshow(frame2)
        axes[1].set_title('Frame from Video 2')
        axes[1].axis('off')
        
        plt.tight_layout()
        plt.show()