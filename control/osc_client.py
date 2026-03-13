import os

from pythonosc import udp_client

from config_manager import load_config


DEFAULT_OSC_HOST = "127.0.0.1"
DEFAULT_OSC_PORT = 8000

DEBUG = os.environ.get("MIX_ROBO_DEBUG", "0") != "0"
_client = None
_client_target = None


def _set_client(host, port, verbose=False):
    global _client, _client_target
    target = (str(host).strip() or DEFAULT_OSC_HOST, int(port))
    if _client is not None and _client_target == target:
        return

    _client = udp_client.SimpleUDPClient(target[0], target[1])
    _client_target = target
    if DEBUG or verbose:
        print(f"[OSC CONFIG] target={target[0]}:{target[1]}")


def configure_from_config(config=None, verbose=False):
    if config is None:
        config = load_config()

    run = config.get("run_settings", {})
    host = run.get("osc_host", DEFAULT_OSC_HOST)
    port = run.get("osc_port", DEFAULT_OSC_PORT)
    _set_client(host, port, verbose=verbose)


def set_volume(track, value, verbose=False):
    if _client is None:
        configure_from_config(verbose=verbose)

    message = f"/track/{track}/volume"
    if DEBUG or verbose:
        print(f"[OSC SEND] {message} = {value:.3f}")
    _client.send_message(message, value)