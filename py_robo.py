# -*- coding: utf-8 -*-
import sys
import numpy as np
import librosa
import socket
from PyQt5 import QtWidgets, QtCore
import pyqtgraph as pg

SAMPLE_RATE = 44100
BUFFER = 2048

# ==============================
# REASTREAM CONFIG
# ==============================

UDP_IP = "localhost"
UDP_PORT = 58710
BUFFER_SIZE = 8192

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)

# ==============================
# CARREGAR REFERÊNCIA
# ==============================

def carregar_referencia(path):
    audio, sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    fft = np.abs(np.fft.rfft(audio))
    fft_db = 20 * np.log10(fft + 1e-9)
    return fft_db

ref_spectrum = carregar_referencia("audio.wav")
freqs = np.fft.rfftfreq(BUFFER, 1/SAMPLE_RATE)

# ==============================
# INTERFACE PRO
# ==============================

class MixAssistantPRO(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mix Assistant PRO 3.1 - ReaStream")
        self.resize(1000, 600)
        self.setStyleSheet("background-color: #1e1e1e; color: white;")

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#1e1e1e")
        self.plot.showGrid(x=True, y=True)
        self.plot.setLogMode(x=True, y=False)
        self.plot.setLabel('left', 'dB')
        self.plot.setLabel('bottom', 'Frequency (Hz)')

        self.curva_atual = self.plot.plot(pen=pg.mkPen('#00ff88', width=2))
        self.curva_ref = self.plot.plot(pen=pg.mkPen('#ff0055', width=2))

        layout.addWidget(self.plot)

        self.lufs_label = QtWidgets.QLabel("LUFS: 0")
        layout.addWidget(self.lufs_label)

        self.alerta = QtWidgets.QLabel("")
        layout.addWidget(self.alerta)

        central.setLayout(layout)
        self.setCentralWidget(central)

    def atualizar(self, spectrum_db, lufs, alerta):
        tamanho = min(len(freqs), len(spectrum_db))
        self.curva_atual.setData(freqs[:tamanho], spectrum_db[:tamanho])
        self.curva_ref.setData(freqs[:tamanho], ref_spectrum[:tamanho])
        self.lufs_label.setText(f"LUFS aproximado: {lufs:.2f}")
        self.alerta.setText(alerta)

# ==============================
# FUNÇÕES DE ANÁLISE
# ==============================

def calcular_spectrum(audio):
    fft = np.abs(np.fft.rfft(audio, n=BUFFER))
    return 20 * np.log10(fft + 1e-9)

def calcular_lufs(audio):
    rms = np.sqrt(np.mean(audio**2))
    return 20 * np.log10(rms + 1e-9)

def detectar_conflito(audio):
    fft = np.abs(np.fft.rfft(audio))
    freqs_local = np.fft.rfftfreq(len(audio), 1/SAMPLE_RATE)
    idx = np.where((freqs_local > 50) & (freqs_local < 90))
    energia = np.mean(fft[idx])
    return energia > np.mean(fft) * 1.5

def detectar_vocal_mascarado(audio):
    fft = np.abs(np.fft.rfft(audio))
    freqs_local = np.fft.rfftfreq(len(audio), 1/SAMPLE_RATE)
    voz = np.mean(fft[(freqs_local > 2000) & (freqs_local < 4000)])
    grave = np.mean(fft[(freqs_local > 60) & (freqs_local < 120)])
    return grave > voz * 1.3

# ==============================
# RECEBER AUDIO REASTREAM
# ==============================

def receber_audio():
    try:
        data, _ = sock.recvfrom(BUFFER_SIZE)
        audio = np.frombuffer(data, dtype=np.float32)
        print("Pacote recebido:", len(data))

        if len(audio) % 2 == 0:
            audio = audio.reshape(-1, 2).mean(axis=1)

        if len(audio) >= BUFFER:
            return audio[:BUFFER]

    except:
        pass

    return None

# ==============================
# APP
# ==============================

app = QtWidgets.QApplication(sys.argv)
janela = MixAssistantPRO()

def atualizar_realtime():
    audio = receber_audio()

    if audio is None:
        return

    spectrum_db = calcular_spectrum(audio)
    lufs = calcular_lufs(audio)

    alerta = ""

    if detectar_conflito(audio):
        alerta += "Conflito grave 60-80Hz detectado\n"

    if detectar_vocal_mascarado(audio):
        alerta += "Vocal pode estar mascarado\n"

    janela.atualizar(spectrum_db, lufs, alerta)

timer = QtCore.QTimer()
timer.timeout.connect(atualizar_realtime)
timer.start(30)

janela.show()
sys.exit(app.exec_())
