
from scipy import signal, stats
import numpy as np
import ffmpeg

class LagCorrelation:

    def correlate(self, reference_camera, other_camera):
        lag = self.__get_lag(reference_camera, other_camera, start_time_correction=True, start_delay=10, duration=120)
        return lag
    

    def __get_lag(self, C2_video_loc, C1_video_loc, start_time_correction=False, start_delay=0, duration=-1):
    
        C1_sample_rate = self.__get_audio_sample_rate(C1_video_loc)
        C2_sample_rate = self.__get_audio_sample_rate(C2_video_loc)

        assert C1_sample_rate == C2_sample_rate

        sample_rate = C1_sample_rate

        C1_audio = self.__get_audio(C1_video_loc, start_delay, duration, start_time_correction)
        C2_audio = self.__get_audio(C2_video_loc, start_delay, duration, start_time_correction)

        correlation = signal.correlate(C1_audio, C2_audio, mode="full") # {‘full’, ‘valid’, ‘same’}

        lags = signal.correlation_lags(C1_audio.size, C2_audio.size, mode="full")
        lag = lags[np.argmax((correlation))]
        
        return np.round(lag/sample_rate,3)
    
    def __get_start_time(self, file_path):
        probe = ffmpeg.probe(file_path)
        format_info = probe['format']
        start_time = float(format_info.get('start_time', 0.0))
        return start_time

    def __get_audio_sample_rate(self, file_path):
        probe = ffmpeg.probe(file_path)
        for stream in probe['streams']:
            if stream['codec_type'] == 'audio':
                sample_rate = int(stream['sample_rate'])
                return sample_rate
        raise ValueError("No audio stream found in the file")

    def __get_audio(self, video_loc, start_delay=0, duration=-1, start_time_correction=False):
        if start_time_correction:
            start_time = self.__get_start_time(video_loc)
        else:
            start_time = 0

        sample_rate = self.__get_audio_sample_rate(video_loc)

        if duration == -1:
            x  = ffmpeg.input(video_loc, ss=start_time+start_delay) \
                .output('-', format='f32le', acodec='pcm_f32le', ac=1, ar=sample_rate) \
                .run(capture_stdout=True, capture_stderr=True)
        else:
            x  = ffmpeg.input(video_loc, ss=start_time+start_delay, to=start_time+start_delay+duration) \
                .output('-', format='f32le', acodec='pcm_f32le', ac=1, ar=sample_rate) \
                .run(capture_stdout=True, capture_stderr=True)

        x = np.frombuffer(x[0], np.float32)

        return x