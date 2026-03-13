import sounddevice as sd

samplerate = 44100
blocksize = 2048

def start_stream(callback):

    stream = sd.InputStream(
        channels=2,
        samplerate=samplerate,
        blocksize=blocksize,
        callback=callback
    )

    stream.start()