import pyloudnorm as pyln

meter = pyln.Meter(44100)

def get_lufs(audio):

    loudness = meter.integrated_loudness(audio)

    return loudness