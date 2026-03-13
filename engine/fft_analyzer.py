import numpy as np

def analyze(audio):

    spectrum = np.fft.rfft(audio)

    magnitude = np.abs(spectrum)

    return magnitude